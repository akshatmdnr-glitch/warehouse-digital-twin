#!/usr/bin/env bash
# One-command demo: build, launch the full twin, submit demonstration tasks,
# and open the Control Center.
#
#   ./run_demo.sh          # 2 robots (default)
#   ./run_demo.sh 1        # single robot

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_COUNT="${1:-2}"

echo "== Warehouse Digital Twin demo (robot_count=$ROBOT_COUNT) =="

# 1. Build the ROS workspace (fast if already built).
echo "== [1/6] building ROS workspace =="
source /opt/ros/jazzy/setup.bash
(cd "$ROOT/ros2_ws" && colcon build --symlink-install)
source "$ROOT/ros2_ws/install/setup.bash"

# 2. Start the backend.
echo "== [2/6] starting backend =="
if ! curl -s http://localhost:8090/api/health >/dev/null 2>&1; then
  BACKEND_LOG_FILE="$ROOT/backend/data/logs/warehouse.log" \
    setsid nohup "$ROOT/scripts/launch_backend.sh" \
    >"$ROOT/backend/data/logs/backend.out" 2>&1 &
  sleep 3
fi
curl -s http://localhost:8090/api/health >/dev/null && echo "   backend OK"

# 3. Launch simulation + robots + fleet + analytics + Control Center + ingest.
echo "== [3/6] launching warehouse simulation =="
if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
  XWRAP=(xvfb-run -a)
else
  XWRAP=()
fi
"${XWRAP[@]}" ros2 launch warehouse_bringup warehouse.launch.py \
  robot_count:="$ROBOT_COUNT" backend_url:=http://localhost:8090 \
  >"$ROOT/backend/data/logs/warehouse.out" 2>&1 &
WAREHOUSE_PID=$!

# 4. Wait for the Control Center, then submit demonstration tasks.
echo "== [4/6] submitting demonstration tasks =="
for i in $(seq 1 30); do
  curl -s http://localhost:8081/api/state >/dev/null 2>&1 && break
  sleep 1
done
CC="http://localhost:8081/api/command"
post() { curl -s -X POST "$CC" -H 'Content-Type: application/json' -d "$1" >/dev/null; }
post '{"action":"create_task","task_id":"DEMO1","px":1,"py":1,"dx":5,"dy":3,"priority":2,"required_payload":0}'
post '{"action":"create_task","task_id":"DEMO2","px":2,"py":2,"dx":-3,"dy":4,"priority":1,"required_payload":0}'
post '{"action":"create_task","task_id":"DEMO3","px":0,"py":0,"dx":4,"dy":5,"priority":2,"required_payload":0}'
echo "   demo tasks submitted (DEMO1, DEMO2, DEMO3)"

# 5. Open the Control Center in the default browser.
echo "== [5/6] opening Control Center =="
(command -v xdg-open >/dev/null && (xdg-open http://localhost:8081 &) ) || true

# 6. Live operation.
echo "== [6/6] live operation (Control Center: http://localhost:8081, backend: http://localhost:8090/docs) =="
echo "   Ctrl-C to stop."
trap 'kill "$WAREHOUSE_PID" 2>/dev/null || true' EXIT
wait "$WAREHOUSE_PID"
