"""Load the warehouse map inside a robot namespace.

The warehouse pose pipeline uses ONE authoritative pose source:
sim_localization_node bridges /ns/odom -> /ns/amcl_pose (with the spawn
offset) and publishes the matching map->odom transform on /ns/tf. This launch
provides the map server the planner needs.

Usage:
    ros2 launch warehouse_bringup localization.launch.py
    ros2 launch warehouse_bringup localization.launch.py map:=my_map.yaml
    ros2 launch warehouse_bringup localization.launch.py namespace:=robot1
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    namespace = LaunchConfiguration('namespace')
    map_file = LaunchConfiguration('map', default='warehouse_map.yaml')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this robot (empty = root)')

    map_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/load_map.launch.py']),
        launch_arguments={
            'map': map_file,
            'namespace': namespace,
        }.items(),
    )

    # The warehouse pose pipeline uses ONE authoritative pose source:
    # sim_localization_node bridges /ns/odom -> /ns/amcl_pose (with the spawn
    # offset) and publishes the matching map->odom transform on /ns/tf. The
    # legacy scan-based AMCL node is intentionally NOT launched here — it was
    # a second, disagreeing publisher of /ns/amcl_pose (uniform particle init
    # with no /initialpose and no scan likelihood) that desynchronized every
    # consumer. The map server above is still required by the planner.

    return LaunchDescription([declare_namespace, map_launch])
