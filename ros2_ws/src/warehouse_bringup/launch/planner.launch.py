"""Launch A* global path planner (namespaced per robot)."""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    namespace = LaunchConfiguration('namespace')

    declare_namespace = DeclareLaunchArgument(
        'namespace', default_value='',
        description='ROS namespace for this planner (empty = root)')

    planner_node = Node(
        package='warehouse_bringup',
        executable='planner_node',
        name='planner',
        namespace=namespace,
        output='screen',
        remappings=[
            ('/map', 'map'),
            ('/amcl_pose', 'amcl_pose'),
            ('/goal_pose', 'goal_pose'),
            ('/plan', 'plan'),
        ],
    )

    return LaunchDescription([declare_namespace, planner_node])
