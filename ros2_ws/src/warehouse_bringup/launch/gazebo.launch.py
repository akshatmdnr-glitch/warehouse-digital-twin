"""Launch Gazebo Harmonic with a selected world file."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, TextSubstitution
from ament_index_python.packages import get_package_share_directory
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():

    world_file = LaunchConfiguration('world', default='warehouse_empty.sdf')

    declare_world_arg = DeclareLaunchArgument(
        'world',
        default_value='warehouse_empty.sdf',
        description='World file to load from the worlds/ directory',
    )

    world_path = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'worlds',
        world_file,
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

    return LaunchDescription([declare_world_arg, gz_launch])
