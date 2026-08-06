"""Launch warehouse task manager with FSM execution (namespaced per robot)."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')
    robot_id = LaunchConfiguration('robot_id', default='tb3_1')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this task manager (empty = root)')
    declare_robot_id = DeclareLaunchArgument(
        'robot_id', default_value='tb3_1',
        description='Robot id this task manager serves (matches fleet registry)')

    node = Node(
        package='warehouse_bringup',
        executable='task_manager_node',
        name='task_manager',
        namespace=namespace,
        output='screen',
        parameters=[{
            'goal_tolerance': 0.3,
            'pickup_delay': 2.0,
            'dropoff_delay': 1.5,
            'robot_id': robot_id,
        }],
        remappings=[
            # local topics → namespaced
            ('/amcl_pose', 'amcl_pose'),
            ('/goal_pose', 'goal_pose'),
            ('/task_status', 'task_status'),
            ('/task_state', 'task_state'),
            ('/queue_info', 'queue_info'),
            # global fleet topics stay at the root namespace
            ('/task_assignment', '/task_assignment'),
            ('/cancel_task', '/cancel_task'),
        ],
    )

    return LaunchDescription([declare_namespace, declare_robot_id, node])
