# Troubleshooting Guide

| Symptom | Cause | Fix |
|---|---|---|
| Robots don't register / `OFFLINE` | heartbeat timeout too short or DDS discovery slow | raise `heartbeat_timeout`, ensure `robot_count` matches launches |
| No map in the dashboard | `/map` (or `/robotN/map`) not published | run `load_map.launch.py` / check localization launch |
| GPU lidar returns `inf` | headless box without a rendering engine | use `xvfb-run -a` or `GUI:=false`; localize from the saved map |
| Backend 401 on ingest | bridge token expired/secret changed | restart ingest; set `BACKEND_AUTH_SECRET` consistently |
| `robot_offline` alerts after restart | stale `last_seen` before the first heartbeat | heartbeats resume within 1s; ignore transient alerts |
| Control Center "backend unreachable" | backend not running / wrong URL | Settings → Production backend → set `http://localhost:8090` |
| Core dump on exit | rclpy background threads at shutdown | harmless teardown artifact |
| DDS saturation | many zombie ROS processes | `pkill -9 -f warehouse_bringup` and relaunch |
| Tasks not relayed to ROS | backend up but relay poll fails | check backend log + `backend_ingest` node output |

## Logs

- Backend: `backend/data/logs/warehouse.log` (JSON lines, rotating).
- ROS nodes: `ros2 launch` output / `backend/data/logs/warehouse.out`.
- `GET /api/logs?lines=200&level=WARNING` for a filtered tail.

## Backend DB

```bash
python -m backend.cli stats
python -m backend.cli backup
python -m backend.cli migrate
```
