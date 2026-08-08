"""Spawn a TurtleBot3 robot into a running Gazebo simulation.

Launch arguments:
    namespace — ROS namespace for this robot's bridge and TF (default '')
    robot_name — Gazebo model name (default 'burger'; use unique names
                 e.g. burger_1 / burger_2 for multiple robots)
    spawn_x, spawn_y, spawn_z — spawn position in meters
                                (default: 0.0, 0.0, 0.01)
    spawn_roll, spawn_pitch, spawn_yaw — spawn orientation in radians
                                (default: 0.0, 0.0, 0.0)

Spawn args are prefixed with 'spawn_' so they never collide with the goal
pose args (x, y, yaw) used by the navigation launch in a composed tree.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (AppendEnvironmentVariable, DeclareLaunchArgument,
                            SetEnvironmentVariable)
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, TextSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    tb3_model = os.environ.get('TURTLEBOT3_MODEL', 'burger')
    model_folder = 'turtlebot3_' + tb3_model

    set_tb3_model = SetEnvironmentVariable('TURTLEBOT3_MODEL', tb3_model)

    set_gz_resource = AppendEnvironmentVariable(
        'GZ_SIM_RESOURCE_PATH',
        get_package_share_directory('turtlebot3_gazebo') + '/models',
    )

    # ---- Namespace / model arguments ----
    namespace = LaunchConfiguration('namespace')
    robot_name = LaunchConfiguration('robot_name', default=tb3_model)
    model_variant = LaunchConfiguration(
        'model_variant', default='turtlebot3_' + tb3_model)

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this robot (empty = root)')
    declare_robot_name = DeclareLaunchArgument(
        'robot_name', default_value=tb3_model,
        description='Gazebo model name (must be unique per robot)')
    declare_model_variant = DeclareLaunchArgument(
        'model_variant', default_value='turtlebot3_' + tb3_model,
        description='Package model directory (unique gz topics per robot)')
    declare_bridge_config = DeclareLaunchArgument(
        'bridge_config', default_value='turtlebot3_burger_bridge.yaml',
        description='ROS-Gazebo bridge config in the package config/ dir')

    # ---- Spawn pose arguments ----
    declare_x = DeclareLaunchArgument('spawn_x', default_value='0.0',
                                      description='Spawn X position (m)')
    declare_y = DeclareLaunchArgument('spawn_y', default_value='0.0',
                                      description='Spawn Y position (m)')
    declare_z = DeclareLaunchArgument('spawn_z', default_value='0.01',
                                      description='Spawn Z position (m)')
    declare_roll = DeclareLaunchArgument('spawn_roll', default_value='0.0',
                                         description='Spawn roll angle (rad)')
    declare_pitch = DeclareLaunchArgument('spawn_pitch', default_value='0.0',
                                          description='Spawn pitch angle (rad)')
    declare_yaw = DeclareLaunchArgument('spawn_yaw', default_value='0.0',
                                        description='Spawn yaw angle (rad)')

    x = LaunchConfiguration('spawn_x')
    y = LaunchConfiguration('spawn_y')
    z = LaunchConfiguration('spawn_z')
    roll = LaunchConfiguration('spawn_roll')
    pitch = LaunchConfiguration('spawn_pitch')
    yaw = LaunchConfiguration('spawn_yaw')

    # ---- Build model SDF path (prefer the package-local model so the CPU
    # lidar variant is used; falls back to turtlebot3_gazebo) ----
    sdf_path = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'models',
        model_variant,
        'model.sdf',
    ])

    # ---- Spawn via ros_gz_sim create ----
    spawn_node = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-name', robot_name,
            '-file', sdf_path,
            '-x', x,
            '-y', y,
            '-z', z,
            '-R', roll,
            '-P', pitch,
            '-Y', yaw,
        ],
        output='screen',
    )

    # ---- ROS-Gazebo bridge (unique gz topics per robot model variant) ----
    bridge_config = PathJoinSubstitution([
        FindPackageShare('warehouse_bringup'),
        'config',
        LaunchConfiguration(
            'bridge_config',
            default='turtlebot3_burger_bridge.yaml'),
    ])

    bridge_node = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        namespace=namespace,
        arguments=[
            '--ros-args',
            '-p',
            [TextSubstitution(text='config_file:='), bridge_config],
        ],
        output='screen',
    )

    # ---- robot_state_publisher (namespaced → /ns/tf, /ns/joint_states) ----
    tb3_rsp = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        namespace=namespace,
        output='screen',
        parameters=[{
            'use_sim_time': True,
            'robot_description': _load_urdf(tb3_model),
        }],
    )

    return LaunchDescription([
        set_tb3_model,
        set_gz_resource,
        declare_namespace,
        declare_robot_name,
        declare_model_variant,
        declare_bridge_config,
        declare_x,
        declare_y,
        declare_z,
        declare_roll,
        declare_pitch,
        declare_yaw,
        spawn_node,
        bridge_node,
        tb3_rsp,
    ])


def _load_urdf(model_name):
    urdf_file = 'turtlebot3_' + model_name + '.urdf'
    urdf_path = os.path.join(
        get_package_share_directory('turtlebot3_gazebo'),
        'urdf',
        urdf_file,
    )
    with open(urdf_path, 'r') as f:
        return f.read()
