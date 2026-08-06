#!/usr/bin/env bash
# Launch the complete system: backend + ROS warehouse (sim, fleet, analytics,
# Control Center, ingest). Gazebo runs with the display available, or headless
# via xvfb when no DISPLAY is set.
#
#   scripts/launch_all.sh [robot_count]
#   scripts/launch_all.sh 2

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROBOT_COUNT="${1:-2}"

# 1. Backend (background, logged).
if ! curl -s http://localhost:8090/api/health >/dev/null 2>&1; then
  echo "[launch_all] starting backend..."
  BACKEND_LOG_FILE="$ROOT/backend/data/logs/warehouse.log" \
    setsid nohup "$ROOT/scripts/launch_backend.sh" \
    >"$ROOT/backend/data/logs/backend.out" 2>&1 &
  sleep 2
fi

# 2. ROS warehouse.
echo "[launch_all] launching warehouse (robot_count=$ROBOT_COUNT)..."
export TURTLEBOT3_MODEL=burger
source /opt/ros/jazzy/setup.bash
source "$ROOT/ros2_ws/install/setup.bash"

if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
  exec xvfb-run -a ros2 launch warehouse_bringup warehouse.launch.py \
    robot_count:="$ROBOT_COUNT" backend_url:=http://localhost:8090
else
  exec ros2 launch warehouse_bringup warehouse.launch.py \
    robot_count:="$ROBOT_COUNT" backend_url:=http://localhost:8090
fi
