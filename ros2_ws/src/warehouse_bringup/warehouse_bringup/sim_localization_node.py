"""Simulated localization — the single authoritative robot pose source.

Publishes each robot's ACTUAL Gazebo model pose (read from the physics
engine's /world/.../pose/info topic) as /ns/amcl_pose. This is the ONE
source of truth: planner, controller, task manager, status beacon and
visualization all read /ns/amcl_pose, so they always agree with the
physical robot model. No odometry integration is involved — the pose is
exactly what the simulator reports for the burger_1 / burger_2 models.

Subscribes: /world/{world}/pose/info (gz.msgs.Pose_V) — the physics engine's
            authoritative model poses
Publishes:  /ns/amcl_pose (geometry_msgs/PoseWithCovarianceStamped) for every
            robot configured in the model_names parameter

The legacy scan-based AMCL node is intentionally not launched: it was a
second, disagreeing publisher of /ns/amcl_pose that desynchronized every
consumer.
"""

import json
import math
import threading

import gz.transport13 as gztr
import rclpy
from gz.msgs10 import pose_v_pb2
from geometry_msgs.msg import Point, PoseWithCovarianceStamped, Quaternion
from rclpy.node import Node


def _yaw(q):
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    )


class SimLocalizationNode(Node):
    def __init__(self):
        super().__init__("sim_localization")
        self.declare_parameter("world", "warehouse_world")
        self.declare_parameter(
            "model_names", '{"robot1": "burger_1", "robot2": "burger_2"}'
        )

        self._world = self.get_parameter("world").value
        raw_models = (
            self.get_parameter("model_names").get_parameter_value().string_value
        )
        try:
            self._model_names = json.loads(raw_models)
        except (ValueError, TypeError):
            self._model_names = {"robot1": "burger_1", "robot2": "burger_2"}

        self._entities = {}  # robot_id -> {'ns', 'pub'}
        self._gz_pose = {}   # gz model name -> (x, y, yaw)
        self._lock = threading.Lock()

        # Create a pose publisher for every configured robot up front. The
        # publisher topic is /<rid>/amcl_pose (namespace derived from the
        # robot id). This makes the node fully standalone: it does not depend
        # on /fleet_status (which the disabled fleet manager would publish).
        for rid, model in self._model_names.items():
            self._entities[rid] = {
                "ns": rid,
                "pub": self.create_publisher(
                    PoseWithCovarianceStamped, f"/{rid}/amcl_pose", 10
                ),
            }

        # Subscribe to the physics engine's authoritative model poses. The gz
        # transport callbacks run on their own thread, so the lock guards the
        # shared pose cache.
        self._gz = gztr.Node()
        self._gz.subscribe(
            pose_v_pb2.Pose_V,
            f"/world/{self._world}/pose/info",
            self._on_gz_pose,
        )
        self.create_timer(0.5, self._republish)
        self.get_logger().info(
            f"Sim localization ready (gz model pose -> amcl_pose, "
            f"world={self._world}, models={self._model_names})"
        )

    def _on_gz_pose(self, msg):
        with self._lock:
            for p in msg.pose:
                name = p.name
                if name in self._model_names.values():
                    self._gz_pose[name] = (
                        p.position.x,
                        p.position.y,
                        _yaw(p.orientation),
                    )

    def _republish(self):
        with self._lock:
            for rid, ent in self._entities.items():
                model = self._model_names.get(rid)
                pose = self._gz_pose.get(model)
                if not pose:
                    continue
                x, y, yaw = pose
                msg = PoseWithCovarianceStamped()
                msg.header.stamp = self.get_clock().now().to_msg()
                msg.header.frame_id = "map"
                msg.pose.pose.position = Point(x=x, y=y, z=0.0)
                msg.pose.pose.orientation = Quaternion(
                    x=0.0, y=0.0, z=math.sin(yaw / 2.0), w=math.cos(yaw / 2.0)
                )
                ent["pub"].publish(msg)


def main():
    rclpy.init()
    node = SimLocalizationNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
