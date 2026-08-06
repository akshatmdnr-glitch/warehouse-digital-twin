"""Launch slam_toolbox in online async mapping mode with lifecycle activation."""

from launch import LaunchDescription
from launch.actions import ExecuteProcess, TimerAction
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import LifecycleNode
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    slam_config = PathJoinSubstitution([
        get_package_share_directory('warehouse_bringup'),
        'config',
        'slam_toolbox_mapping.yaml',
    ])

    slam_node = LifecycleNode(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        namespace='',
        output='screen',
        parameters=[slam_config, {'use_sim_time': True}],
    )

    lifecycle_cmd = ExecuteProcess(
        cmd=[
            'bash', '-c',
            'sleep 2 && '
            'ros2 lifecycle set /slam_toolbox configure && '
            'sleep 3 && '
            'ros2 lifecycle set /slam_toolbox activate',
        ],
        output='screen',
    )

    return LaunchDescription([
        slam_node,
        lifecycle_cmd,
    ])
