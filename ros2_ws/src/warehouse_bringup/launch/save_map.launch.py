"""Save the current /map topic to PGM + YAML files.

Usage:
    ros2 launch warehouse_bringup save_map.launch.py
    ros2 launch warehouse_bringup save_map.launch.py map_path:=/path/to/map_name

Default: saves to the package maps/ directory as warehouse_map.{pgm,yaml}
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    maps_dir = os.path.join(
        get_package_share_directory('warehouse_bringup'),
        'maps',
    )

    default_path = os.path.join(maps_dir, 'warehouse_map')
    map_path = LaunchConfiguration('map_path', default=default_path)

    saver_node = Node(
        package='warehouse_bringup',
        executable='save_warehouse_map',
        name='map_saver',
        output='screen',
        parameters=[{'map_path': map_path}],
    )

    return LaunchDescription([saver_node])
