"""Launch navigation components for one robot: localization, planning, control.

Usage:
    ros2 launch warehouse_bringup navigation.launch.py
    ros2 launch warehouse_bringup navigation.launch.py map:=my_map.yaml
    ros2 launch warehouse_bringup navigation.launch.py x:=2.0 y:=3.0 yaw:=1.57
    ros2 launch warehouse_bringup navigation.launch.py namespace:=robot1 robot_id:=robot1
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_dir = get_package_share_directory('warehouse_bringup')

    namespace = LaunchConfiguration('namespace')
    robot_id = LaunchConfiguration('robot_id', default='tb3_1')
    map_file = LaunchConfiguration('map', default='warehouse_map.yaml')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this robot (empty = root)')
    declare_robot_id = DeclareLaunchArgument(
        'robot_id', default_value='tb3_1',
        description='Robot id this navigation stack serves')

    # Localization (namespaced AMCL + map server)
    loc_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/localization.launch.py']),
        launch_arguments={
            'map': map_file,
            'namespace': namespace,
        }.items(),
    )

    # Goal arguments
    declare_x = DeclareLaunchArgument('x', default_value='0.0',
                                      description='Goal X position (m)')
    declare_y = DeclareLaunchArgument('y', default_value='0.0',
                                      description='Goal Y position (m)')
    declare_yaw = DeclareLaunchArgument('yaw', default_value='0.0',
                                        description='Goal yaw orientation (rad)')

    goal_node = Node(
        package='warehouse_bringup',
        executable='goal_publisher',
        name='goal_publisher',
        namespace=namespace,
        output='screen',
        parameters=[{
            'x': LaunchConfiguration('x'),
            'y': LaunchConfiguration('y'),
            'yaw': LaunchConfiguration('yaw'),
        }],
        remappings=[('/goal_pose', 'goal_pose'), ('/task_state', 'task_state')],
    )

    # Path planner (namespaced)
    planner_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/planner.launch.py']),
        launch_arguments={'namespace': namespace}.items(),
    )

    # Local controller (remapped to publish via obstacle monitor, namespaced)
    controller_node = Node(
        package='warehouse_bringup',
        executable='controller_node',
        name='controller',
        namespace=namespace,
        output='screen',
        parameters=[PathJoinSubstitution([pkg_dir, 'config', 'controller.yaml'])],
        remappings=[
            ('/plan', 'plan'),
            ('/amcl_pose', 'amcl_pose'),
            ('/cmd_vel', 'controller_cmd_vel'),
        ],
    )

    # Obstacle avoidance monitor (namespaced)
    obstacle_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/obstacle_avoidance.launch.py']),
        launch_arguments={'namespace': namespace}.items(),
    )

    # Warehouse task manager (namespaced, fleet topics stay global)
    task_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([pkg_dir, '/launch/task_manager.launch.py']),
        launch_arguments={
            'namespace': namespace,
            'robot_id': robot_id,
        }.items(),
    )

    return LaunchDescription([
        declare_namespace,
        declare_robot_id,
        loc_launch,
        declare_x,
        declare_y,
        declare_yaw,
        goal_node,
        planner_launch,
        controller_node,
        obstacle_launch,
        task_launch,
    ])
