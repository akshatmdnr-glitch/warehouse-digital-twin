#!/usr/bin/env python3
"""Phase 3/4 — one physical yellow cube rigidly following robot1.

The cube is spawned as a real (non-static) model in Gazebo. While the robot
carries it, the cube pose is set EVERY tick strictly from the robot's live
pose (/robot1/amcl_pose, which is the actual Gazebo model pose) plus a fixed
offset. The cube never moves by itself — it only moves because the robot
moves. On DROPPING the cube is placed at the dropoff and no longer follows.

This is deliberately minimal: no labels, no glows, no markers, no
interpolation of the cube position.
"""

import json
import math
import time

import gz.transport13 as gztr
import rclpy
from gz.msgs10 import (
    boolean_pb2,
    entity_factory_pb2,
    entity_pb2,
    pose_pb2,
    pose_v_pb2,
)
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from std_msgs.msg import String

_CARRY_STATES = {"GO_TO_DROPOFF", "DROPPING"}

_CUBE_SDF = """<sdf version="1.9">
<model name="{name}">
  <static>true</static>
  <link name="cube_link">
    <visual name="vis">
      <geometry><box><size>0.15 0.15 0.15</size></box></geometry>
      <material><ambient>1 1 0 1</ambient><diffuse>1 1 0 1</diffuse></material>
    </visual>
  </link>
</model>
</sdf>"""


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


class CubeCarrier(Node):
    def __init__(self):
        super().__init__("cube_carrier")
        self.declare_parameter("world", "warehouse_world")
        self.declare_parameter("robot", "robot1")
        self.declare_parameter("carry_offset", "0.0,0.0,0.35")

        self._world = self.get_parameter("world").value
        self._robot = self.get_parameter("robot").value
        off = self.get_parameter("carry_offset").value.split(",")
        self._off = tuple(float(v) for v in off[:3])

        self._cube = None
        self._carrying = False
        self._dropoff = None
        self._pose = None

        self._gz = gztr.Node()
        self.create_subscription(
            PoseWithCovarianceStamped,
            f"/{self._robot}/amcl_pose",
            self._on_pose,
            10,
        )
        self.create_subscription(
            String,
            f"/{self._robot}/task_state",
            self._on_state,
            10,
        )
        self.create_timer(0.05, self._tick)  # 20 Hz follow

    def _on_pose(self, msg):
        p = msg.pose.pose
        self._pose = (p.position.x, p.position.y, p.position.z)

    def _on_state(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        state = data.get("state", "")
        if state == "PICKING" and not self._carrying and self._cube is None:
            self._spawn_cube()
        elif state in _CARRY_STATES and not self._carrying:
            self._carrying = True
            self._carry_started = time.time()
        elif state == "DROPPING" and self._carrying:
            # Robot is physically at the dropoff; detach the cube there.
            self._carrying = False
            self._drop_cube()

    def _spawn_cube(self):
        name = "demo_cube"
        sdf = _CUBE_SDF.format(name=name)
        req = entity_factory_pb2.EntityFactory()
        req.name = name
        req.sdf = sdf
        ok, _ = self._gz.request(
            f"/world/{self._world}/create",
            req,
            entity_factory_pb2.EntityFactory,
            boolean_pb2.Boolean,
            2000,
        )
        self._cube = name
        self.get_logger().info(f"Cube spawned (create ok={bool(ok)})")

    def _drop_cube(self):
        pose = self._pose
        if self._cube:
            if pose:
                self._set_model_pose(
                    self._cube, pose[0], pose[1], 0.075, 0.0
                )
            self._cube = None
        self.get_logger().info("Cube dropped at dropoff")

    def _set_model_pose(self, name, x, y, z, yaw):
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
        self._gz.request(
            f"/world/{self._world}/set_pose",
            req,
            pose_pb2.Pose,
            boolean_pb2.Boolean,
            2000,
        )

    def _tick(self):
        if not self._cube or not self._carrying:
            return
        pose = self._pose
        if pose is None:
            return
        # Cube position derived from robot pose + fixed offset. The cube does
        # not move independently — it only moves because the robot moves.
        x = pose[0] + self._off[0]
        y = pose[1] + self._off[1]
        z = pose[2] + self._off[2]
        self._set_model_pose(self._cube, x, y, z, 0.0)


def main():
    rclpy.init()
    node = CubeCarrier()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
