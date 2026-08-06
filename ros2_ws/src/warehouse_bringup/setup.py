import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'warehouse_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'rviz'),
            glob('rviz/*.rviz')),
        (os.path.join('share', package_name, 'worlds'),
            glob('worlds/*.sdf')),
        (os.path.join('share', package_name, 'config'),
            glob('config/*.yaml')),
        (os.path.join('share', package_name, 'maps'),
            glob('maps/*')),
        (os.path.join('share', package_name, 'web'),
            glob('web/*.html') + glob('web/*.js') + glob('web/*.css')),
        (os.path.join('share', package_name, 'web', 'js'),
            glob('web/js/*.js')),
    ],
    install_requires=['setuptools'],
    entry_points={
        'console_scripts': [
            'save_warehouse_map = warehouse_bringup.save_map:main',
            'load_warehouse_map = warehouse_bringup.map_loader:main',
            'amcl_node = warehouse_bringup.amcl_node:main',
            'goal_publisher = warehouse_bringup.goal_publisher:main',
            'planner_node = warehouse_bringup.planner_node:main',
            'controller_node = warehouse_bringup.controller_node:main',
            'obstacle_monitor_node = warehouse_bringup.obstacle_monitor_node:main',
            'task_manager_node = warehouse_bringup.task_manager_node:main',
            'fleet_manager_node = warehouse_bringup.fleet_manager_node:main',
            'robot_status_publisher = warehouse_bringup.robot_status_publisher:main',
            'analytics_node = warehouse_bringup.analytics_node:main',
            'dashboard_node = warehouse_bringup.dashboard_node:main',
            'control_center_node = warehouse_bringup.control_center_node:main',
            'backend_ingest_node = warehouse_bringup.backend_ingest_node:main',
            'sim_world_node = warehouse_bringup.sim_world_node:main',
        ],
    },
    zip_safe=True,
    maintainer='Akshat',
    maintainer_email='akshat@example.com',
    description='Bringup scripts and launch files for the Warehouse Digital Twin.',
    license='Apache-2.0',
    tests_require=['pytest', 'pytest-cov'],
)
