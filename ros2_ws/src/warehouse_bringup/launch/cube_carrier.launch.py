"""Launch the Phase 3/4 physical cube carrier for robot1.

Spawns one yellow cube that rigidly follows robot1's real Gazebo pose while
the robot is executing a pickup->delivery task, and drops it at the dropoff.
No visualization helpers are used.
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='warehouse_bringup',
            executable='cube_carrier_node',
            name='cube_carrier',
            output='screen',
            parameters=[{
                'world': 'warehouse_world',
                'robot': 'robot1',
                'carry_offset': '0.0,0.0,0.35',
            }],
        ),
    ])
