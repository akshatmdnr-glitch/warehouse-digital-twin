#!/usr/bin/env python3
"""Demo visualization — planned paths, pickup/dropoff markers, robot goal labels.

A lightweight observer for the concurrent two-robot demo. It reads existing
ROS topics only (never writes to navigation, fleet, task or package topics)
and renders in the Gazebo world:

  * the actual planned path per robot as a thick colored floor ribbon
    (robot1 = cyan, robot2 = orange), rebuilt on replan, removed on task end
  * a green floor square + floating "PICKUP" text at each task's pickup,
    drawn when the task is created, removed once the robot reaches PICKING
  * a blue floor square + floating "DROPOFF" text at the dropoff, drawn at
    PICKING, removed once the package is delivered (COMPLETED/IDLE)
  * a floating goal label above each robot: "Robot1 → PICKUP" or "→ DROPOFF"
    that follows the robot's live amcl pose

Package spawn / carry / attach is left exactly to package_carrier_node — this
node never creates or moves packages.
"""

import json
import math
import threading
import time

import gz.transport13 as gztr
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from gz.msgs10 import boolean_pb2, entity_factory_pb2, entity_pb2, pose_pb2
from nav_msgs.msg import Path
from rclpy.node import Node
from std_msgs.msg import String

from warehouse_bringup import viz_sdf

_PATH_WIDTH = 0.22  # thick line on the floor
_PICKUP_COLOR = (0.05, 0.95, 0.35)
_DROPOFF_COLOR = (0.15, 0.55, 1.0)
# robot_id -> path / label colour
_ROBOT_COLORS = {
    "robot1": (0.0, 1.0, 1.0),   # cyan
    "robot2": (1.0, 0.55, 0.1),  # orange
}


