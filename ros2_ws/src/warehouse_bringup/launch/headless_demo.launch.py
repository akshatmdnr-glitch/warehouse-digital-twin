"""Headless live demo — the full warehouse platform without Gazebo.

Runs the real robot stack (map server, planner, controller, obstacle monitor,
task manager, fleet beacon) for every robot using sim_world_node for physics,
plus the fleet manager, analytics, Control Center and backend ingest bridge.
No rendering engine is required, so it works on servers and headless boxes.

Usage:
    ros2 launch warehouse_bringup headless_demo.launch.py
    ros2 launch warehouse_bringup headless_demo.launch.py robot_count:=1
    ros2 launch warehouse_bringup headless_demo.launch.py backend_url:=http://localhost:8090
"""

import json

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')
    robot_count = LaunchConfiguration('robot_count', default='2')
    map_file = LaunchConfiguration('map', default='warehouse_map.yaml')
    backend_url = LaunchConfiguration('backend_url', default='')

    is_multi = PythonExpression(["'", robot_count, "' == '2' and 'true' or 'false'"])

    declare_args = [
        DeclareLaunchArgument('robot_count', default_value='2'),
        DeclareLaunchArgument('map', default_value='headless_map.yaml',
                              description='Map covering the demo world (-5..5 m)'),
        DeclareLaunchArgument('backend_url', default_value='',
                              description='Production backend URL ('' = disable ingest)'),
    ]

    robots = [
        {'ns': 'robot1', 'id': 'robot1', 'spawn': [0.0, 0.0, 0.0],
         'station': [0.0, 4.0]},
        {'ns': 'robot2', 'id': 'robot2', 'spawn': [-1.0, 1.0, 0.5],
         'station': [0.0, -4.0]},
    ]

    def _robot_stack(r, condition):
        ns = r['ns']
        return [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([pkg_dir, '/launch/load_map.launch.py']),
                launch_arguments={'namespace': ns, 'map': map_file}.items(),
                condition=condition),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([pkg_dir, '/launch/planner.launch.py']),
                launch_arguments={'namespace': ns}.items(),
                condition=condition),
            Node(
                package='warehouse_bringup', executable='controller_node',
                name='controller', namespace=ns, output='screen',
                parameters=[f'{pkg_dir}/config/controller.yaml'],
                remappings=[
                    ('/plan', 'plan'), ('/amcl_pose', 'amcl_pose'),
                    ('/cmd_vel', 'controller_cmd_vel')],
                condition=condition),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([pkg_dir, '/launch/obstacle_avoidance.launch.py']),
                launch_arguments={'namespace': ns}.items(),
                condition=condition),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([pkg_dir, '/launch/task_manager.launch.py']),
                launch_arguments={'namespace': ns, 'robot_id': r['id']}.items(),
                condition=condition),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource([pkg_dir, '/launch/beacon.launch.py']),
                launch_arguments={
                    'namespace': ns, 'robot_id': r['id'],
                    'charging_station_x': str(r['station'][0]),
                    'charging_station_y': str(r['station'][1]),
                }.items(),
                condition=condition),
        ]

    actions = list(declare_args)
    spawns = {}
    for r in robots:
        spawns[r['ns']] = r['spawn']
        # robot1 always launches; robot2 only in multi-robot mode.
        cond = IfCondition(is_multi) if r['ns'] == 'robot2' else None
        actions.extend(_robot_stack(r, cond))

    # Headless physics (moves robots toward their goals).
    actions.append(Node(
        package='warehouse_bringup', executable='sim_world_node',
        name='sim_world', output='screen',
        parameters=[{
            'robots': ','.join(r['ns'] for r in robots),
            'spawns': json.dumps(spawns),
            'max_speed': 0.25,
            'bounds': '[-4.8, 4.8, -4.8, 4.8]',
        }]))

    # Global services.
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/fleet_manager.launch.py']),
        launch_arguments={'with_beacon': 'false'}.items()))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/analytics.launch.py'])))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/dashboard.launch.py']),
        launch_arguments={'port': '8080'}.items()))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/control_center.launch.py'])))
    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/backend_ingest.launch.py']),
        launch_arguments={'backend_url': backend_url}.items()))

    return LaunchDescription(actions)
