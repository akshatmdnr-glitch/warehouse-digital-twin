#!/usr/bin/env python3
# pylint: disable=no-member  # gz.msgs10 protobuf classes are generated at runtime
"""Warehouse order-fulfillment visualization for Gazebo.

Watches the existing ROS topics (fleet status, task assignments, per-robot
task state / plans / poses) and renders the whole workflow inside the 3D
world by spawning and moving visual-only entities through the gz transport:

  * package inventory on the racks (one box per package)
  * green pickup highlight + "PICKUP <id>" label on the selected package
  * blue dropoff highlight + "DROP" label at the destination
  * per-robot glow column + floating status label (state / task id)
  * planned path ribbon on the floor
  * the package visually lifts off the rack, follows the robot, and is
    delivered at the destination ("DELIVERED" flash)
  * transient floating events (ORDER CREATED, ROBOT ASSIGNED, ...)
  * cinematic camera presets + optional auto-follow during tasks

It observes only; it never writes to fleet / task / navigation topics.
"""

import json
import math
import threading
import time

import gz.transport13 as gztr
import rclpy
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from gz.msgs10 import boolean_pb2, entity_factory_pb2, entity_pb2, pose_pb2
from nav_msgs.msg import Path
from rclpy.node import Node
from std_msgs.msg import String

from warehouse_bringup import viz_sdf
from warehouse_bringup.order_fulfillment import (
    DELIVERY_Z,
    PACKAGE_LIFT,
    PACKAGE_SIZE,
    ROBOT_COLORS,
    build_inventory,
    task_to_package,
)

