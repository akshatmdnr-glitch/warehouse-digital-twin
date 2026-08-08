"""Launch a single robot status beacon inside a robot namespace.

The beacon publishes registration/heartbeat/pose on the GLOBAL fleet topics
(/robot_registration, /robot_heartbeat, /robot_pose) and reads its robot's
local state (/ns/task_state, /ns/queue_info, /ns/amcl_pose). It also simulates
battery: drains while executing, recharges at critical battery, and publishes
/ns/battery_status plus a charging-station goal on /ns/goal_pose.

The pose relay (/robot_pose) is published at 10 Hz so the backend, dashboard
and Control Center track the robot's live pose continuously.

Usage:
    ros2 launch warehouse_bringup beacon.launch.py namespace:=robot1 robot_id:=robot1
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    robot_id = LaunchConfiguration('robot_id', default='tb3_1')
    robot_type = LaunchConfiguration('robot_type', default='turtlebot3_burger')
    payload = LaunchConfiguration('payload_capacity', default='5.0')
    speed = LaunchConfiguration('max_speed', default='0.22')
    priority = LaunchConfiguration('priority', default='0.0')
    initial_battery = LaunchConfiguration('initial_battery', default='100.0')
    discharge = LaunchConfiguration('discharge_rate', default='1.0')
    charge = LaunchConfiguration('charge_rate', default='10.0')
    critical = LaunchConfiguration('critical_battery_threshold', default='15.0')
    complete = LaunchConfiguration('charge_complete_threshold', default='100.0')
    low = LaunchConfiguration('low_battery_threshold', default='30.0')
    station_x = LaunchConfiguration('charging_station_x', default='0.0')
    station_y = LaunchConfiguration('charging_station_y', default='5.0')

    declare_args = [
        DeclareLaunchArgument('namespace', default_value='',
                              description='ROS namespace for this robot'),
        DeclareLaunchArgument('robot_id', default_value='tb3_1',
                              description='Robot id registered with the fleet'),
        DeclareLaunchArgument('robot_type', default_value='turtlebot3_burger',
                              description='Robot type reported to the fleet'),
        DeclareLaunchArgument('payload_capacity', default_value='5.0',
                              description='Payload capacity in kg'),
        DeclareLaunchArgument('max_speed', default_value='0.22',
                              description='Max speed in m/s'),
        DeclareLaunchArgument('priority', default_value='0.0',
                              description='Dispatch priority (higher preferred)'),
        DeclareLaunchArgument('initial_battery', default_value='100.0',
                              description='Starting battery percentage'),
        DeclareLaunchArgument('discharge_rate', default_value='1.0',
                              description='Battery drain % per second while executing'),
        DeclareLaunchArgument('charge_rate', default_value='10.0',
                              description='Battery charge % per second'),
        DeclareLaunchArgument('critical_battery_threshold', default_value='15.0',
                              description='Battery % that triggers charging'),
        DeclareLaunchArgument('charge_complete_threshold', default_value='100.0',
                              description='Battery % considered fully charged'),
        DeclareLaunchArgument('low_battery_threshold', default_value='30.0',
                              description='Battery % below which the robot takes no new work'),
        DeclareLaunchArgument('charging_station_x', default_value='0.0',
                              description='Charging station X (m)'),
        DeclareLaunchArgument('charging_station_y', default_value='5.0',
                              description='Charging station Y (m)'),
    ]

    beacon = Node(
        package='warehouse_bringup',
        executable='robot_status_publisher',
        name='robot_status_publisher',
        namespace=namespace,
        output='screen',
        parameters=[{
            'robot_id': robot_id,
            'robot_type': robot_type,
            'payload_capacity': payload,
            'max_speed': speed,
            'priority': priority,
            'publish_rate': 10.0,
            'initial_battery': initial_battery,
            'discharge_rate': discharge,
            'charge_rate': charge,
            'critical_battery_threshold': critical,
            'charge_complete_threshold': complete,
            'low_battery_threshold': low,
            'charging_station_x': station_x,
            'charging_station_y': station_y,
        }],
        remappings=[
            # local robot state (namespaced)
            ('/task_state', 'task_state'),
            ('/queue_info', 'queue_info'),
            ('/amcl_pose', 'amcl_pose'),
            ('/odom', 'odom'),
            ('/plan', 'plan'),
            ('/battery_status', 'battery_status'),
            ('/goal_pose', 'goal_pose'),
            # publishers stay on the global fleet topics (absolute, unaffected)
        ],
    )

    return LaunchDescription(declare_args + [beacon])
