"""Launch obstacle avoidance monitor (namespaced per robot)."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    config = PathJoinSubstitution([
        get_package_share_directory('warehouse_bringup'),
        'config',
        'obstacle_monitor.yaml',
    ])

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this monitor (empty = root)')

    node = Node(
        package='warehouse_bringup',
        executable='obstacle_monitor_node',
        name='obstacle_monitor',
        namespace=namespace,
        output='screen',
        parameters=[config],
        remappings=[
            ('/scan', 'scan'),
            ('/controller_cmd_vel', 'controller_cmd_vel'),
            ('/cmd_vel', 'cmd_vel'),
        ],
    )

    return LaunchDescription([declare_namespace, node])
