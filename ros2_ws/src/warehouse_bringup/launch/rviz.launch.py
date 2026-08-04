"""Launch RViz2 with the warehouse visualization configuration."""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    rviz_config = PathJoinSubstitution([
        get_package_share_directory('warehouse_bringup'),
        'rviz',
        'default.rviz',
    ])

    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        arguments=['-d', rviz_config],
        output='screen',
    )

    return LaunchDescription([rviz_node])
