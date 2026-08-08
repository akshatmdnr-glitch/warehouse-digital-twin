#!/usr/bin/env python3
"""Demo package carrier — real packages on shelves, carried by robot1.

Spawns a few packages (P01..P0N) as static box models on the shelves, then
carries the selected one with robot1 during a pickup->delivery mission:

  * PICKING  -> attach: every tick set the package pose to the robot's live
                pose + fixed offset. The package moves ONLY because the robot
                moves — it is never interpolated or moved independently.
  * DROPPING -> place the package at the robot's live pose (the dropoff) and
                stop following. The package stays at the destination.

The robot pose (/robot1/amcl_pose) is the single source of truth; it is the
actual Gazebo model pose published by sim_localization_node.
"""

import json
import math

import gz.transport13 as gztr
import rclpy
from gz.msgs10 import (
    boolean_pb2,
    entity_factory_pb2,
    pose_pb2,
)
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from std_msgs.msg import String

from warehouse_bringup.order_fulfillment import (
    PACKAGE_SIZE,
    build_inventory,
)
from warehouse_bringup import viz_sdf

PACKAGE_LIFT = 0.42  # carry height of the box centre above the robot base
DELIVERY_Z = 0.15    # resting height of the box centre at the destination


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


class PackageCarrier(Node):
    def __init__(self):
        super().__init__("package_carrier")
        self.declare_parameter("world", "warehouse_world")
        self.declare_parameter("robot", "robot1")
        self.declare_parameter("package_id", "P01")
        self.declare_parameter("package_count", 6)
        self.declare_parameter("package_offset", 0)

        self._world = self.get_parameter("world").value
        self._robot = self.get_parameter("robot").value
        self._package_id = self.get_parameter("package_id").value
        self._package_count = int(self.get_parameter("package_count").value)
        self._package_offset = int(self.get_parameter("package_offset").value)

        self._inventory = build_inventory()
        self._carrying = False
        self._pose = None  # (x, y) from /robot1/amcl_pose

        self._gz = gztr.Node()
        self.create_subscription(
            PoseWithCovarianceStamped,
            f"/{self._robot}/amcl_pose",
            self._on_pose,
            10,
        )
        self.create_subscription(
            String, f"/{self._robot}/task_state", self._on_state, 10
        )
        self.create_timer(0.05, self._tick)  # 20 Hz follow while carrying

        # Spawn the requested packages on their shelves. Each carrier instance
        # claims a disjoint slice of the inventory (package_offset) so two
        # carriers (robot1 + robot2) never spawn the same model name.
        for pkg in self._inventory.packages[
            self._package_offset : self._package_offset + self._package_count
        ]:
            self._spawn_package(pkg)

    # -- Gazebo helpers ---------------------------------------------------

    def _request(self, service, msg, req_type, rep_type, timeout=2000):
        try:
            ok, _ = self._gz.request(
                f"/world/{self._world}/{service}",
                msg,
                req_type,
                rep_type,
                timeout,
            )
            return bool(ok)
        except Exception:  # noqa: BLE001 - transport hiccups are retried by caller
            return False

    def _spawn_package(self, pkg):
        name = f"pkg_{pkg.package_id}"
        sdf = viz_sdf.package_model(pkg.package_id, pkg.color)
        req = entity_factory_pb2.EntityFactory()
        req.name = name
        req.sdf = sdf
        req.pose.position.x = pkg.x
        req.pose.position.y = pkg.y
        # The box visual in the model is offset +PACKAGE_SIZE/2 from the model
        # origin, so the model origin goes half a box lower than the centre.
        req.pose.position.z = pkg.z - PACKAGE_SIZE / 2.0
        ok = self._request("create", req, entity_factory_pb2.EntityFactory,
                           boolean_pb2.Boolean)
        self.get_logger().info(
            f"Spawned {name} at ({pkg.x:.2f},{pkg.y:.2f},{pkg.z:.2f}) "
            f"ok={ok}"
        )

    def _set_pkg_pose(self, x, y, z, yaw=0.0):
        req = pose_pb2.Pose()
        req.name = f"pkg_{self._package_id}"
        req.position.x = x
        req.position.y = y
        req.position.z = z
        qx, qy, qz, qw = _euler_to_quat(0.0, 0.0, yaw)
        req.orientation.x = qx
        req.orientation.y = qy
        req.orientation.z = qz
        req.orientation.w = qw
        return self._request("set_pose", req, pose_pb2.Pose,
                             boolean_pb2.Boolean)

    # -- ROS callbacks ----------------------------------------------------

    def _on_pose(self, msg):
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y)

    def _on_state(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        state = data.get("state", "")
        if state == "PICKING":
            self._carrying = True
            self.get_logger().info("Package attached to robot (PICKING)")
        elif state == "DROPPING":
            self._carrying = False
            self._place_package()
            self.get_logger().info("Package placed at dropoff (DROPPING)")

    # -- Behaviour --------------------------------------------------------

    def _tick(self):
        if not self._carrying or self._pose is None:
            return
        x, y = self._pose
        # Derived strictly from the robot's live pose + fixed offset. The
        # package never moves on its own. The model origin is placed half a
        # box below the desired centre height.
        self._set_pkg_pose(x, y, PACKAGE_LIFT - PACKAGE_SIZE / 2.0)

    def _place_package(self):
        if self._pose is None:
            return
        x, y = self._pose
        self._set_pkg_pose(x, y, DELIVERY_Z - PACKAGE_SIZE / 2.0)


def main():
    rclpy.init()
    node = PackageCarrier()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
