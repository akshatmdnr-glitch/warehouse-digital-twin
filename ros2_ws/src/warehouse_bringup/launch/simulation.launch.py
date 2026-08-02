"""Launch Gazebo with an empty world and spawn a TurtleBot3 robot."""

from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/gazebo.launch.py']),
    )

    robot_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/spawn_robot.launch.py']),
    )

    rviz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/rviz.launch.py']),
    )

    return LaunchDescription([gz_launch, robot_launch, rviz_launch])
