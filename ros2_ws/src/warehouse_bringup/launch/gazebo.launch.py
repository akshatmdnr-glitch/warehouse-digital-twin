"""Launch Gazebo Harmonic with a selected world file.

Adds the warehouse_bringup models directory to GZ_SIM_RESOURCE_PATH (so the
box-based warehouse models resolve via model:// URIs) and, when a GUI display
is available, loads the warehouse camera framing via --gui-config.
"""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (AppendEnvironmentVariable, DeclareLaunchArgument,
                            IncludeLaunchDescription)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (LaunchConfiguration, PathJoinSubstitution,
                                  TextSubstitution)
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():

    world_file = LaunchConfiguration('world', default='warehouse_empty.sdf')
    gui_config = LaunchConfiguration('gui_config',
                                     default='warehouse_gui.config')

    declare_world_arg = DeclareLaunchArgument(
        'world',
        default_value='warehouse_empty.sdf',
        description='World file to load from the worlds/ directory',
    )
    declare_gui_arg = DeclareLaunchArgument(
        'gui_config',
        default_value='warehouse_gui.config',
        description='GUI layout/camera file from the config/ directory',
    )

    # Make the package-local models (rack, pallet, ...) visible to Gazebo.
    resource_path = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH',
        PathJoinSubstitution([
            FindPackageShare('warehouse_bringup'),
            'models',
        ]),
    )

    world_path = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'worlds',
        world_file,
    ])

    gui_path = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'config',
        gui_config,
    ])

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            get_package_share_directory('ros_gz_sim'),
            '/launch/gz_sim.launch.py',
        ]),
        launch_arguments={
            'gz_args': [
                TextSubstitution(text='-r '),
                world_path,
                TextSubstitution(text=' --gui-config '),
                gui_path,
            ],
            'on_exit_shutdown': 'true',
        }.items(),
    )

    return LaunchDescription(
        [declare_world_arg, declare_gui_arg, resource_path, gz_launch]
    )
