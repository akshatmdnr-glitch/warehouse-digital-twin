"""Launch Pure Pursuit path-following controller."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    config = PathJoinSubstitution([
        get_package_share_directory('warehouse_bringup'),
        'config',
        'controller.yaml',
    ])

    controller_node = Node(
        package='warehouse_bringup',
        executable='controller_node',
        name='controller',
        output='screen',
        parameters=[config],
    )

    return LaunchDescription([controller_node])
