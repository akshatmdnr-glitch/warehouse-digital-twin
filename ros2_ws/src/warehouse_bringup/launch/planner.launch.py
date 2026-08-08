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
        parameters=[{
            # Keep the path at least ~0.25 m from obstacles so the robot body
            # (TurtleBot3 radius ~0.18 m) does not clip rack corners. 5 cells
            # x 0.05 m = 0.25 m clearance.
            'inflation_radius_cells': 5,
        }],
        remappings=[
            ('/map', 'map'),
            ('/amcl_pose', 'amcl_pose'),
            ('/goal_pose', 'goal_pose'),
            ('/plan', 'plan'),
        ],
    )

    return LaunchDescription([declare_namespace, planner_node])
