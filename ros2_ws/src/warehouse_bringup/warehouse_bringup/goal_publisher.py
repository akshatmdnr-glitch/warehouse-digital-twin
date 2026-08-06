"""Publish a single navigation goal pose.

Accepts x, y, yaw parameters and publishes as geometry_msgs/PoseStamped.
"""

import math

import rclpy
from geometry_msgs.msg import Point, PoseStamped, Quaternion
from rclpy.node import Node


def _euler_to_quaternion(yaw):
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return Quaternion(x=0.0, y=0.0, z=sy, w=cy)


class GoalPublisher(Node):
    def __init__(self):
        super().__init__("goal_publisher")
        self.declare_parameter("x", 0.0)
        self.declare_parameter("y", 0.0)
        self.declare_parameter("yaw", 0.0)

        x = self.get_parameter("x").value
        y = self.get_parameter("y").value
        yaw = self.get_parameter("yaw").value

        self._pub = self.create_publisher(PoseStamped, "/goal_pose", 10)
        self._timer = self.create_timer(1.0, self._publish_goal)

        self._goal = PoseStamped()
        self._goal.header.frame_id = "map"
        self._goal.pose.position = Point(x=x, y=y, z=0.0)
        self._goal.pose.orientation = _euler_to_quaternion(yaw)

        self.get_logger().info(f"Goal set: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f}")

    def _publish_goal(self):
        self._goal.header.stamp = self.get_clock().now().to_msg()
        self._pub.publish(self._goal)
        self.get_logger().debug("Goal republished")


def main():
    rclpy.init()
    node = GoalPublisher()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
