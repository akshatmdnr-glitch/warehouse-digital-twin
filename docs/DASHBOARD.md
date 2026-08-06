# Dashboard Guide

Two web UIs are provided.

## Control Center (http://localhost:8081)

The primary operator UI (`control_center_node`). Live data is pushed over a
WebSocket (`ws://localhost:8082/ws`) with an HTTP polling fallback.

- **Live map** — occupancy grid, oriented robot icons, planned paths, goal
  markers, charging stations and reserved corridors (from `/reservation_status`
  cells). Supports zoom (wheel), pan (drag), auto-center, and click-to-select.
- **Robot control** — drives `/ns/cmd_vel` (TwistStamped) and `/ns/goal_pose`
  (PoseStamped); stop/estop/resume publish a zero velocity.
- **Task & fleet management** — publishes `/add_task`, `/cancel_task`,
  `/task_assignment`, `/robot_heartbeat` and the beacon sim topics
  (`/battery_command`, `/control`).
- **Events & alerts** — derived from observed transitions; searchable,
  filterable, exportable.
- **Backend integration** — when a backend URL is configured (Settings →
  Production backend), the top bar shows backend health and the Events/Tasks/
  Alerts/Monitoring tabs show persistent history proxied from the backend
  (`/api/backend/*`).

## Legacy read-only dashboard (http://localhost:8080)

`dashboard_node` — a minimal single-page observer that auto-refreshes
`/api/state` every second. Kept for backward compatibility and quick glances.

## API used by the UI

- `GET /api/state` — full snapshot (fleet, robots, tasks, analytics,
  reservations, events, alerts, settings, map).
- `GET /api/events`, `GET /api/events/export` — log + export.
- `POST /api/command` — operator commands (move, goal, estop, tasks, fleet…).
- `POST /api/settings` — runtime parameter updates.
- `POST /api/alerts/ack` — acknowledge alerts.
- `GET /api/backend/{kind}` — proxy to the production backend.
