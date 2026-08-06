"""Launch the ROS -> Backend ingest bridge.

Forwards existing warehouse topics to the production backend and relays
backend-created tasks into ROS. The backend service is launched separately
(see backend/).

Usage:
    ros2 launch warehouse_bringup backend_ingest.launch.py \
        backend_url:=http://localhost:8090 bridge_username:=admin \
        bridge_password:=admin
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    url = LaunchConfiguration('backend_url')
    period = LaunchConfiguration('ingest_period')
    poll = LaunchConfiguration('poll_period')
    user = LaunchConfiguration('bridge_username')
    password = LaunchConfiguration('bridge_password')

    declare_args = [
        DeclareLaunchArgument('backend_url', default_value='http://localhost:8090',
                              description='Backend base URL'),
        DeclareLaunchArgument('ingest_period', default_value='1.0',
                              description='Seconds between ingest batches'),
        DeclareLaunchArgument('poll_period', default_value='1.0',
                              description='Seconds between task-relay polls'),
        DeclareLaunchArgument('bridge_username', default_value='admin',
                              description='Backend user for the ingest token'),
        DeclareLaunchArgument('bridge_password', default_value='admin',
                              description='Password for the ingest token'),
    ]

    node = Node(
        package='warehouse_bringup',
        executable='backend_ingest_node',
        name='backend_ingest',
        output='screen',
        parameters=[{
            'backend_url': url,
            'ingest_period': period,
            'poll_period': poll,
            'bridge_username': user,
            'bridge_password': password,
        }],
    )

    return LaunchDescription(declare_args + [node])