def _euler_to_quat(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def _look_at(eye, target):
    """Return a quaternion that makes +X face `target` (gz camera convention)."""
    fx = target[0] - eye[0]
    fy = target[1] - eye[1]
    fz = target[2] - eye[2]
    d = math.hypot(fx, fy)
    if d < 1e-6:
        yaw = 0.0
        pitch = math.pi / 2 if fz < 0 else -math.pi / 2
    else:
        yaw = math.atan2(fy, fx)
        pitch = -math.atan2(fz, d)
    return _euler_to_quat(0.0, pitch, yaw)


class GzClient:
    """Thin wrapper over the gz transport for the running sim server."""

    def __init__(self, world="warehouse_world"):
        self._world = world
        self._lock = threading.Lock()
        self._node = gztr.Node()
        self._camera = self._node.advertise("/gui/camera/pose", pose_pb2.Pose)

    def camera(self, eye, target):
        m = pose_pb2.Pose()
        m.position.x = eye[0]
        m.position.y = eye[1]
        m.position.z = eye[2]
        qx, qy, qz, qw = _look_at(eye, target)
        m.orientation.x = qx
        m.orientation.y = qy
        m.orientation.z = qz
        m.orientation.w = qw
        try:
            with self._lock:
                return self._camera.publish(m)
        except Exception:  # noqa: BLE001 - transport hiccups are retried
            return False

    def _req(self, service, msg, req_type, rep_type, timeout=4000):
        with self._lock:
            try:
                return self._node.request(
                    f"/world/{self._world}/{service}", msg, req_type, rep_type, timeout
                )
            except Exception:  # noqa: BLE001 - transport hiccups are retried
                return None

    def create(self, name, sdf, x=0.0, y=0.0, z=0.0, yaw=0.0):
        req = entity_factory_pb2.EntityFactory()
        req.name = name
        req.sdf = sdf
        req.pose.position.x = x
        req.pose.position.y = y
        req.pose.position.z = z
        qx, qy, qz, qw = _euler_to_quat(0.0, 0.0, yaw)
        req.pose.orientation.x = qx
        req.pose.orientation.y = qy
        req.pose.orientation.z = qz
        req.pose.orientation.w = qw
        rep = self._req(
            "create", req, entity_factory_pb2.EntityFactory, boolean_pb2.Boolean
        )
        return bool(rep and rep[0])

    def set_pose(self, name, x, y, z, yaw=0.0):
        req = pose_pb2.Pose()
        req.name = name
        req.position.x = x
        req.position.y = y
        req.position.z = z
        qx, qy, qz, qw = _euler_to_quat(0.0, 0.0, yaw)
        req.orientation.x = qx
        req.orientation.y = qy
        req.orientation.z = qz
        req.orientation.w = qw
        return self._req("set_pose", req, pose_pb2.Pose, boolean_pb2.Boolean)

    def remove(self, name):
        req = entity_pb2.Entity()
        req.name = name
        req.type = entity_pb2.Entity.Type.Value("MODEL")
        return self._req("remove/blocking", req, entity_pb2.Entity, boolean_pb2.Boolean)


class DemoVisualizationNode(Node):
    def __init__(self):
        super().__init__("demo_visualization")
        self.declare_parameter("world", "warehouse_world")
        self.declare_parameter("robots", "robot1,robot2")

        self._world = self.get_parameter("world").value
        self._robots_list = [
            r.strip() for r in self.get_parameter("robots").value.split(",") if r.strip()
        ]

        self._gz = GzClient(self._world)
        self._tasks = {}  # task_id -> {pickup, dropoff, robot}
        self._robot_state = {}  # rid -> latest task_state
        self._robot_pose = {}  # rid -> (x, y, yaw)
        self._plans = {}  # rid -> [(x, y), ...]
        self._path_key = {}  # rid -> signature
        self._path_ent = {}  # rid -> entity name
        self._goal_ent = {}  # rid -> entity name
        self._goal_text = {}  # rid -> current label text
        self._pickup_ents = {}  # task_id -> [entity names]
        self._drop_ents = {}  # task_id -> [entity names]
        self._picking_done = set()  # task_ids whose pickup marker was removed

        # ---- global task feed (fleet dispatcher) ----
        self.create_subscription(String, "/add_task", self._on_add_task, 10)
        self.create_subscription(String, "/task_assignment", self._on_assignment, 10)

        # ---- per-robot observers ----
        for rid in self._robots_list:
            self._robot_state[rid] = {"state": "IDLE", "task": None}
            self.create_subscription(
                String, f"/{rid}/task_state", self._mk_state_cb(rid), 10
            )
            self.create_subscription(
                Path, f"/{rid}/plan", self._mk_plan_cb(rid), 10
            )
            self.create_subscription(
                PoseWithCovarianceStamped,
                f"/{rid}/amcl_pose",
                self._mk_amcl_cb(rid),
                10,
            )

        self._tick = self.create_timer(0.2, self._update)
        self.get_logger().info(
            f"Demo visualization ready (world={self._world}, robots={self._robots_list})"
        )

    # ------------------------------------------------------------------
    # ROS callbacks
    # ------------------------------------------------------------------
    def _on_add_task(self, msg):
        parts = [p.strip() for p in msg.data.strip().split(",")]
        if len(parts) < 5:
            return
        try:
            tid = parts[0]
            px, py = float(parts[1]), float(parts[2])
            dx, dy = float(parts[3]), float(parts[4])
        except ValueError:
            return
        self._register_task(tid, (px, py), (dx, dy))

    def _on_assignment(self, msg):
        parts = [p.strip() for p in msg.data.strip().split(",")]
        if len(parts) < 6:
            return
        try:
            robot = parts[0]
            tid = parts[1]
            px, py = float(parts[2]), float(parts[3])
            dx, dy = float(parts[4]), float(parts[5])
        except ValueError:
            return
        task = self._register_task(tid, (px, py), (dx, dy))
        if task:
            task["robot"] = robot
        self._spawn_pickup_marker(tid)

    def _register_task(self, tid, pickup, dropoff):
        if tid not in self._tasks:
            self._tasks[tid] = {
                "pickup": pickup,
                "dropoff": dropoff,
                "robot": None,
            }
            self.get_logger().info(
                f"TASK {tid}: pickup=({pickup[0]:.1f},{pickup[1]:.1f}) "
                f"dropoff=({dropoff[0]:.1f},{dropoff[1]:.1f})"
            )
        self._spawn_pickup_marker(tid)
        return self._tasks[tid]

    def _mk_state_cb(self, rid):
        def cb(msg):
            try:
                data = json.loads(msg.data)
            except (ValueError, TypeError):
                return
            state = data.get("state", "IDLE")
            task_id = data.get("active_task")
            prev = self._robot_state[rid]["state"]
            self._robot_state[rid] = {"state": state, "task": task_id}
            self._on_state_change(rid, state, task_id, prev)

        return cb

    def _mk_plan_cb(self, rid):
        def cb(msg):
            pts = [(p.pose.position.x, p.pose.position.y) for p in msg.poses]
            self._plans[rid] = pts
            self._maybe_update_path(rid)

        return cb

    def _mk_amcl_cb(self, rid):
        def cb(msg):
            p = msg.pose.pose
            yaw = math.atan2(
                2.0 * (p.orientation.w * p.orientation.z + p.orientation.x * p.orientation.y),
                1.0 - 2.0 * (p.orientation.y * p.orientation.y + p.orientation.z * p.orientation.z),
            )
            self._robot_pose[rid] = (p.position.x, p.position.y, yaw)

        return cb

    # ------------------------------------------------------------------
    # Marker lifecycle
    # ------------------------------------------------------------------
    def _spawn_pickup_marker(self, tid):
        task = self._tasks.get(tid)
        if task is None or tid in self._picking_done:
            return
        if tid in self._pickup_ents:
            return
        px, py = task["pickup"]
        square = f"demo_pickup_{tid}"
        self._gz.create(square, viz_sdf.floor_square_model(square, _PICKUP_COLOR), px, py, 0.0)
        tex = viz_sdf.make_text_texture("PICKUP", f"pickup_{tid}", fg=(120, 255, 150))
        text_ent = f"demo_pickup_txt_{tid}"
        self._gz.create(
            text_ent,
            viz_sdf.text_model(text_ent, tex[0], tex[1], tex[2]),
            px, py, 1.6,
        )
        self._pickup_ents[tid] = [square, text_ent]

    def _remove_pickup_marker(self, tid):
        for ent in self._pickup_ents.pop(tid, []):
            self._gz.remove(ent)
        self._picking_done.add(tid)

    def _spawn_dropoff_marker(self, tid):
        task = self._tasks.get(tid)
        if task is None or tid in self._drop_ents:
            return
        dx, dy = task["dropoff"]
        square = f"demo_drop_{tid}"
        self._gz.create(square, viz_sdf.floor_square_model(square, _DROPOFF_COLOR), dx, dy, 0.0)
        tex = viz_sdf.make_text_texture("DROPOFF", f"drop_{tid}", fg=(140, 190, 255))
        text_ent = f"demo_drop_txt_{tid}"
        self._gz.create(
            text_ent,
            viz_sdf.text_model(text_ent, tex[0], tex[1], tex[2]),
            dx, dy, 1.6,
        )
        self._drop_ents[tid] = [square, text_ent]

    def _remove_dropoff_marker(self, tid):
        for ent in self._drop_ents.pop(tid, []):
            self._gz.remove(ent)

    def _on_state_change(self, rid, state, task_id, prev):
        tid = task_id
        if state == "PICKING":
            # Pickup reached -> clear the pickup marker, reveal the dropoff.
            if tid:
                self._remove_pickup_marker(tid)
                self._spawn_dropoff_marker(tid)
        elif state in ("COMPLETED", "IDLE"):
            # Task finished -> remove dropoff marker + planned path.
            if tid:
                self._remove_dropoff_marker(tid)
            self._clear_path(rid)
        self._maybe_update_goal_label(rid)

    # ------------------------------------------------------------------
    # Planned path (thick colored ribbon, rebuilt on replan)
    # ------------------------------------------------------------------
    def _maybe_update_path(self, rid):
        pts = self._plans.get(rid)
        if not pts or len(pts) < 2:
            return
        key = (
            round(pts[0][0], 2),
            round(pts[0][1], 2),
            round(pts[-1][0], 2),
            round(pts[-1][1], 2),
            len(pts) // 8,
        )
        if self._path_key.get(rid) == key:
            return
        self._path_key[rid] = key
        sample = pts[:: max(1, len(pts) // 40)]
        color = _ROBOT_COLORS.get(rid, (1, 1, 1))
        ent = f"demo_path_{rid}"
        sdf = viz_sdf.path_model(ent, color, sample, width=_PATH_WIDTH)
        if not sdf:
            return
        if self._path_ent.get(rid):
            self._gz.remove(self._path_ent[rid])
        self._gz.create(ent, sdf, 0, 0, 0)
        self._path_ent[rid] = ent

    def _clear_path(self, rid):
        ent = self._path_ent.pop(rid, None)
        if ent:
            self._gz.remove(ent)
        self._path_key.pop(rid, None)
        self._plans.pop(rid, None)

    # ------------------------------------------------------------------
    # Robot goal label (follows the robot)
    # ------------------------------------------------------------------
    def _maybe_update_goal_label(self, rid):
        st = self._robot_state.get(rid, {"state": "IDLE", "task": None})
        state = st["state"]
        label = {
            "GO_TO_PICKUP": "PICKUP",
            "PICKING": "PICKUP",
            "GO_TO_DROPOFF": "DROPOFF",
            "DROPPING": "DROPOFF",
        }.get(state, "IDLE")
        name = rid.replace("robot", "Robot ")
        text = f"{name}\n→ {label}"
        if self._goal_text.get(rid) == text:
            return
        self._goal_text[rid] = text
        tex = viz_sdf.make_text_texture(text, f"goal_{rid}", fg=(255, 230, 120))
        ent = f"demo_goal_{rid}"
        sdf = viz_sdf.text_model(ent, tex[0], tex[1], tex[2])
        old = self._goal_ent.get(rid)
        if old:
            self._gz.remove(old)
        self._goal_ent[rid] = ent
        pose = self._robot_pose.get(rid)
        if pose:
            self._gz.create(ent, sdf, pose[0], pose[1], 2.4)
        else:
            self._gz.create(ent, sdf, 0, 0, 2.4)

    # ------------------------------------------------------------------
    # Per-tick: keep labels following the robots, camera follows the busy one
    # ------------------------------------------------------------------
    def _update(self):
        for rid in self._robots_list:
            pose = self._robot_pose.get(rid)
            ent = self._goal_ent.get(rid)
            if pose and ent:
                self._gz.set_pose(ent, pose[0], pose[1], 2.4)
        self._frame_fleet()

    def _frame_fleet(self):
        """Keep every robot on screen.

        The default camera (8,-8,13) sits behind the 2 m racks, so a robot
        working in the south aisle is occluded by the rack geometry while its
        floating label stays visible — which reads as "the robot disappeared".
        Instead of chasing a single active robot (which pushes the other one
        off-screen), frame the whole fleet from a high vantage so every robot
        body stays visible regardless of which is working. The camera only
        observes; it never moves or replaces any robot model.
        """
        known = [
            self._robot_pose[r] for r in self._robots_list
            if r in self._robot_pose and self._robot_pose[r] is not None
        ]
        if not known:
            return
        cx = sum(p[0] for p in known) / len(known)
        cy = sum(p[1] for p in known) / len(known)
        spread = max(
            (math.hypot(p[0] - cx, p[1] - cy) for p in known), default=0.0
        )
        # Distance scaled to the fleet spread; high elevation avoids rack
        # occlusion and keeps a floor-level overview of the whole warehouse.
        dist = max(10.0, spread * 3.0)
        self._gz.camera((cx, cy - dist, dist * 0.9), (cx, cy, 0))


def main():
    rclpy.init()
    node = DemoVisualizationNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
