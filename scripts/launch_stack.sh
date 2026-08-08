#!/usr/bin/env bash
# Robust detached launcher for the warehouse stack.
# Usage: scripts/launch_stack.sh [logfile]
LOG="${1:-/tmp/opencode/stack.log}"
cd /home/akshat/warehouse-digital-twin || exit 1
# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
source ros2_ws/install/setup.bash
exec scripts/launch_gui.sh ros2 launch warehouse_bringup warehouse.launch.py \
    robot_count:=2 backend_url:=http://localhost:8090 > "$LOG" 2>&1
