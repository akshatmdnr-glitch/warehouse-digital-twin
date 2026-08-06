"""Launch the real-time warehouse web dashboard (read-only observer).

Usage:
    ros2 launch warehouse_bringup dashboard.launch.py
    ros2 launch warehouse_bringup dashboard.launch.py port:=8080
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    port = LaunchConfiguration('port')
    refresh = LaunchConfiguration('refresh_interval')

    declare_args = [
        DeclareLaunchArgument('port', default_value='8080',
                              description='HTTP port for the dashboard'),
        DeclareLaunchArgument('refresh_interval', default_value='1.0',
                              description='Seconds between dashboard refreshes'),
    ]

    dashboard = Node(
        package='warehouse_bringup',
        executable='dashboard_node',
        name='dashboard',
        output='screen',
        parameters=[{'port': port, 'refresh_interval': refresh}],
    )

    return LaunchDescription(declare_args + [dashboard])
