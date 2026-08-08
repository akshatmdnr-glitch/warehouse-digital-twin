"""Pure Pursuit path-following controller.

Subscribes: /plan (nav_msgs/Path), /amcl_pose (PoseWithCovarianceStamped)
Publishes:  /cmd_vel (geometry_msgs/Twist)

Path-following behaviour:
  * The lookahead point is selected by walking forward along the path for a
    fixed arc-length distance (lookahead_distance), so the robot commits to
    the plan instead of chasing the nearest waypoint cell.
  * Linear and angular velocities are rate-limited (acceleration limited) so
    steering corrections ramp in smoothly instead of snapping.
  * The robot only slows down for genuinely sharp turns and near the goal;
    in open aisles it holds a steady speed on the planned trajectory.
"""

import math

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, TwistStamped
from nav_msgs.msg import Path
from rclpy.node import Node


class ControllerNode(Node):
    def __init__(self):
        super().__init__("controller")
        self.declare_parameter("linear_speed", 0.3)
        self.declare_parameter("angular_speed", 0.8)
        self.declare_parameter("goal_tolerance", 0.15)
        self.declare_parameter("waypoint_tolerance", 0.1)
        self.declare_parameter("lookahead_distance", 0.9)
        self.declare_parameter("max_linear_accel", 0.5)
        self.declare_parameter("max_angular_accel", 1.5)
        self.declare_parameter("turn_slow_threshold", 0.25)
        self.declare_parameter("turn_slow_factor", 0.6)

        self._linear_speed = self.get_parameter("linear_speed").value
        self._angular_speed = self.get_parameter("angular_speed").value
        self._goal_tolerance = self.get_parameter("goal_tolerance").value
        self._waypoint_tolerance = self.get_parameter("waypoint_tolerance").value
        self._lookahead = self.get_parameter("lookahead_distance").value
        self._max_linear_accel = self.get_parameter("max_linear_accel").value
        self._max_angular_accel = self.get_parameter("max_angular_accel").value
        self._turn_slow_threshold = self.get_parameter("turn_slow_threshold").value
        self._turn_slow_factor = self.get_parameter("turn_slow_factor").value

        self._path = None
        self._current_pose = None
        self._dt = 0.1  # matches the control timer period
        self._last_linear = 0.0
        self._last_angular = 0.0

        self._plan_sub = self.create_subscription(
            Path, "/plan", self._plan_callback, 10
        )
        self._pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._pose_callback, 10
        )
        self._cmd_pub = self.create_publisher(TwistStamped, "/cmd_vel", 10)

        self._timer = self.create_timer(self._dt, self._control_loop)

        self.get_logger().info(
            f"Controller ready: speed={self._linear_speed} m/s, "
            f"angular={self._angular_speed} rad/s, "
            f"lookahead={self._lookahead} m, "
            f"tolerance={self._goal_tolerance} m"
        )

    def _plan_callback(self, msg):
        self._path = msg.poses
        self._last_linear = 0.0
        self._last_angular = 0.0
        self.get_logger().info(f"New path: {len(self._path)} waypoints")

    def _pose_callback(self, msg):
        self._current_pose = msg.pose.pose

    def _control_loop(self):
        if not self._path or not self._current_pose:
            self._publish_cmd(0.0, 0.0)
            return

        px = self._current_pose.position.x
        py = self._current_pose.position.y
        theta = self._get_yaw(self._current_pose.orientation)

        # If path is empty (start/goal not set)
        if len(self._path) == 0:
            self._publish_cmd(0.0, 0.0)
            return

        # Get goal pose
        goal_pose = self._path[-1].pose
        gx = goal_pose.position.x
        gy = goal_pose.position.y

        # Check if goal reached
        dist_to_goal = math.hypot(gx - px, gy - py)
        if dist_to_goal < self._goal_tolerance:
            self.get_logger().info(f"Goal reached: ({gx:.2f}, {gy:.2f})")
            self._path = None
            self._publish_cmd(0.0, 0.0)
            return

        # Find nearest waypoint index (project robot onto the path).
        nearest_idx = 0
        nearest_dist = float("inf")
        for i, pose_stamped in enumerate(self._path):
            wx = pose_stamped.pose.position.x
            wy = pose_stamped.pose.position.y
            d = math.hypot(wx - px, wy - py)
            if d < nearest_dist:
                nearest_dist = d
                nearest_idx = i

        # Distance-based lookahead: walk forward along the path for a fixed
        # arc-length (lookahead_distance) instead of a fixed waypoint count.
        # This makes the robot commit to the planned trajectory far ahead and
        # eliminates the constant micro-steering of a short fixed lookahead.
        lookahead_idx = self._find_lookahead_index(nearest_idx)
        target = self._path[lookahead_idx].pose
        tx = target.position.x
        ty = target.position.y

        # Transform target to robot frame
        dx = tx - px
        dy = ty - py
        local_x = dx * math.cos(theta) + dy * math.sin(theta)
        local_y = -dx * math.sin(theta) + dy * math.cos(theta)

        # Pure Pursuit: curvature = 2 * lateral_error / L^2
        L = math.hypot(local_x, local_y)
        if L < 0.01:
            self._publish_cmd(0.0, 0.0)
            return

        curvature = (2.0 * local_y) / (L * L)

        # Angular velocity
        angular_z = self._angular_speed * curvature
        angular_z = max(-self._angular_speed, min(self._angular_speed, angular_z))

        # Linear velocity — slow down only for genuinely sharp turns.
        linear_x = self._linear_speed
        if abs(angular_z) > self._turn_slow_threshold:
            linear_x *= self._turn_slow_factor
        # Slow down near goal
        if dist_to_goal < self._lookahead * 2:
            linear_x *= max(0.3, dist_to_goal / (self._lookahead * 2))

        # Rate-limit velocity commands so corrections ramp smoothly.
        linear_x = self._rate_limit(linear_x, self._last_linear, self._max_linear_accel)
        angular_z = self._rate_limit(angular_z, self._last_angular, self._max_angular_accel)
        self._last_linear = linear_x
        self._last_angular = angular_z

        self._publish_cmd(linear_x, angular_z)

    def _find_lookahead_index(self, nearest_idx):
        """Return the first waypoint at least `lookahead_distance` arc-length
        ahead of the nearest waypoint, walking forward along the path."""
        n = len(self._path)
        arc = 0.0
        idx = nearest_idx
        while idx < n - 1:
            w0 = self._path[idx].pose.position
            w1 = self._path[idx + 1].pose.position
            arc += math.hypot(w1.x - w0.x, w1.y - w0.y)
            if arc >= self._lookahead:
                return idx + 1
            idx += 1
        return n - 1

    def _rate_limit(self, desired, previous, max_accel):
        delta = max_accel * self._dt
        return max(previous - delta, min(desired, previous + delta))

    def _publish_cmd(self, linear_x, angular_z):
        twist = TwistStamped()
        twist.header.stamp = self.get_clock().now().to_msg()
        twist.header.frame_id = "base_link"
        twist.twist.linear.x = linear_x
        twist.twist.angular.z = angular_z
        self._cmd_pub.publish(twist)

    @staticmethod
    def _get_yaw(orientation):
        q = orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny_cosp, cosy_cosp)


def main():
    rclpy.init()
    node = ControllerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
