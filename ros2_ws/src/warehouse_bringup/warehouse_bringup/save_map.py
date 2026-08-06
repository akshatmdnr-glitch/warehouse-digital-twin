#!/usr/bin/env python3
"""Save the current /map topic as PGM + YAML map files."""

import os
import sys

import rclpy
import yaml
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node


class MapSaver(Node):
    def __init__(self):
        super().__init__("map_saver")
        self.declare_parameter("map_path", "warehouse_map")
        self.map_path = (
            self.get_parameter("map_path").get_parameter_value().string_value
        )
        self.received = False
        self.sub = self.create_subscription(
            OccupancyGrid,
            "/map",
            self.callback,
            10,
        )

    def callback(self, msg):
        if self.received:
            return
        self.received = True
        self.get_logger().info(f"Saving map to {self.map_path}")
        self._write_map(msg)
        self.destroy_node()
        rclpy.shutdown()

    def _write_map(self, msg):
        pgm_path = self.map_path + ".pgm"
        yaml_path = self.map_path + ".yaml"

        width = msg.info.width
        height = msg.info.height
        if width == 0 or height == 0:
            self.get_logger().info("Empty map received, saving minimal placeholder")
            self._write_pgm(pgm_path, [0], 1, 1)
        else:
            self._write_pgm(pgm_path, msg.data, width, height)

        origin = msg.info.origin
        metadata = {
            "image": os.path.basename(pgm_path),
            "mode": "trinary",
            "resolution": msg.info.resolution,
            "origin": [origin.position.x, origin.position.y, 0.0],
            "negate": 0,
            "occupied_thresh": 0.65,
            "free_thresh": 0.25,
        }

        with open(yaml_path, "w") as f:
            yaml.dump(metadata, f)

        self.get_logger().info(f"Saved {yaml_path} and {pgm_path}")

    def _write_pgm(self, path, data, width, height):
        with open(path, "wb") as f:
            f.write(f"P5\n{width} {height}\n255\n".encode())
            for val in data:
                if val == -1:
                    f.write(bytes([205]))
                elif val >= 65:
                    f.write(bytes([0]))
                elif val <= 25:
                    f.write(bytes([255]))
                else:
                    f.write(bytes([255 - (val * 255 // 100)]))


def main():
    rclpy.init()
    node = MapSaver()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
