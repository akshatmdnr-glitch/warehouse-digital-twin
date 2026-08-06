"""Launch AMCL localization using a saved map.

Usage:
    ros2 launch warehouse_bringup localization.launch.py
    ros2 launch warehouse_bringup localization.launch.py map:=my_map.yaml
    ros2 launch warehouse_bringup localization.launch.py namespace:=robot1
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
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

    amcl_config = PathJoinSubstitution([
        pkg_dir, 'config', 'amcl.yaml',
    ])

    # Namespaced AMCL: subscribes /ns/map, /ns/scan, /ns/tf and publishes
    # /ns/amcl_pose plus the map→odom transform on /ns/tf.
    amcl_node = Node(
        package='warehouse_bringup',
        executable='amcl_node',
        name='amcl',
        namespace=namespace,
        output='screen',
        parameters=[amcl_config],
        remappings=[
            ('/map', 'map'),
            ('/scan', 'scan'),
            ('/amcl_pose', 'amcl_pose'),
            ('/initialpose', 'initialpose'),
        ],
    )

    return LaunchDescription([declare_namespace, map_launch, amcl_node])