_ACTIVE_STATES = {"GO_TO_PICKUP", "PICKING", "GO_TO_DROPOFF", "DROPPING"}
_LABEL_STATES = {
    "GO_TO_PICKUP": "MOVING TO PICKUP",
    "PICKING": "PICKING",
    "GO_TO_DROPOFF": "DELIVERING",
    "DROPPING": "DELIVERING",
    "COMPLETED": "DONE",
    "IDLE": "IDLE",
    "WAITING": "IDLE",
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
        # blocking variant + explicit type make removal reliable
        return self._req("remove/blocking", req, entity_pb2.Entity, boolean_pb2.Boolean)

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
        except Exception:  # noqa: BLE001
            return False


class VisualizationNode(Node):
    def __init__(self):
        super().__init__("warehouse_visualization")
        self.declare_parameter("world", "warehouse_world")
        self.declare_parameter("auto_follow", True)
        self.declare_parameter("show_paths", True)
        self.declare_parameter("packages_per_shelf", 2)

        self._world = self.get_parameter("world").value
        self._auto_follow = self.get_parameter("auto_follow").value
        self._show_paths = self.get_parameter("show_paths").value

        self._gz = GzClient(self._world)
        self._inventory = build_inventory()
        self._tasks = {}  # task_id -> order_fulfillment.Task
        self._robots = {}  # robot_id -> dict(state)
        self._robot_subs = {}  # robot_id -> list of subscriptions
        self._ready = False

        # spawned entity names per robot / task
        self._glow_ent = {}  # robot_id -> entity name
        self._label_ent = {}  # robot_id -> entity name
        self._label_text = {}  # robot_id -> current label text
        self._path_ent = {}  # robot_id -> entity name
        self._path_key = {}  # robot_id -> plan signature
        self._carry = {}  # robot_id -> package_id
        self._event_ents = {}  # entity name -> expiry
        self._highlights = set()  # spawned highlight entity names
        self._delivered = set()  # task_ids whose package was delivered
        self._seen_active = set()  # task_ids observed in an active state
        self._return_until = {}  # robot_id -> monotonic time showing RETURNING
        self._last_task = {}  # robot_id -> most recent task_id
        self._active_robot = None
        self._active_task = None

        self._camera_mode = "auto"  # auto | overview | follow:<id> | pickup | delivery
        self._camera_shot = None  # last computed (eye, target)
        self._camera_started = False

        # ---- subscriptions (observers only) ----
        self.create_subscription(String, "/fleet_status", self._on_fleet, 10)
        self.create_subscription(String, "/add_task", self._on_add_task, 10)
        self.create_subscription(String, "/task_assignment", self._on_assignment, 10)
        self.create_subscription(String, "/cancel_task", self._on_cancel, 10)
        self.create_subscription(String, "/viz/camera_cmd", self._on_camera_cmd, 10)

        self._tick = self.create_timer(0.2, self._update)
        self._cleanup_timer = self.create_timer(1.0, self._expire_events)

        # spawn the inventory once the world is available
        threading.Thread(target=self._bootstrap, daemon=True).start()
        self.get_logger().info(
            f"Warehouse visualization ready (world={self._world}, "
            f"packages={len(self._inventory.packages)})"
        )

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    def _bootstrap(self):
        remaining = set(p.package_id for p in self._inventory.packages)
        for _ in range(40):
            for pkg in self._inventory.packages:
                if pkg.package_id not in remaining:
                    continue
                if self._spawn_package(pkg):
                    remaining.discard(pkg.package_id)
            if not remaining:
                break
            time.sleep(0.5)
        if remaining:
            self.get_logger().warn(
                f"Package inventory partial: missing {sorted(remaining)}"
            )
        self._ready = True
        self.get_logger().info(
            f"Package inventory spawned "
            f"({len(self._inventory.packages) - len(remaining)}/"
            f"{len(self._inventory.packages)})"
        )

    def _spawn_package(self, pkg):
        name = f"viz_pkg_{pkg.package_id}"
        sdf = viz_sdf.package_model(pkg.package_id, pkg.color)
        z = pkg.z - PACKAGE_SIZE / 2.0
        return bool(self._gz.create(name, sdf, pkg.x, pkg.y, z))

    # ------------------------------------------------------------------
    # ROS callbacks
    # ------------------------------------------------------------------
    def _on_fleet(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        for r in data.get("robots", []):
            rid = r.get("robot_id")
            if not rid:
                continue
            ns = (r.get("namespace") or "").strip("/")
            if rid not in self._robots:
                self._robots[rid] = {
                    "ns": ns,
                    "pose": None,
                    "state": "IDLE",
                    "task": None,
                    "plan": None,
                    "goal": None,
                }
                self._make_robot_subscriptions(rid, ns)
                self._spawn_robot_markers(rid)

    def _make_robot_subscriptions(self, rid, ns):
        if rid in self._robot_subs:
            return
        subs = []
        subs.append(
            self.create_subscription(
                String, f"/{ns}/task_state", self._mk_task_state_cb(rid), 10
            )
        )
        subs.append(
            self.create_subscription(Path, f"/{ns}/plan", self._mk_plan_cb(rid), 10)
        )
        subs.append(
            self.create_subscription(
                PoseStamped, f"/{ns}/goal_pose", self._mk_goal_cb(rid), 10
            )
        )
        subs.append(
            self.create_subscription(
                PoseWithCovarianceStamped,
                f"/{ns}/amcl_pose",
                self._mk_amcl_cb(rid),
                10,
            )
        )
        self._robot_subs[rid] = subs

    def _mk_amcl_cb(self, rid):
        """Single authoritative pose: /ns/amcl_pose (sim_localization).

        The visualization never stores or estimates its own robot position —
        it renders exactly the latest pose published on this topic.
        """

        def cb(msg):
            p = msg.pose.pose
            yaw = math.atan2(
                2.0 * (p.orientation.w * p.orientation.z + p.orientation.x * p.orientation.y),
                1.0 - 2.0 * (p.orientation.y * p.orientation.y + p.orientation.z * p.orientation.z),
            )
            self._robots.setdefault(rid, {})["pose"] = (
                p.position.x,
                p.position.y,
                yaw,
            )

        return cb

    def _mk_task_state_cb(self, rid):
        def cb(msg):
            try:
                data = json.loads(msg.data)
            except (ValueError, TypeError):
                return
            state = data.get("state", "IDLE")
            task = data.get("active_task")
            prev = self._robots.get(rid, {}).get("state")
            self._robots.setdefault(rid, {})["state"] = state
            self._robots[rid]["task"] = task
            if task:
                self._robots[rid]["task"] = task
            self._on_state_change(rid, state, task, prev)

        return cb

    def _mk_plan_cb(self, rid):
        def cb(msg):
            pts = [(p.pose.position.x, p.pose.position.y) for p in msg.poses]
            self._robots.setdefault(rid, {})["plan"] = pts
            self._maybe_update_path(rid)

        return cb

    def _mk_goal_cb(self, rid):
        def cb(msg):
            self._robots.setdefault(rid, {})["goal"] = (
                msg.pose.position.x,
                msg.pose.position.y,
            )

        return cb

    def _on_add_task(self, msg):
        parts = msg.data.strip().split(",")
        if len(parts) < 5:
            return
        try:
            tid = parts[0].strip()
            px, py = float(parts[1]), float(parts[2])
            dx, dy = float(parts[3]), float(parts[4])
        except ValueError:
            return
        if tid in self._tasks:
            return
        task = task_to_package(self._inventory, (px, py), (dx, dy), tid)
        self._tasks[tid] = task
        self.get_logger().info(
            f"ORDER {tid}: package={task.package.package_id if task.package else None} "
            f"pickup=({px:.1f},{py:.1f}) dropoff=({dx:.1f},{dy:.1f})"
        )
        self._spawn_pickup_highlight(task)
        self._event(f"ORDER CREATED  {tid}", px, py - 2.0)

    def _on_assignment(self, msg):
        parts = msg.data.strip().split(",")
        if len(parts) < 6:
            return
        try:
            robot = parts[0].strip()
            tid = parts[1].strip()
            px, py = float(parts[2]), float(parts[3])
            dx, dy = float(parts[4]), float(parts[5])
        except ValueError:
            return
        task = self._tasks.get(tid)
        if task is None:
            task = task_to_package(self._inventory, (px, py), (dx, dy), tid)
            self._tasks[tid] = task
        task.robot = robot
        task.state = "ASSIGNED"
        self.get_logger().info(
            f"ASSIGN {tid} -> {robot} (package={task.package.package_id if task.package else None})"
        )
        self._spawn_pickup_highlight(task)
        self._spawn_dropoff_highlight(task)
        if task.package is not None:
            self._spawn_package_glow(task.package)
        if robot in self._robots and self._robots[robot].get("pose"):
            self._event(f"ROBOT ASSIGNED  {robot}", *self._robots[robot]["pose"][:2])

    def _on_cancel(self, msg):
        parts = msg.data.strip().split(",")
        tid = parts[-1].strip()
        task = self._tasks.pop(tid, None)
        if task is None:
            return
        if task.package is not None and task.package.status == "reserved":
            task.package.status = "free"
        self._clear_highlights(tid)
        self.get_logger().info(f"CANCEL {tid}")

    def _on_camera_cmd(self, msg):
        cmd = msg.data.strip().lower()
        if cmd in ("auto", "overview", "pickup", "delivery"):
            self._camera_mode = cmd
        elif cmd.startswith("follow:"):
            self._camera_mode = cmd
        else:
            return
        self.get_logger().info(f"Camera mode -> {self._camera_mode}")
        self._apply_camera(force=True)

    # ------------------------------------------------------------------
    # State machine (per-robot task transitions -> animations)
    # ------------------------------------------------------------------
    def _on_state_change(self, rid, state, task_id, prev):
        if task_id:
            self._last_task[rid] = task_id
        tid = task_id or self._last_task.get(rid)
        task = self._tasks.get(tid) if tid else None

        if state == "PICKING" and prev != "PICKING":
            pkg = task.package if task else None
            if pkg is not None and pkg.status in ("reserved",):
                self._carry[rid] = pkg.package_id
                pkg.status = "carried"
                self._event(
                    f"PACKAGE {pkg.package_id} PICKED",
                    *self._robots[rid].get("pose", (0, 0))[:2],
                )
                self.get_logger().info(f"{rid}: PACKAGE {pkg.package_id} PICKED")
        elif state in ("GO_TO_DROPOFF", "DROPPING") and task:
            if self._carry.get(rid) is None and task.package:
                self._carry[rid] = task.package.package_id
            if state == "DROPPING" and not self._is_delivered(task):
                self._deliver(task)
        elif state in ("COMPLETED", "IDLE"):
            pkg_id = self._carry.pop(rid, None)
            # If we missed the PICKING/DROPPING messages, fall back to any
            # assigned task for this robot that was observed active.
            if task is None or self._is_delivered(task):
                for t in self._tasks.values():
                    if (
                        t.robot == rid
                        and not self._is_delivered(t)
                        and t.task_id in self._seen_active
                    ):
                        task = t
                        break
            if task and not self._is_delivered(task):
                if pkg_id or state == "COMPLETED" or task.task_id in self._seen_active:
                    self._deliver(task)
            self._clear_highlights(task.task_id if task else tid)
            if task is not None and self._is_delivered(task):
                self._return_until[rid] = time.monotonic() + 30.0

        if state in _ACTIVE_STATES:
            self._active_robot = rid
            self._return_until.pop(rid, None)
            if task_id:
                self._seen_active.add(task_id)

        self._maybe_update_label(rid)

    # ------------------------------------------------------------------
    # Spawning / moving visuals
    # ------------------------------------------------------------------
    def _spawn_robot_markers(self, rid):
        color = ROBOT_COLORS.get(rid, (1, 1, 1))
        glow = f"viz_glow_{rid}"
        self._gz.create(glow, viz_sdf.robot_glow_model(glow, color), 0, 0, 0)
        self._glow_ent[rid] = glow
        self._maybe_update_label(rid)

    def _maybe_update_label(self, rid):
        if rid not in self._robots:
            return
        robot = self._robots[rid]
        pose = robot.get("pose")
        task_id = robot.get("task")
        state = robot.get("state", "IDLE")
        label_state = _LABEL_STATES.get(state, state)
        if state == "IDLE" and time.monotonic() < self._return_until.get(rid, 0.0):
            label_state = "RETURNING"
        task = self._tasks.get(task_id) if task_id else None
        pkg = task.package.package_id if task and task.package else ""
        name = rid.replace("robot", "Robot ")
        text = f"{name}\n{label_state}"
        if task_id and task:
            text += f"\nTask {task_id}" + (f"  {pkg}" if pkg else "")

        name_ent = f"viz_label_{rid}"
        if self._label_text.get(rid) == text:
            if pose and name_ent in self._label_ent:
                x, y, _ = pose
                self._gz.set_pose(name_ent, x, y, 2.6)
            return
        self._label_text[rid] = text
        tex = viz_sdf.make_text_texture(text, f"label_{rid}")
        sdf = viz_sdf.text_model(name_ent, tex[0], tex[1], tex[2])
        old = self._label_ent.get(rid)
        if old:
            self._gz.remove(old)
        self._label_ent[rid] = name_ent
        if pose:
            x, y, _ = pose
            self._gz.create(name_ent, sdf, x, y, 2.6)
        else:
            self._gz.create(name_ent, sdf, 0, 0, 2.6)

    def _spawn_pickup_highlight(self, task):
        name = f"viz_pickup_{task.task_id}"
        if name in self._highlights:
            return
        sdf = viz_sdf.pickup_highlight_model(name)
        px, py = task.pickup
        self._gz.create(name, sdf, px, py, 0.0)
        self._highlights.add(name)
        if task.package is not None:
            tex = viz_sdf.make_text_texture(
                f"PICKUP  {task.package.package_id}",
                f"pickup_{task.task_id}",
                fg=(120, 255, 150),
            )
            text_ent = f"viz_pickup_text_{task.task_id}"
            self._gz.create(
                text_ent,
                viz_sdf.text_model(text_ent, tex[0], tex[1], tex[2]),
                task.package.x,
                task.package.y,
                task.package.z + 1.6,
            )
            self._highlights.add(text_ent)

    def _spawn_package_glow(self, pkg):
        name = f"viz_pkgglow_{pkg.package_id}"
        if name in self._highlights:
            return
        sdf = viz_sdf.sphere_model(
            name, 0.45, (0.05, 0.95, 0.35), emissive=(0.0, 0.9, 0.3), ambient=2.0
        )
        self._gz.create(name, sdf, pkg.x, pkg.y, pkg.z)
        self._highlights.add(name)

    def _spawn_dropoff_highlight(self, task):
        name = f"viz_drop_{task.task_id}"
        if name in self._highlights:
            return
        sdf = viz_sdf.dropoff_highlight_model(name)
        dx, dy = task.dropoff
        self._gz.create(name, sdf, dx, dy, 0.0)
        self._highlights.add(name)
        tex = viz_sdf.make_text_texture(
            "DROP HERE", f"drop_{task.task_id}", fg=(140, 190, 255)
        )
        text_ent = f"viz_drop_text_{task.task_id}"
        self._gz.create(
            text_ent, viz_sdf.text_model(text_ent, tex[0], tex[1], tex[2]), dx, dy, 5.6
        )
        self._highlights.add(text_ent)

    def _maybe_update_path(self, rid):
        if not self._show_paths:
            return
        robot = self._robots.get(rid, {})
        pts = robot.get("plan")
        if not pts or len(pts) < 2:
            return
        # signature: endpoints + coarse sample so we don't respawn every replan
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
        sample = pts[:: max(1, len(pts) // 24)]
        color = ROBOT_COLORS.get(rid, (1, 1, 1))
        ent = f"viz_path_{rid}"
        sdf = viz_sdf.path_model(ent, color, sample)
        if not sdf:
            return
        if self._path_ent.get(rid):
            self._gz.remove(self._path_ent[rid])
        self._gz.create(ent, sdf, 0, 0, 0)
        self._path_ent[rid] = ent

    def _clear_highlights(self, task_id):
        for name in (
            f"viz_pickup_{task_id}",
            f"viz_pickup_text_{task_id}",
            f"viz_drop_{task_id}",
            f"viz_drop_text_{task_id}",
        ):
            self._gz.remove(name)
            self._highlights.discard(name)
        # remove package glow for the package this task used
        task = self._tasks.get(task_id)
        if task and task.package:
            glow = f"viz_pkgglow_{task.package.package_id}"
            self._gz.remove(glow)
            self._highlights.discard(glow)

    def _is_delivered(self, task):
        return task.task_id in self._delivered

    def _deliver(self, task):
        if self._is_delivered(task):
            return
        self._delivered.add(task.task_id)
        pkg = task.package
        if pkg is None:
            return
        dx, dy = task.dropoff
        self._gz.set_pose(
            f"viz_pkg_{pkg.package_id}", dx, dy, DELIVERY_Z - PACKAGE_SIZE / 2.0
        )
        pkg.status = "delivered"
        # DELIVERED flash at the destination
        tex = viz_sdf.make_text_texture(
            "DELIVERED", f"delivered_{task.task_id}", fg=(120, 255, 150)
        )
        ent = f"viz_delivered_{task.task_id}"
        self._gz.create(
            ent, viz_sdf.text_model(ent, tex[0], tex[1], tex[2]), dx, dy, 5.2
        )
        self._event_ents[ent] = time.monotonic() + 2.0
        self._event(f"TASK COMPLETED  {task.task_id}", dx, dy - 2.0)
        task.state = "DONE"
        self.get_logger().info(
            f"DELIVER {pkg.package_id} -> ({dx:.1f},{dy:.1f}) ({task.task_id})"
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def _event(self, text, x, y, z=6.0):
        import hashlib

        tag = hashlib.md5(text.encode()).hexdigest()[:8]
        ent = f"viz_event_{tag}"
        tex = viz_sdf.make_text_texture(text, ent, fg=(255, 230, 120))
        self._gz.create(ent, viz_sdf.text_model(ent, tex[0], tex[1], tex[2]), x, y, z)
        self._event_ents[ent] = time.monotonic() + 3.0

    def _expire_events(self):
        now = time.monotonic()
        for ent, t in list(self._event_ents.items()):
            if now >= t:
                self._gz.remove(ent)
                del self._event_ents[ent]

    # ------------------------------------------------------------------
    # Per-tick updates
    # ------------------------------------------------------------------
    def _update(self):
        for rid, robot in list(self._robots.items()):
            pose = robot.get("pose")
            if not pose:
                continue
            x, y, yaw = pose
            glow = self._glow_ent.get(rid)
            if glow:
                self._gz.set_pose(glow, x, y, 0.0)
            label = self._label_ent.get(rid)
            if label:
                self._gz.set_pose(label, x, y, 2.6)
            # carried package follows the robot
            pkg_id = self._carry.get(rid)
            if pkg_id:
                self._gz.set_pose(
                    f"viz_pkg_{pkg_id}", x, y, PACKAGE_LIFT - PACKAGE_SIZE / 2.0, yaw
                )
        self._apply_camera()

    def _apply_camera(self, force=False):
        mode = self._camera_mode
        eye = target = None
        follow = False
        if mode == "overview":
            eye, target = (0, -14, 11), (0, 0, 0)
        elif mode == "pickup":
            eye, target = (-2, -13, 8), (-2, -7, 0)
        elif mode == "delivery":
            eye, target = (2, -13, 8), (2, -7, 0)
        elif mode.startswith("follow:"):
            rid = mode.split(":", 1)[1]
            pose = self._robots.get(rid, {}).get("pose")
            if pose:
                eye = (pose[0] + 4, pose[1] - 4, 4.5)
                target = (pose[0], pose[1], 0)
            follow = True
        elif self._auto_follow and self._active_robot:
            pose = self._robots.get(self._active_robot, {}).get("pose")
            if pose:
                eye = (pose[0] + 4, pose[1] - 4, 4.5)
                target = (pose[0], pose[1], 0)
            follow = True
        if eye is None:
            eye, target = (0, -14, 11), (0, 0, 0)
        self._camera_shot = (eye, target)
        # follow modes republish every tick; static shots only on start / force
        if follow or force or not self._camera_started:
            self._camera_started = True
            self._gz.camera(eye, target)


def main():
    rclpy.init()
    node = VisualizationNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
