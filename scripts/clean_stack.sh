#!/usr/bin/env bash
# Stop the warehouse ROS + Gazebo stack and purge stale DDS shared memory.
# Keeps the production backend (:8090) and monitoring (:9100) running.
#
# Usage: scripts/clean_stack.sh
set -u

for p in $(ps -eo pid,args | grep -E "gz sim (server|gui|-r)|warehouse_gui.config|create -name" | grep -v grep | awk '{print $1}'); do
  kill -9 "$p" 2>/dev/null
done
pgrep -f "/opt/ros/[j]azzy" | xargs -r kill -9 2>/dev/null
pgrep -f "ros2_ws/[i]nstall" | xargs -r kill -9 2>/dev/null
sleep 2

rm -f /dev/shm/fastrtps* /dev/shm/sem.fastrtps* 2>/dev/null

echo "cleaned. remaining gz: $(ps aux | grep -cE 'gz sim [s]erver|gz sim [g]ui|gz sim -[r]')"
echo "remaining ros: $(ps aux | grep -cE '/opt/ros/[j]azzy|ros2_ws/[i]nstall')"
