"""Launch one full robot stack inside a namespace.

Composes the modular bringup files for a single robot (no node duplication):
  spawn_robot.launch.py  — Gazebo model + bridge + robot_state_publisher
  navigation.launch.py   — localization (AMCL) + planner + controller +
                           obstacle avoidance + task manager
  beacon.launch.py       — fleet registration/heartbeat/pose beacon

Usage:
    ros2 launch warehouse_bringup robot.launch.py \
        namespace:=robot1 robot_name:=burger_1 robot_id:=robot1 \
        spawn_x:=1.0 spawn_y:=0.0 x:=4.0 y:=0.0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    namespace = LaunchConfiguration('namespace')
    robot_name = LaunchConfiguration('robot_name', default='burger')
    robot_id = LaunchConfiguration('robot_id', default='tb3_1')
    map_file = LaunchConfiguration('map', default='warehouse_map.yaml')
    model_variant = LaunchConfiguration('model_variant',
                                        default='turtlebot3_burger')
    bridge_config = LaunchConfiguration('bridge_config',
                                        default='turtlebot3_burger_bridge.yaml')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this robot (empty = root)')
    declare_robot_name = DeclareLaunchArgument(
        'robot_name', default_value='burger',
        description='Gazebo model name (must be unique per robot)')
    declare_robot_id = DeclareLaunchArgument(
        'robot_id', default_value='tb3_1',
        description='Robot id registered with the fleet')
    declare_model_variant = DeclareLaunchArgument(
        'model_variant', default_value='turtlebot3_burger',
        description='Model dir (unique gz topics) for this robot')
    declare_bridge_config = DeclareLaunchArgument(
        'bridge_config', default_value='turtlebot3_burger_bridge.yaml',
        description='Bridge config file in the package config/ dir')

    # Spawn: model + bridge + TF (all in the robot's namespace)
    spawn_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/spawn_robot.launch.py']),
        launch_arguments={
            'namespace': namespace,
            'robot_name': robot_name,
            'model_variant': model_variant,
            'bridge_config': bridge_config,
            'spawn_x': LaunchConfiguration('spawn_x', default='0.0'),
            'spawn_y': LaunchConfiguration('spawn_y', default='0.0'),
            'spawn_z': LaunchConfiguration('spawn_z', default='0.01'),
            'spawn_roll': LaunchConfiguration('spawn_roll', default='0.0'),
            'spawn_pitch': LaunchConfiguration('spawn_pitch', default='0.0'),
            'spawn_yaw': LaunchConfiguration('spawn_yaw', default='0.0'),
        }.items(),
    )

    # Navigation: localization + planner + controller + obstacle + task manager
    nav_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/navigation.launch.py']),
        launch_arguments={
            'namespace': namespace,
            'robot_id': robot_id,
            'map': map_file,
            'x': LaunchConfiguration('x', default='0.0'),
            'y': LaunchConfiguration('y', default='0.0'),
            'yaw': LaunchConfiguration('yaw', default='0.0'),
        }.items(),
    )

    # Fleet beacon: registers this robot with the global fleet manager
    beacon_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/beacon.launch.py']),
        launch_arguments={
            'namespace': namespace,
            'robot_id': robot_id,
            'initial_battery': LaunchConfiguration('initial_battery', default='100.0'),
            'discharge_rate': LaunchConfiguration('discharge_rate', default='1.0'),
            'charge_rate': LaunchConfiguration('charge_rate', default='10.0'),
            'critical_battery_threshold': LaunchConfiguration(
                'critical_battery_threshold', default='15.0'),
            'charge_complete_threshold': LaunchConfiguration(
                'charge_complete_threshold', default='100.0'),
            'low_battery_threshold': LaunchConfiguration(
                'low_battery_threshold', default='30.0'),
            'charging_station_x': LaunchConfiguration('charging_station_x', default='0.0'),
            'charging_station_y': LaunchConfiguration('charging_station_y', default='8.0'),
        }.items(),
    )

    return LaunchDescription([
        declare_namespace,
        declare_robot_name,
        declare_robot_id,
        declare_model_variant,
        declare_bridge_config,
        spawn_launch,
        nav_launch,
        beacon_launch,
    ])
