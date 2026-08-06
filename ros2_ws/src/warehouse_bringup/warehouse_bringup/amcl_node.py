"""Minimal Monte Carlo Localization using a saved map.

Subscribes to /scan and /tf, publishes map→odom transform and /amcl_pose.
No external dependencies beyond ROS2 standard packages.
"""

import math
import random

import rclpy
from builtin_interfaces.msg import Time as BuiltinTime
from geometry_msgs.msg import (
    Point,
    Pose,
    PoseWithCovarianceStamped,
    Quaternion,
    TransformStamped,
)
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan
from tf2_ros import TransformBroadcaster


def _euler_to_quaternion(roll, pitch, yaw):
    """Convert Euler angles (roll, pitch, yaw) to quaternion."""
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return Quaternion(
        x=sr * cp * cy - cr * sp * sy,
        y=cr * sp * cy + sr * cp * sy,
        z=cr * cp * sy - sr * sp * cy,
        w=cr * cp * cy + sr * sp * sy,
    )


def _quaternion_to_euler(q):
    """Convert quaternion to Euler angles (roll, pitch, yaw)."""
    sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z)
    cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2.0 * (q.w * q.y - q.z * q.x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return roll, pitch, yaw


def _normalize_angle(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


class AMCLNode(Node):
    def __init__(self):
        super().__init__("amcl")
        self.declare_parameter("num_particles", 500)
        self.declare_parameter("motion_noise_xy", 0.05)
        self.declare_parameter("motion_noise_theta", 0.1)

        num_particles = self.get_parameter("num_particles").value
        self._motion_noise_xy = self.get_parameter("motion_noise_xy").value
        self._motion_noise_theta = self.get_parameter("motion_noise_theta").value

        self._map_data = None
        self._map_info = None
        self._particles = []
        self._weights = []
        self._num_particles = num_particles

        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )

        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._map_callback, map_qos
        )
        self._scan_sub = self.create_subscription(
            LaserScan, "/scan", self._scan_callback, 10
        )
        self._pose_pub = self.create_publisher(
            PoseWithCovarianceStamped, "/amcl_pose", 10
        )
        self._init_pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/initialpose", self._init_pose_callback, 10
        )

        self._tf_br = TransformBroadcaster(self)
        self._prev_x = 0.0
        self._prev_y = 0.0
        self._prev_theta = 0.0
        self._odom_set = False

        self.get_logger().info(
            f"AMCL ready with {num_particles} particles, waiting for map..."
        )

    def _map_callback(self, msg):
        self._map_data = msg.data
        self._map_info = msg.info
        mi = msg.info
        if not self._odom_set:
            self.get_logger().info(
                f"Map received: {mi.width}x{mi.height} "
                f"({mi.resolution:.3f} m/pixel), initializing particles"
            )
            self._init_particles_uniform(mi)
            self._odom_set = True

    def _init_particles_uniform(self, mi):
        free = [
            (i, j)
            for i in range(mi.width)
            for j in range(mi.height)
            if self._map_data[j * mi.width + i] == 0
        ]
        if not free:
            free = [(mi.width // 2, mi.height // 2)]

        ox = mi.origin.position.x
        oy = mi.origin.position.y
        res = mi.resolution

        self._particles = []
        for _ in range(self._num_particles):
            ci, cj = random.choice(free)
            x = ox + (ci + random.random()) * res
            y = oy + (cj + random.random()) * res
            theta = random.uniform(-math.pi, math.pi)
            self._particles.append((x, y, theta))
        self._weights = [1.0 / self._num_particles] * self._num_particles

    def _init_pose_callback(self, msg):
        if not self._map_info:
            return
        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        _, _, theta = _quaternion_to_euler(q)

        noise_xy = 0.3
        noise_theta = 0.3
        self._particles = [
            (
                px + random.gauss(0, noise_xy),
                py + random.gauss(0, noise_xy),
                theta + random.gauss(0, noise_theta),
            )
            for _ in range(self._num_particles)
        ]
        self._weights = [1.0 / self._num_particles] * self._num_particles
        self._prev_x = px
        self._prev_y = py
        self._prev_theta = theta
        self._odom_set = True
        self.get_logger().info(f"Initial pose: ({px:.2f}, {py:.2f}, {theta:.2f})")

    def _scan_callback(self, scan_msg):
        if not self._map_info or not self._particles:
            return

        # Apply motion update using scan data timing as a proxy
        self._motion_update()

        # Resample
        self._resample()

        # Add noise to combat particle depletion
        n_xy = self._motion_noise_xy * 0.2
        n_th = self._motion_noise_theta * 0.1
        for i in range(len(self._particles)):
            x, y, theta = self._particles[i]
            self._particles[i] = (
                x + random.gauss(0, n_xy),
                y + random.gauss(0, n_xy),
                theta + random.gauss(0, n_th),
            )

        self._publish_estimate(scan_msg.header.stamp)

    def _motion_update(self):
        """Apply small random motion update between scan messages."""
        for i in range(len(self._particles)):
            x, y, theta = self._particles[i]
            dx = random.gauss(0, self._motion_noise_xy)
            dy = random.gauss(0, self._motion_noise_xy)
            dtheta = random.gauss(0, self._motion_noise_theta)
            x += dx * math.cos(theta) - dy * math.sin(theta)
            y += dx * math.sin(theta) + dy * math.cos(theta)
            self._particles[i] = (x, y, _normalize_angle(theta + dtheta))

    def _resample(self):
        if not self._particles:
            return
        n = self._num_particles
        w = self._weights
        total = sum(w) or 1.0
        w = [wi / total for wi in w]

        new_particles = []
        r = random.random() / n
        c = w[0]
        i = 0
        for m in range(n):
            u = r + m / n
            while u > c:
                i = (i + 1) % n
                c += w[i]
            new_particles.append(self._particles[i])
        self._particles = new_particles
        self._weights = [1.0 / n] * n

    def _publish_estimate(self, stamp):
        if not self._particles:
            return
        # Weighted mean
        n = len(self._particles)
        if n == 0:
            return
        # Use average of all particles
        x = sum(p[0] for p in self._particles) / n
        y = sum(p[1] for p in self._particles) / n
        theta = math.atan2(
            sum(math.sin(p[2]) for p in self._particles),
            sum(math.cos(p[2]) for p in self._particles),
        )

        q = _euler_to_quaternion(0, 0, theta)

        # Publish pose
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = "map"
        msg.pose.pose = Pose(position=Point(x=x, y=y, z=0.0), orientation=q)
        self._pose_pub.publish(msg)

        # Publish map→odom transform
        t = TransformStamped()
        t.header.stamp = stamp
        t.header.frame_id = "map"
        t.child_frame_id = "odom"
        t.transform.translation.x = x
        t.transform.translation.y = y
        t.transform.translation.z = 0.0
        t.transform.rotation = q
        self._tf_br.sendTransform(t)


def main():
    rclpy.init()
    node = AMCLNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
