"""Spawn a TurtleBot3 robot into a running Gazebo simulation."""

from launch import LaunchDescription
from launch.actions import (AppendEnvironmentVariable, IncludeLaunchDescription,
                            SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    set_tb3_model = SetEnvironmentVariable('TURTLEBOT3_MODEL', 'burger')

    set_gz_resource = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH',
        get_package_share_directory('turtlebot3_gazebo') + '/models',
    )

    tb3_spawn = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            get_package_share_directory('turtlebot3_gazebo'),
            '/launch/spawn_turtlebot3.launch.py',
        ]),
        launch_arguments={
            'x_pose': LaunchConfiguration('x_pose', default='0.0'),
            'y_pose': LaunchConfiguration('y_pose', default='0.0'),
        }.items(),
    )

    tb3_rsp = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            get_package_share_directory('turtlebot3_gazebo'),
            '/launch/robot_state_publisher.launch.py',
        ]),
        launch_arguments={'use_sim_time': 'true'}.items(),
    )

    return LaunchDescription([
        set_tb3_model,
        set_gz_resource,
        tb3_spawn,
        tb3_rsp,
    ])
