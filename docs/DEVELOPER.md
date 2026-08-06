# Developer Guide

## Workflow

```bash
# ROS workspace
cd ros2_ws
colcon build --symlink-install
source install/setup.bash
python -m pytest src/warehouse_bringup/test/          # unit + launch tests

# Backend
cd backend
pip install -r requirements.txt
python -m pytest tests/ --cov=backend                 # API/auth/WS/DB tests
```

## Code quality

```bash
black backend/ ros2_ws/src/warehouse_bringup/warehouse_bringup/
isort backend/
flake8 backend/backend/ ros2_ws/src/warehouse_bringup/warehouse_bringup/
pylint --rcfile=pylintrc backend/backend/
mypy --config-file pyproject.toml backend/backend/
```

CI runs all of these on every push (`.github/workflows/ci.yml`).

## Conventions

- **ROS nodes** observe existing topics; they never publish control messages
  unless they are the Control Center (operator commands).
- **Backend** contains zero `rclpy`; all ROS↔backend traffic flows through
  `backend_ingest_node` → `POST /api/ingest` and the task relay
  (GET `/api/tasks?status=PENDING` → `/add_task`).
- **Message formats are CSV on ROS** (e.g. `robot_id,x,y,yaw`), JSON on the
  backend. Do not break backward compatibility; new fields append to the tail.
- State is derived from the **database** (comparison against previous rows),
  so events survive restarts.

## Adding a robot type

1. Register it in the beacon params (`robot_type`, `payload_capacity`,
   `max_speed`, `priority`).
2. The fleet scoring uses those fields automatically.
3. `robot_count:=N` supports 1–2 today; extend `warehouse.launch.py` (and
   `robot.launch.py` includes) for more. The Control Center discovers robots
   dynamically from `/fleet_status`.

## Performance / scale

`scripts/perf_benchmark.py` measures API latency, ingest throughput for
50/100/250 robots and dashboard latency. Ingestion is batched; time-series
rows are pruned by retention; WebSocket broadcasts are single JSON payloads.
