"""Load a saved map and publish it as a nav_msgs/OccupancyGrid.

Reads YAML metadata + PGM image and publishes on /map as a latched topic.
"""

import os

import builtin_interfaces.msg
import rclpy
import yaml
from geometry_msgs.msg import Point, Pose, Quaternion
from nav_msgs.msg import MapMetaData, OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Header


class MapLoader(Node):
    def __init__(self):
        super().__init__("map_loader")
        self.declare_parameter("map_yaml_path", "")
        yaml_path = (
            self.get_parameter("map_yaml_path").get_parameter_value().string_value
        )

        if not yaml_path or not os.path.isfile(yaml_path):
            self.get_logger().fatal(f"Map YAML not found: {yaml_path}")
            raise FileNotFoundError(yaml_path)

        pgm_path, metadata = self._load_yaml(yaml_path)
        if not os.path.isfile(pgm_path):
            self.get_logger().fatal(f"Map PGM not found: {pgm_path}")
            raise FileNotFoundError(pgm_path)

        self._width, self._height, self._map_data = self._load_pgm(pgm_path)

        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            depth=1,
        )

        self._pub = self.create_publisher(OccupancyGrid, "/map", qos)
        self._timer = self.create_timer(0.5, self._publish_map)

        origin_pose = Pose(
            position=Point(
                x=metadata["origin"][0],
                y=metadata["origin"][1],
                z=metadata["origin"][2],
            ),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
        )

        self._msg = OccupancyGrid(
            header=Header(frame_id="map"),
            info=MapMetaData(
                map_load_time=builtin_interfaces.msg.Time(sec=0, nanosec=0),
                resolution=metadata["resolution"],
                width=self._width,
                height=self._height,
                origin=origin_pose,
            ),
            data=self._map_data,
        )

        self.get_logger().info(
            f"Loaded map {self._width}x{self._height} "
            f'({metadata["resolution"]} m/pixel) from {os.path.basename(yaml_path)}'
        )

    def _load_yaml(self, yaml_path):
        with open(yaml_path, "r") as f:
            metadata = yaml.safe_load(f)
        pgm_path = os.path.join(os.path.dirname(yaml_path), metadata["image"])
        return pgm_path, metadata

    def _load_pgm(self, pgm_path):
        with open(pgm_path, "rb") as f:
            header = f.readline().decode().strip()
            if header != "P5":
                raise ValueError(f"Unsupported PGM format: {header}")
            dims = f.readline().decode().strip()
            while dims.startswith("#") or dims == "":
                dims = f.readline().decode().strip()
            width, height = map(int, dims.split())
            max_val = int(f.readline().decode().strip())
            raw = f.read()

        data = []
        for byte in raw:
            if byte == 205:
                data.append(-1)
            elif byte <= 25:
                data.append(0)
            elif byte >= 65:
                data.append(100)
            else:
                pct = (byte / max_val) * 100.0
                data.append(int(100.0 - pct))
        return width, height, data

    def _publish_map(self):
        stamp = self.get_clock().now().to_msg()
        self._msg.header.stamp = stamp
        self._pub.publish(self._msg)


def main():
    rclpy.init()
    node = MapLoader()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
