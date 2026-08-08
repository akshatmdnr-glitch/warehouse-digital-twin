"""Launch the warehouse order-fulfillment visualization.

Renders the whole order workflow (package selection, highlights, robot labels,
paths, pickup/delivery animation, camera presets) inside the Gazebo world by
observing the existing ROS topics.  Requires the Gazebo stack (it spawns
visual entities via the gz transport), so it is included only in the
multi-robot Gazebo launch.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='warehouse_bringup',
            executable='visualization_node',
            name='warehouse_visualization',
            output='screen',
            parameters=[{
                'world': 'warehouse_world',
                'auto_follow': True,
                'show_paths': True,
            }],
        ),
    ])
