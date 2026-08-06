"""Launch Gazebo with a world, spawn a TurtleBot3 robot, and start SLAM.

Usage:
    ros2 launch warehouse_bringup simulation.launch.py
    ros2 launch warehouse_bringup simulation.launch.py world:=warehouse.world.sdf
    ros2 launch warehouse_bringup simulation.launch.py spawn_x:=2.0 spawn_y:=1.5 spawn_yaw:=1.57
"""

from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/gazebo.launch.py']),
        launch_arguments={
            'world': LaunchConfiguration('world', default='warehouse_empty.sdf'),
        }.items(),
    )

    robot_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/spawn_robot.launch.py']),
        launch_arguments={
            'spawn_x': LaunchConfiguration('spawn_x', default='0.0'),
            'spawn_y': LaunchConfiguration('spawn_y', default='0.0'),
            'spawn_z': LaunchConfiguration('spawn_z', default='0.01'),
            'spawn_roll': LaunchConfiguration('spawn_roll', default='0.0'),
            'spawn_pitch': LaunchConfiguration('spawn_pitch', default='0.0'),
            'spawn_yaw': LaunchConfiguration('spawn_yaw', default='0.0'),
        }.items(),
    )

    slam_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/slam.launch.py']),
    )

    rviz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/rviz.launch.py']),
    )

    return LaunchDescription([gz_launch, robot_launch, slam_launch, rviz_launch])
