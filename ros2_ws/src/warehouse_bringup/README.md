# warehouse_bringup

Bringup scripts and launch files for the Warehouse Digital Twin.

## Prerequisites

- ROS2 Jazzy
- Gazebo Harmonic
- TurtleBot3 packages

```bash
sudo apt install ros-jazzy-turtlebot3-gazebo
```

## Build

```bash
cd ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select warehouse_bringup
```

## Launch

```bash
source install/setup.bash
ros2 launch warehouse_bringup simulation.launch.py
```

Launches Gazebo with an empty world, spawns a TurtleBot3 burger, and opens RViz.

## Teleoperation

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args -p stamped:=true
```
