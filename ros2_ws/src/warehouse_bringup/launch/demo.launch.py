"""Concurrent two-robot warehouse demo in Gazebo.

Launches the full fleet-driven demo:
  * Gazebo warehouse world
  * two TurtleBots, each with its own namespace / planner / controller /
    obstacle monitor / task manager / beacon / TF / cmd_vel
  * sim_localization (real Gazebo model pose as /ns/amcl_pose — single
    source of truth)
  * fleet manager (dispatches each submitted task to an idle robot, reserves
    route segments, waits on aisle conflicts, auto-retries)
  * two package carriers (robot1 carries P01, robot2 carries P25) — pickup
    and package attachment logic unchanged
  * demo mission: submits two tasks via /add_task; the fleet dispatcher
    assigns Task1 -> robot1 and Task2 -> robot2 so both run simultaneously
  * demo visualization: planned path (cyan/orange), PICKUP/DROPOFF markers,
    robot goal labels
  * web dashboard (read-only observer showing both robots ONLINE/Busy)

Usage:
    ros2 launch warehouse_bringup demo.launch.py
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    world = LaunchConfiguration('world', default='warehouse.world.sdf')
    map_file = LaunchConfiguration('map', default='warehouse_world.yaml')

    declare_world = DeclareLaunchArgument(
        'world', default_value='warehouse.world.sdf',
        description='World file to load from the worlds/ directory')
    declare_map = DeclareLaunchArgument(
        'map', default_value='warehouse_world.yaml',
        description='Map YAML file to load from the maps/ directory')

    # Gazebo world (once)
    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/gazebo.launch.py']),
        launch_arguments={'world': world}.items(),
    )

    # Robot 1 and Robot 2 — spawn + navigation, each in its own namespace.
    robot1 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/robot.launch.py']),
        launch_arguments={
            'namespace': 'robot1',
            'robot_name': 'burger_1',
            'robot_id': 'robot1',
            'model_variant': 'turtlebot3_burger',
            'bridge_config': 'turtlebot3_burger_bridge.yaml',
            'map': map_file,
            'x': '0.0', 'y': '5.0', 'yaw': '0.0',  # home goal
            'spawn_x': '0.0', 'spawn_y': '5.0', 'spawn_yaw': '-1.57',
            'initial_battery': '100.0', 'discharge_rate': '0.1',
            'charge_rate': '10.0', 'critical_battery_threshold': '5.0',
        }.items(),
    )

    robot2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/robot.launch.py']),
        launch_arguments={
            'namespace': 'robot2',
            'robot_name': 'burger_2',
            'robot_id': 'robot2',
            'model_variant': 'turtlebot3_burger2',
            'bridge_config': 'turtlebot3_burger2_bridge.yaml',
            'map': map_file,
            'x': '0.0', 'y': '-5.0', 'yaw': '0.0',  # home goal
            'spawn_x': '0.0', 'spawn_y': '-5.0', 'spawn_yaw': '1.57',
            'initial_battery': '100.0', 'discharge_rate': '0.1',
            'charge_rate': '10.0', 'critical_battery_threshold': '5.0',
        }.items(),
    )

    # Single source of truth: real Gazebo model pose -> /ns/amcl_pose.
    sim_localization = Node(
        package='warehouse_bringup',
        executable='sim_localization_node',
        name='sim_localization',
        output='screen',
        parameters=[{
            'world': 'warehouse_world',
            'model_names': '{"robot1": "burger_1", "robot2": "burger_2"}',
        }],
    )

    # Fleet dispatcher — routes /add_task to idle robots with per-robot
    # reservations, traffic lookahead, head-on serialization and auto-retry.
    # Beacons come from each robot.launch.py (with_beacon:=false here).
    fleet_manager = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/fleet_manager.launch.py']),
        launch_arguments={
            'with_beacon': 'false',
            'heartbeat_timeout': '3.0',
            'score_w_distance': '1.0',
            'score_w_queue': '1.0',
            'score_w_battery': '1.0',
            'score_w_current': '10.0',
            'score_w_eta': '1.0',
        }.items(),
    )

    # Two package carriers — one per robot, each claiming a disjoint slice of
    # the inventory so no model is spawned twice. Pickup/attach logic unchanged.
    package_carrier1 = Node(
        package='warehouse_bringup',
        executable='package_carrier_node',
        name='package_carrier_robot1',
        output='screen',
        parameters=[{
            'world': 'warehouse_world',
            'robot': 'robot1',
            'package_id': 'P01',
            'package_count': 6,
            'package_offset': 0,
        }],
    )

    package_carrier2 = Node(
        package='warehouse_bringup',
        executable='package_carrier_node',
        name='package_carrier_robot2',
        output='screen',
        parameters=[{
            'world': 'warehouse_world',
            'robot': 'robot2',
            'package_id': 'P13',
            'package_count': 6,
            'package_offset': 12,
        }],
    )

    # Demo visualization — paths, PICKUP/DROPOFF markers, robot goal labels.
    demo_visualization = Node(
        package='warehouse_bringup',
        executable='demo_visualization_node',
        name='demo_visualization',
        output='screen',
        parameters=[{
            'world': 'warehouse_world',
            'robots': 'robot1,robot2',
        }],
    )

    # Submit two tasks via the fleet dispatcher. Both dropoffs are permanent
    # logistics stations in the warehouse (no temporary drop pads):
    #   Task1 (robot1): pickup A1 rack front -> dropoff_station (2,-7)
    #   Task2 (robot2): pickup A3 rack front -> charging_north pad (0,8)
    # The two routes are ~5 m apart so both robots run truly concurrently
    # with no route conflicts. The fleet manager picks robot1 for Task1
    # (closest to A1) and robot2 for Task2 (robot1 already busy).
    demo_mission = Node(
        package='warehouse_bringup',
        executable='demo_mission_node',
        name='demo_mission',
        output='screen',
        parameters=[{
            'task1_id': 'DEMO01',
            'task1_pickup': '-4.0,2.2',    # A1 rack front (pickup)
            'task1_dropoff': '2.0,-7.0',   # dropoff_station (permanent)
            'task2_id': 'DEMO02',
            'task2_pickup': '4.0,2.2',     # A3 rack front (pickup)
            'task2_dropoff': '0.0,8.0',    # charging_north pad (permanent)
            'priority': 1,
            'start_delay': 6.0,
            'stagger': 1.5,
        }],
    )

    # Web dashboard (read-only observer of /fleet_status and /map) showing
    # both robots ONLINE / Busy with their own tasks.
    dashboard = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/dashboard.launch.py']),
        launch_arguments={'port': '8080'}.items(),
    )

    return LaunchDescription([
        declare_world, declare_map,
        gz_launch, robot1, robot2,
        sim_localization, fleet_manager,
        package_carrier1, package_carrier2,
        demo_visualization, demo_mission, dashboard,
    ])
