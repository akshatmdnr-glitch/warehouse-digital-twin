"""Launch the complete Warehouse Digital Twin — one command.

Multi-robot mode (robot_count:=2, default) composes:
  gazebo.launch.py       — Gazebo world (once)
  robot.launch.py ×2     — robot1/robot2: spawn + navigation + fleet beacon,
                           each in its own namespace with unique TF/topics
  fleet_manager.launch.py— fleet registry, heartbeats, reservations,
                           recovery, scoring dispatch, monitoring (with_beacon:=false)
  rviz.launch.py         — shared RViz

Single-robot mode (robot_count:=1) reproduces the original stack exactly:
  simulation.launch.py + navigation.launch.py + fleet_manager.launch.py.

Usage:
    ros2 launch warehouse_bringup warehouse.launch.py
    ros2 launch warehouse_bringup warehouse.launch.py robot_count:=1
    ros2 launch warehouse_bringup warehouse.launch.py world:=warehouse.world.sdf
    ros2 launch warehouse_bringup warehouse.launch.py \
        spawn_x:=1.0 spawn_y:=1.0 spawn_yaw:=0.0 \
        spawn_x2:=-1.0 spawn_y2:=1.0 spawn_yaw2:=3.14
    ros2 launch warehouse_bringup warehouse.launch.py x:=4.0 y:=2.0 yaw:=0.0
    ros2 launch warehouse_bringup warehouse.launch.py heartbeat_timeout:=5.0 score_w_distance:=2.0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    world = LaunchConfiguration('world')
    map_file = LaunchConfiguration('map')
    robot_count = LaunchConfiguration('robot_count')
    goal_x = LaunchConfiguration('x')
    goal_y = LaunchConfiguration('y')
    goal_yaw = LaunchConfiguration('yaw')
    goal_x2 = LaunchConfiguration('x2')
    goal_y2 = LaunchConfiguration('y2')
    goal_yaw2 = LaunchConfiguration('yaw2')
    spawn_x = LaunchConfiguration('spawn_x')
    spawn_y = LaunchConfiguration('spawn_y')
    spawn_yaw = LaunchConfiguration('spawn_yaw')
    spawn_x2 = LaunchConfiguration('spawn_x2')
    spawn_y2 = LaunchConfiguration('spawn_y2')
    spawn_yaw2 = LaunchConfiguration('spawn_yaw2')
    timeout = LaunchConfiguration('heartbeat_timeout')
    w_dist = LaunchConfiguration('score_w_distance')
    w_load = LaunchConfiguration('score_w_workload')
    w_prio = LaunchConfiguration('score_w_priority')
    w_cap = LaunchConfiguration('score_w_capability')
    low_battery = LaunchConfiguration('low_battery_threshold')
    critical_battery = LaunchConfiguration('critical_battery_threshold')
    initial_battery = LaunchConfiguration('initial_battery')
    discharge_rate = LaunchConfiguration('discharge_rate')
    charge_rate = LaunchConfiguration('charge_rate')
    station_x = LaunchConfiguration('charging_station_x')
    station_y = LaunchConfiguration('charging_station_y')
    station_x2 = LaunchConfiguration('charging_station_x2')
    station_y2 = LaunchConfiguration('charging_station_y2')
    analytics_period = LaunchConfiguration('analytics_period')
    dashboard_port = LaunchConfiguration('dashboard_port')
    control_center_port = LaunchConfiguration('control_center_port')
    control_center_ws = LaunchConfiguration('control_center_ws')
    backend_url = LaunchConfiguration('backend_url')

    # Boolean strings for conditional includes (quote the value so the
    # Python expression compares two strings: "'2' == '2'").
    is_single = PythonExpression(["'", robot_count, "' == '1' and 'true' or 'false'"])
    is_multi = PythonExpression(["'", robot_count, "' == '2' and 'true' or 'false'"])
    fleet_beacon = PythonExpression(["'", robot_count, "' == '1' and 'true' or 'false'"])

    declare_args = [
        DeclareLaunchArgument('robot_count', default_value='2',
                              description='Number of robots to launch (1 or 2)'),
        DeclareLaunchArgument('world', default_value='warehouse_empty.sdf',
                              description='World file to load from the worlds/ directory'),
        DeclareLaunchArgument('map', default_value='warehouse_map.yaml',
                              description='Map YAML file to load from the maps/ directory'),
        DeclareLaunchArgument('x', default_value='0.0', description='Robot 1 goal X (m)'),
        DeclareLaunchArgument('y', default_value='0.0', description='Robot 1 goal Y (m)'),
        DeclareLaunchArgument('yaw', default_value='0.0', description='Robot 1 goal yaw (rad)'),
        DeclareLaunchArgument('x2', default_value='0.0', description='Robot 2 goal X (m)'),
        DeclareLaunchArgument('y2', default_value='0.0', description='Robot 2 goal Y (m)'),
        DeclareLaunchArgument('yaw2', default_value='0.0', description='Robot 2 goal yaw (rad)'),
        DeclareLaunchArgument('spawn_x', default_value='0.0', description='Robot 1 spawn X (m)'),
        DeclareLaunchArgument('spawn_y', default_value='0.0', description='Robot 1 spawn Y (m)'),
        DeclareLaunchArgument('spawn_yaw', default_value='0.0', description='Robot 1 spawn yaw (rad)'),
        DeclareLaunchArgument('spawn_x2', default_value='0.0', description='Robot 2 spawn X (m)'),
        DeclareLaunchArgument('spawn_y2', default_value='0.0', description='Robot 2 spawn Y (m)'),
        DeclareLaunchArgument('spawn_yaw2', default_value='0.0', description='Robot 2 spawn yaw (rad)'),
        DeclareLaunchArgument('heartbeat_timeout', default_value='3.0',
                              description='Fleet heartbeat timeout (s)'),
        DeclareLaunchArgument('score_w_distance', default_value='1.0',
                              description='Dispatch scoring weight for distance'),
        DeclareLaunchArgument('score_w_workload', default_value='1.0',
                              description='Dispatch scoring weight for workload'),
        DeclareLaunchArgument('score_w_priority', default_value='1.0',
                              description='Dispatch scoring weight for priority'),
        DeclareLaunchArgument('score_w_capability', default_value='1.0',
                              description='Dispatch scoring weight for capability'),
        DeclareLaunchArgument('low_battery_threshold', default_value='30.0',
                              description='Battery % below which a robot takes no new work'),
        DeclareLaunchArgument('critical_battery_threshold', default_value='15.0',
                              description='Battery % that forces a robot to charge'),
        DeclareLaunchArgument('initial_battery', default_value='100.0',
                              description='Starting battery % for every robot'),
        DeclareLaunchArgument('discharge_rate', default_value='1.0',
                              description='Battery drain % per second while executing'),
        DeclareLaunchArgument('charge_rate', default_value='10.0',
                              description='Battery charge % per second'),
        DeclareLaunchArgument('charging_station_x', default_value='0.0',
                              description='Robot 1 charging station X (m)'),
        DeclareLaunchArgument('charging_station_y', default_value='5.0',
                              description='Robot 1 charging station Y (m)'),
        DeclareLaunchArgument('charging_station_x2', default_value='0.0',
                              description='Robot 2 charging station X (m)'),
        DeclareLaunchArgument('charging_station_y2', default_value='-5.0',
                              description='Robot 2 charging station Y (m)'),
        DeclareLaunchArgument('analytics_period', default_value='2.0',
                              description='Seconds between /analytics summaries'),
        DeclareLaunchArgument('dashboard_port', default_value='8080',
                              description='HTTP port for the web dashboard'),
        DeclareLaunchArgument('control_center_port', default_value='8081',
                              description='HTTP port for the Control Center UI/REST'),
        DeclareLaunchArgument('control_center_ws', default_value='8082',
                              description='WebSocket port for Control Center live push'),
        DeclareLaunchArgument('backend_url', default_value='http://localhost:8090',
                              description='Production backend URL ('' = disable ingest)'),
    ]

    # ---- Single-robot mode (original stack, backward compatible) ----
    sim_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/simulation.launch.py']),
        launch_arguments={
            'world': world,
            'spawn_x': spawn_x,
            'spawn_y': spawn_y,
            'spawn_yaw': spawn_yaw,
        }.items(),
        condition=IfCondition(is_single),
    )

    nav_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/navigation.launch.py']),
        launch_arguments={
            'map': map_file,
            'x': goal_x,
            'y': goal_y,
            'yaw': goal_yaw,
        }.items(),
        condition=IfCondition(is_single),
    )

    # ---- Multi-robot mode (shared world + two namespaced robots) ----
    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/gazebo.launch.py']),
        launch_arguments={'world': world}.items(),
        condition=IfCondition(is_multi),
    )

    robot1 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/robot.launch.py']),
        launch_arguments={
            'namespace': 'robot1',
            'robot_name': 'burger_1',
            'robot_id': 'robot1',
            'map': map_file,
            'x': goal_x,
            'y': goal_y,
            'yaw': goal_yaw,
            'spawn_x': spawn_x,
            'spawn_y': spawn_y,
            'spawn_yaw': spawn_yaw,
            'initial_battery': initial_battery,
            'discharge_rate': discharge_rate,
            'charge_rate': charge_rate,
            'charging_station_x': station_x,
            'charging_station_y': station_y,
        }.items(),
        condition=IfCondition(is_multi),
    )

    robot2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/robot.launch.py']),
        launch_arguments={
            'namespace': 'robot2',
            'robot_name': 'burger_2',
            'robot_id': 'robot2',
            'map': map_file,
            'x': goal_x2,
            'y': goal_y2,
            'yaw': goal_yaw2,
            'spawn_x': spawn_x2,
            'spawn_y': spawn_y2,
            'spawn_yaw': spawn_yaw2,
            'initial_battery': initial_battery,
            'discharge_rate': discharge_rate,
            'charge_rate': charge_rate,
            'charging_station_x': station_x2,
            'charging_station_y': station_y2,
        }.items(),
        condition=IfCondition(is_multi),
    )

    # Shared RViz (multi-robot mode only; single mode gets it via simulation)
    rviz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/rviz.launch.py']),
        condition=IfCondition(is_multi),
    )

    # Warehouse analytics (independent observer; runs in both modes)
    analytics_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/analytics.launch.py']),
        launch_arguments={'publish_period': analytics_period}.items(),
    )

    # Real-time web dashboard (read-only observer; runs in both modes)
    dashboard_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/dashboard.launch.py']),
        launch_arguments={'port': dashboard_port}.items(),
    )

    # Warehouse Control Center (operator UI + backend bridge; runs in both modes)
    control_center_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/control_center.launch.py']),
        launch_arguments={
            'http_port': control_center_port,
            'ws_port': control_center_ws,
        }.items(),
    )

    # ROS -> Backend ingest bridge (enabled when backend_url is set)
    enable_ingest = PythonExpression(
        ["'", backend_url, "' != '' and 'true' or 'false'"])
    backend_ingest_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/backend_ingest.launch.py']),
        launch_arguments={'backend_url': backend_url}.items(),
        condition=IfCondition(enable_ingest),
    )

    # Fleet management + monitoring. Single mode keeps the default beacon;
    # multi mode launches one beacon per robot (from robot.launch.py).
    fleet_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/fleet_manager.launch.py']),
        launch_arguments={
            'with_beacon': fleet_beacon,
            'heartbeat_timeout': timeout,
            'low_battery_threshold': low_battery,
            'critical_battery_threshold': critical_battery,
            'score_w_distance': w_dist,
            'score_w_workload': w_load,
            'score_w_priority': w_prio,
            'score_w_capability': w_cap,
        }.items(),
    )

    return LaunchDescription(
        declare_args
        + [sim_launch, nav_single, gz_launch, robot1, robot2, rviz_launch,
           analytics_launch, dashboard_launch, control_center_launch,
           backend_ingest_launch, fleet_launch]
    )
