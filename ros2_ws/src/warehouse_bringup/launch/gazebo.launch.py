"""Launch Gazebo Harmonic with the warehouse empty world."""

from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution, TextSubstitution
from ament_index_python.packages import get_package_share_directory
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():

    world_path = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'worlds',
        'warehouse_empty.sdf',
    ])

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            get_package_share_directory('ros_gz_sim'),
            '/launch/gz_sim.launch.py',
        ]),
        launch_arguments={
            'gz_args': [TextSubstitution(text='-r '), world_path],
            'on_exit_shutdown': 'true',
        }.items(),
    )

    return LaunchDescription([gz_launch])
