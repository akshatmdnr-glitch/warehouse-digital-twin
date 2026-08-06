"""Load a saved map and publish it on /map (or /ns/map in a namespace).

Usage:
    ros2 launch warehouse_bringup load_map.launch.py
    ros2 launch warehouse_bringup load_map.launch.py map:=my_map.yaml
    ros2 launch warehouse_bringup load_map.launch.py namespace:=robot1
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    map_file = LaunchConfiguration('map', default='warehouse_map.yaml')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this map server (empty = root)')
    declare_map_arg = DeclareLaunchArgument(
        'map',
        default_value='warehouse_map.yaml',
        description='Map YAML file to load from the maps/ directory',
    )

    map_path = PathJoinSubstitution([
        get_package_share_directory('warehouse_bringup'),
        'maps',
        map_file,
    ])

    map_server_node = Node(
        package='warehouse_bringup',
        executable='load_warehouse_map',
        name='map_server',
        namespace=namespace,
        output='screen',
        parameters=[{'map_yaml_path': map_path}],
        remappings=[('/map', 'map')],
    )

    return LaunchDescription([declare_namespace, declare_map_arg, map_server_node])
