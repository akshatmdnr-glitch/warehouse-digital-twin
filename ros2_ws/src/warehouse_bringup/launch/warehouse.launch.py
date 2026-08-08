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
from launch_ros.actions import Node
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
    w_queue = LaunchConfiguration('score_w_queue')
    w_battery = LaunchConfiguration('score_w_battery')
    w_current = LaunchConfiguration('score_w_current')
    w_eta = LaunchConfiguration('score_w_eta')
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
        DeclareLaunchArgument('world', default_value='warehouse.world.sdf',
                              description='World file to load from the worlds/ directory'),
        DeclareLaunchArgument('map', default_value='warehouse_world.yaml',
                              description='Map YAML file to load from the maps/ directory'),
        DeclareLaunchArgument('x', default_value='0.0', description='Robot 1 goal X (m)'),
        DeclareLaunchArgument('y', default_value='5.0', description='Robot 1 goal Y (m)'),
        DeclareLaunchArgument('yaw', default_value='0.0', description='Robot 1 goal yaw (rad)'),
        DeclareLaunchArgument('x2', default_value='0.0', description='Robot 2 goal X (m)'),
        DeclareLaunchArgument('y2', default_value='-5.0', description='Robot 2 goal Y (m)'),
        DeclareLaunchArgument('yaw2', default_value='0.0', description='Robot 2 goal yaw (rad)'),
        DeclareLaunchArgument('spawn_x', default_value='0.0', description='Robot 1 spawn X (m)'),
        DeclareLaunchArgument('spawn_y', default_value='5.0', description='Robot 1 spawn Y (m)'),
        DeclareLaunchArgument('spawn_yaw', default_value='-1.57', description='Robot 1 spawn yaw (rad)'),
        DeclareLaunchArgument('spawn_x2', default_value='0.0', description='Robot 2 spawn X (m)'),
        DeclareLaunchArgument('spawn_y2', default_value='-5.0', description='Robot 2 spawn Y (m)'),
        DeclareLaunchArgument('spawn_yaw2', default_value='1.57', description='Robot 2 spawn yaw (rad)'),
        DeclareLaunchArgument('heartbeat_timeout', default_value='3.0',
                              description='Fleet heartbeat timeout (s)'),
        DeclareLaunchArgument('score_w_distance', default_value='1.0',
                              description='Dispatch scoring weight for distance'),
        DeclareLaunchArgument('score_w_queue', default_value='1.0',
                              description='Dispatch scoring weight per queued task'),
        DeclareLaunchArgument('score_w_battery', default_value='1.0',
                              description='Dispatch scoring weight for low battery'),
        DeclareLaunchArgument('score_w_current', default_value='10.0',
                              description='Dispatch penalty for a robot already executing'),
        DeclareLaunchArgument('score_w_eta', default_value='1.0',
                              description='Dispatch scoring weight for finish-time (ETA)'),
        DeclareLaunchArgument('low_battery_threshold', default_value='30.0',
                              description='Battery % below which a robot takes no new work'),
        DeclareLaunchArgument('critical_battery_threshold', default_value='15.0',
                              description='Battery % that forces a robot to charge'),
        DeclareLaunchArgument('initial_battery', default_value='100.0',
                              description='Starting battery % for every robot'),
        DeclareLaunchArgument('discharge_rate', default_value='0.1',
                              description='Battery drain % per second while executing'),
        DeclareLaunchArgument('charge_rate', default_value='10.0',
                              description='Battery charge % per second'),
        DeclareLaunchArgument('charging_station_x', default_value='0.0',
                              description='Robot 1 charging station X (m)'),
        DeclareLaunchArgument('charging_station_y', default_value='8.0',
                              description='Robot 1 charging station Y (m)'),
        DeclareLaunchArgument('charging_station_x2', default_value='0.0',
                              description='Robot 2 charging station X (m)'),
        DeclareLaunchArgument('charging_station_y2', default_value='-8.0',
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
            'model_variant': 'turtlebot3_burger',
            'bridge_config': 'turtlebot3_burger_bridge.yaml',
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
            'model_variant': 'turtlebot3_burger2',
            'bridge_config': 'turtlebot3_burger2_bridge.yaml',
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

    # Phase 3/4: one physical yellow cube carried by robot1 (no visual helpers)
    cube_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/cube_carrier.launch.py']),
        condition=IfCondition(is_multi),
    )

    # Simulated localization: publishes each robot's ACTUAL Gazebo model pose
    # as /ns/amcl_pose (read from /world/.../pose/info). This is the ONE
    # source of truth — no odometry integration, so it can never drift from
    # the physical robot model.
    sim_localization_node = Node(
        package='warehouse_bringup',
        executable='sim_localization_node',
        name='sim_localization',
        output='screen',
        parameters=[{
            'world': 'warehouse_world',
            'model_names': '{"robot1": "burger_1", "robot2": "burger_2"}',
        }],
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
            'score_w_queue': w_queue,
            'score_w_battery': w_battery,
            'score_w_current': w_current,
            'score_w_eta': w_eta,
        }.items(),
    )

    return LaunchDescription(
        declare_args
        + [sim_launch, nav_single, gz_launch, robot1, robot2,
           analytics_launch, dashboard_launch, control_center_launch,
           sim_localization_node, cube_launch, backend_ingest_launch,
           fleet_launch]
    )
