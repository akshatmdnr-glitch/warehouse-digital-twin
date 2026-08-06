#!/usr/bin/env bash
# Run the WHOLE Warehouse Digital Twin on a headless machine (no Gazebo).
#
#   scripts/run_headless.sh [robot_count]
#   scripts/run_headless.sh 1
#
# Starts: production backend, ROS fleet+robots (real nav stack with headless
# physics), analytics, Control Center, ingest bridge and monitoring, then
# submits demonstration tasks so the live dashboards show real operation.

set -eo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROBOT_COUNT="${1:-2}"
LOG_DIR="$ROOT/backend/data/logs"
mkdir -p "$LOG_DIR"

source /opt/ros/jazzy/setup.bash
source "$ROOT/ros2_ws/install/setup.bash"

echo "== Warehouse Digital Twin (headless, robot_count=$ROBOT_COUNT) =="

# 1. Backend.
if ! curl -s http://localhost:8090/api/health >/dev/null 2>&1; then
  echo "== starting backend =="
  BACKEND_LOG_FILE="$LOG_DIR/warehouse.log" \
    setsid nohup "$ROOT/scripts/launch_backend.sh" >"$LOG_DIR/backend.out" 2>&1 &
  sleep 3
fi
curl -s http://localhost:8090/api/health >/dev/null && echo "   backend:  http://localhost:8090 (Swagger /docs)"

# 2. Monitoring.
if ! curl -s http://localhost:9100/health >/dev/null 2>&1; then
  BACKEND_URL=http://localhost:8090 \
    setsid nohup python3 "$ROOT/scripts/system_monitor.py" --serve --port 9100 \
    >"$LOG_DIR/monitoring.out" 2>&1 &
fi
echo "   monitoring: http://localhost:9100"

# 3. ROS fleet + robots + Control Center + ingest.
echo "== launching ROS fleet + robots (headless physics) =="
setsid nohup ros2 launch warehouse_bringup headless_demo.launch.py \
  robot_count:="$ROBOT_COUNT" backend_url:=http://localhost:8090 \
  >"$LOG_DIR/warehouse.out" 2>&1 &
LAUNCH_PID=$!

# 4. Wait for the Control Center and for robots to register with the fleet.
echo "== waiting for Control Center + fleet registration =="
CC="http://localhost:8081/api/state"
for i in $(seq 1 60); do
  STATE="$(curl -s "$CC" 2>/dev/null || true)"
  TOTAL="$(printf '%s' "$STATE" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("fleet",{}).get("total",0))
except Exception: print(0)' 2>/dev/null || echo 0)"
  [ "$TOTAL" -ge "$ROBOT_COUNT" ] 2>/dev/null && break
  sleep 1
done
echo "   fleet registered: $TOTAL robot(s)"

# 5. Submit demonstration tasks (once the fleet can dispatch them).
echo "== submitting demonstration tasks =="
CC="http://localhost:8081/api/command"
post() { curl -s -X POST "$CC" -H 'Content-Type: application/json' -d "$1" >/dev/null; }
post '{"action":"create_task","task_id":"DEMO1","px":1,"py":1,"dx":3,"dy":3,"priority":2,"required_payload":0}'
post '{"action":"create_task","task_id":"DEMO2","px":2,"py":2,"dx":-3,"dy":3,"priority":1,"required_payload":0}'
post '{"action":"create_task","task_id":"DEMO3","px":0,"py":0,"dx":4,"dy":2,"priority":2,"required_payload":0}'
echo "   demo tasks submitted (DEMO1, DEMO2, DEMO3)"

echo ""
echo "== LIVE =="
echo "   Control Center : http://localhost:8081"
echo "   Read-only      : http://localhost:8080"
echo "   Backend API    : http://localhost:8090/docs"
echo "   Monitoring     : http://localhost:9100"
echo "   ROS launch pid : $LAUNCH_PID  (logs: $LOG_DIR/warehouse.out)"
echo ""
echo "Tip: create more tasks from the Control Center 'Tasks' tab, or:"
echo "  curl -s -X POST http://localhost:8081/api/command -H 'Content-Type: application/json' \\"
echo "    -d '{\"action\":\"create_task\",\"task_id\":\"T10\",\"px\":1,\"py\":1,\"dx\":6,\"dy\":2,\"priority\":1}'"
