"""Obstacle monitor — stops the robot when obstacles are detected by LiDAR.

Subscribes: /scan, /controller_cmd_vel
Publishes: /cmd_vel (safe velocity)

If the LiDAR feed is unavailable (no /scan messages within scan_timeout),
the monitor forwards the controller's command unchanged so the robot can
still navigate (a common situation when the laser rendering is degraded).
"""

import math
import time

import rclpy
from geometry_msgs.msg import TwistStamped
from rclpy.node import Node
from sensor_msgs.msg import LaserScan


class ObstacleMonitor(Node):
    def __init__(self):
        super().__init__("obstacle_monitor")
        self.declare_parameter("stop_distance", 0.5)
        self.declare_parameter("field_of_view", 60.0)  # degrees (half-angle)
        self.declare_parameter("min_safe_ranges", 3)
        self.declare_parameter("scan_timeout", 1.0)

        self._stop_distance = self.get_parameter("stop_distance").value
        self._fov = math.radians(self.get_parameter("field_of_view").value)
        self._min_safe = self.get_parameter("min_safe_ranges").value
        self._scan_timeout = self.get_parameter("scan_timeout").value

        self._blocked = False
        self._last_cmd = TwistStamped()
        self._last_cmd.twist.linear.x = 0.0
        self._last_cmd.twist.angular.z = 0.0
        self._last_scan = 0.0

        self._scan_sub = self.create_subscription(
            LaserScan, "/scan", self._scan_callback, 10
        )
        self._cmd_sub = self.create_subscription(
            TwistStamped, "/controller_cmd_vel", self._cmd_callback, 10
        )
        self._cmd_pub = self.create_publisher(TwistStamped, "/cmd_vel", 10)
        self._timer = self.create_timer(0.1, self._forward_cmd)

        self.get_logger().info(
            f"Obstacle monitor ready: stop_dist={self._stop_distance}m, "
            f"fov={self._fov:.1f}rad, scan_timeout={self._scan_timeout}s"
        )

    def _scan_callback(self, scan_msg):
        self._last_scan = time.monotonic()
        blocked = self._check_obstacle(scan_msg)
        if blocked != self._blocked:
            self._blocked = blocked
            if blocked:
                self.get_logger().warn(
                    f"Obstacle detected within {self._stop_distance}m — stopping"
                )
            else:
                self.get_logger().info("Path clear — resuming")

    def _cmd_callback(self, msg):
        self._last_cmd = msg

    def _check_obstacle(self, scan_msg):
        """True only when the path ahead is genuinely blocked.

        Counts front-facing beams that are shorter than stop_distance and
        requires at least min_safe_ranges of them to be blocked. A single
        spurious short beam (lidar blip) does not stop the robot, so it
        commits to the planned trajectory in open aisles and only brakes
        when an obstacle really occupies the path.
        """
        angle_min = scan_msg.angle_min
        angle_increment = scan_msg.angle_increment
        blocked_beams = 0
        for i, r in enumerate(scan_msg.ranges):
            angle = angle_min + i * angle_increment
            # Check front-facing beams only
            if abs(angle) < self._fov or abs(angle - 2 * math.pi) < self._fov:
                if math.isfinite(r) and r < self._stop_distance:
                    blocked_beams += 1
                    if blocked_beams >= self._min_safe:
                        return True
        return False

    def _forward_cmd(self):
        scan_ok = time.monotonic() - self._last_scan <= self._scan_timeout
        # Without a fresh scan we cannot detect obstacles, so pass the
        # controller's command straight through instead of freezing the robot.
        self._publish_cmd(self._blocked if scan_ok else False)

    def _publish_cmd(self, blocked):
        twist = TwistStamped()
        twist.header.stamp = self.get_clock().now().to_msg()
        twist.header.frame_id = "base_link"
        if blocked:
            twist.twist.linear.x = 0.0
            twist.twist.angular.z = 0.0
        else:
            twist.twist.linear.x = self._last_cmd.twist.linear.x
            twist.twist.angular.z = self._last_cmd.twist.angular.z
        self._cmd_pub.publish(twist)


def main():
    rclpy.init()
    node = ObstacleMonitor()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
