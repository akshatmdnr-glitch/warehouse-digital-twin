"""Launch the Fleet Manager and optionally a default robot status beacon.

The Fleet Manager is central (one per fleet). The robot status beacon is
per-robot; this launch provides one for the default single-robot twin.
Additional robots launch their own beacon with beacon.launch.py.

Usage:
    ros2 launch warehouse_bringup fleet_manager.launch.py
    ros2 launch warehouse_bringup fleet_manager.launch.py heartbeat_timeout:=5.0
    ros2 launch warehouse_bringup fleet_manager.launch.py with_beacon:=false
    ros2 launch warehouse_bringup fleet_manager.launch.py score_w_distance:=2.0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    timeout = LaunchConfiguration('heartbeat_timeout')
    with_beacon = LaunchConfiguration('with_beacon', default='true')
    low_battery = LaunchConfiguration('low_battery_threshold')
    critical_battery = LaunchConfiguration('critical_battery_threshold')
    w_dist = LaunchConfiguration('score_w_distance')
    w_queue = LaunchConfiguration('score_w_queue')
    w_battery = LaunchConfiguration('score_w_battery')
    w_current = LaunchConfiguration('score_w_current')
    w_eta = LaunchConfiguration('score_w_eta')

    declare_args = [
        DeclareLaunchArgument(
            'heartbeat_timeout', default_value='3.0',
            description='Seconds without a heartbeat before a robot is marked OFFLINE'),
        DeclareLaunchArgument(
            'with_beacon', default_value='true',
            description='Launch a default robot status beacon for robot tb3_1'),
        DeclareLaunchArgument(
            'low_battery_threshold', default_value='30.0',
            description='Battery % below which a robot takes no new work'),
        DeclareLaunchArgument(
            'critical_battery_threshold', default_value='15.0',
            description='Battery % that forces a robot to charge (releases its task)'),
        DeclareLaunchArgument(
            'score_w_distance', default_value='1.0',
            description='Dispatch scoring weight for distance to pickup'),
        DeclareLaunchArgument(
            'score_w_queue', default_value='1.0',
            description='Dispatch scoring weight per queued task'),
        DeclareLaunchArgument(
            'score_w_battery', default_value='1.0',
            description='Dispatch scoring weight for low battery (per % below 100)'),
        DeclareLaunchArgument(
            'score_w_current', default_value='10.0',
            description='Dispatch scoring penalty for a robot already executing'),
        DeclareLaunchArgument(
            'score_w_eta', default_value='1.0',
            description='Dispatch scoring weight for finish-time (ETA)'),
    ]

    fleet_manager = Node(
        package='warehouse_bringup',
        executable='fleet_manager_node',
        name='fleet_manager',
        output='screen',
        parameters=[{
            'heartbeat_timeout': timeout,
            'low_battery_threshold': low_battery,
            'critical_battery_threshold': critical_battery,
            'score_w_distance': w_dist,
            'score_w_queue': w_queue,
            'score_w_battery': w_battery,
            'score_w_current': w_current,
            'score_w_eta': w_eta,
        }],
    )

    robot_beacon = Node(
        package='warehouse_bringup',
        executable='robot_status_publisher',
        name='robot_status_publisher',
        output='screen',
        parameters=[{'robot_id': 'tb3_1'}],
        condition=IfCondition(with_beacon),
    )

    return LaunchDescription(declare_args + [fleet_manager, robot_beacon])

