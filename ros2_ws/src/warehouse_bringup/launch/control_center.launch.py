"""Launch the Warehouse Control Center (backend bridge + web UI).

The Control Center is a read-only observer of the ROS2 warehouse graph. It
serves a web UI (HTTP + WebSocket), derives events/alerts/settings, and lets
an operator publish commands to existing control topics (/cmd_vel, /goal_pose,
/add_task, /cancel_task, /robot_heartbeat) plus the simulation-only beacon
topics (/battery_command, /control).

Usage:
    ros2 launch warehouse_bringup control_center.launch.py
    ros2 launch warehouse_bringup control_center.launch.py http_port:=8081 ws_port:=8082
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    http_port = LaunchConfiguration('http_port')
    ws_port = LaunchConfiguration('ws_port')
    push_period = LaunchConfiguration('push_period')
    events_max = LaunchConfiguration('events_max')

    declare_args = [
        DeclareLaunchArgument('http_port', default_value='8081',
                              description='HTTP port for the Control Center UI/REST'),
        DeclareLaunchArgument('ws_port', default_value='8082',
                              description='WebSocket port for live state push'),
        DeclareLaunchArgument('push_period', default_value='0.5',
                              description='Seconds between WebSocket state pushes'),
        DeclareLaunchArgument('events_max', default_value='500',
                              description='Maximum retained event log entries'),
    ]

    control_center = Node(
        package='warehouse_bringup',
        executable='control_center_node',
        name='control_center',
        output='screen',
        parameters=[{
            'http_port': http_port,
            'ws_port': ws_port,
            'push_period': push_period,
            'events_max': events_max,
        }],
    )

    return LaunchDescription(declare_args + [control_center])
