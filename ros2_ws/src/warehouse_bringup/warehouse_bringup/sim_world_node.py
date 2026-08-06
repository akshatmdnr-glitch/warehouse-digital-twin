"""Headless world physics — a minimal Gazebo substitute.

In headless environments (no GPU rendering) Gazebo's engine is unavailable,
so this node provides the motion model a robot needs to actually navigate:
it integrates the velocity command published on /ns/cmd_vel and republishes
the robot pose, odometry and a clear laser scan for every namespace it knows.

Subscribes: /ns/cmd_vel (geometry_msgs/TwistStamped) per robot
Publishes:  /ns/amcl_pose (PoseWithCovarianceStamped)
            /ns/odom (nav_msgs/Odometry)
            /ns/scan (sensor_msgs/LaserScan, obstacle-free)

Parameters:
    robots            comma-separated namespaces (e.g. "robot1,robot2")
    spawn_x/y/yaw_<i> per-robot spawn pose (or spawn list JSON)
    max_speed         clamp for linear velocity (m/s)
"""

import json
import math
import time

import rclpy
from geometry_msgs.msg import Point, PoseWithCovarianceStamped, Quaternion, TwistStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import LaserScan


def _yaw_to_quat(yaw):
    return Quaternion(x=0.0, y=0.0, z=math.sin(yaw / 2.0), w=math.cos(yaw / 2.0))


class SimWorldNode(Node):
    def __init__(self):
        super().__init__("sim_world")
        self.declare_parameter("robots", "robot1")
        self.declare_parameter("spawns", "")  # JSON: {ns: [x, y, yaw]}
        self.declare_parameter("max_speed", 0.25)
        self.declare_parameter(
            "bounds", "[-3.0, 7.0, -3.0, 7.0]"
        )  # xmin xmax ymin ymax

        self._max_speed = float(self.get_parameter("max_speed").value)
        self._bounds = json.loads(self.get_parameter("bounds").value)
        spawns = json.loads(self.get_parameter("spawns").value or "{}")
        robots = [
            r.strip()
            for r in self.get_parameter("robots").value.split(",")
            if r.strip()
        ]

        self._robots = {}
        self._amcl_pubs = {}
        self._odom_pubs = {}
        self._scan_pubs = {}
        for i, ns in enumerate(robots):
            x, y, yaw = spawns.get(ns, [0.0, 0.0, 0.0])
            self._robots[ns] = {
                "x": float(x),
                "y": float(y),
                "yaw": float(yaw),
                "lin": 0.0,
                "ang": 0.0,
                "last_t": time.monotonic(),
            }
            topic = f"/{ns}/cmd_vel" if ns else "/cmd_vel"
            self.create_subscription(
                TwistStamped, topic, lambda m, rid=ns: self._on_cmd(rid, m), 10
            )
            self._amcl_pubs[ns] = self.create_publisher(
                PoseWithCovarianceStamped, f"/{ns}/amcl_pose", 10
            )
            self._odom_pubs[ns] = self.create_publisher(Odometry, f"/{ns}/odom", 10)
            self._scan_pubs[ns] = self.create_publisher(LaserScan, f"/{ns}/scan", 10)

        self.create_timer(0.05, self._tick)
        self.get_logger().info(
            f"Sim world ready for robots {robots} " f"(max_speed={self._max_speed} m/s)"
        )

    def _on_cmd(self, rid, msg):
        self._robots[rid]["lin"] = max(
            -self._max_speed, min(self._max_speed, msg.twist.linear.x)
        )
        self._robots[rid]["ang"] = msg.twist.angular.z

    def _tick(self):
        now = time.monotonic()
        for rid, r in self._robots.items():
            dt = min(0.1, now - r["last_t"])
            r["last_t"] = now
            r["yaw"] += r["ang"] * dt
            r["x"] += r["lin"] * math.cos(r["yaw"]) * dt
            r["y"] += r["lin"] * math.sin(r["yaw"]) * dt
            self._clamp(rid, r)
            self._publish_amcl(rid, r)
            self._publish_odom(rid, r)
            self._publish_scan(rid)

    def _clamp(self, rid, r):
        xmin, xmax, ymin, ymax = self._bounds
        r["x"] = max(xmin, min(xmax, r["x"]))
        r["y"] = max(ymin, min(ymax, r["y"]))

    def _publish_amcl(self, rid, r):
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.pose.pose.position = Point(x=r["x"], y=r["y"], z=0.0)
        msg.pose.pose.orientation = _yaw_to_quat(r["yaw"])
        self._amcl_pubs[rid].publish(msg)

    def _publish_odom(self, rid, r):
        msg = Odometry()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "odom"
        msg.child_frame_id = "base_link"
        msg.pose.pose.position = Point(x=r["x"], y=r["y"], z=0.0)
        msg.pose.pose.orientation = _yaw_to_quat(r["yaw"])
        msg.twist.twist.linear.x = r["lin"]
        msg.twist.twist.angular.z = r["ang"]
        self._odom_pubs[rid].publish(msg)

    def _publish_scan(self, rid):
        n = int(2 * math.pi / 0.017453) + 1
        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "base_scan"
        msg.angle_min, msg.angle_max, msg.angle_increment = -math.pi, math.pi, 0.017453
        msg.range_min, msg.range_max = 0.12, 12.0
        msg.ranges = [5.0] * n  # obstacle-free
        self._scan_pubs[rid].publish(msg)


def main():
    rclpy.init()
    node = SimWorldNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
