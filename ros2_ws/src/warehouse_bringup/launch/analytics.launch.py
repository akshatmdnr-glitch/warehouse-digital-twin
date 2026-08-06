"""Launch the warehouse analytics observer (independent of robot control).

Usage:
    ros2 launch warehouse_bringup analytics.launch.py
    ros2 launch warehouse_bringup analytics.launch.py publish_period:=5.0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    period = LaunchConfiguration('publish_period')
    window = LaunchConfiguration('rolling_window')

    declare_args = [
        DeclareLaunchArgument('publish_period', default_value='2.0',
                              description='Seconds between /analytics summaries'),
        DeclareLaunchArgument('rolling_window', default_value='20',
                              description='Number of samples kept for rolling averages'),
    ]

    analytics = Node(
        package='warehouse_bringup',
        executable='analytics_node',
        name='analytics',
        output='screen',
        parameters=[{'publish_period': period, 'rolling_window': window}],
    )

    return LaunchDescription(declare_args + [analytics])
