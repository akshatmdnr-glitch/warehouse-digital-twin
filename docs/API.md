# API Documentation

The backend exposes an OpenAPI 3 specification at
`GET http://localhost:8090/openapi.json` with interactive Swagger UI at
`GET http://localhost:8090/docs` (and ReDoc at `/redoc`).

## Authentication

Most endpoints require `Authorization: Bearer <token>`.

1. `POST /api/auth/login` `{username, password}` → `{token, user, expires_at}`.
2. Use the token in the `Authorization` header (or `?token=` for WebSockets).

### Roles

| Role | Permissions |
|---|---|
| `operator` | read + dispatch commands, create/patch/delete tasks |
| `supervisor` | operator + settings, config reload, service control |
| `administrator` | everything + user management |

Tokens can carry `readonly: true` (read-only dashboard mode) which blocks all
mutations with `403`.

### Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | – (rate-limited) | login, returns token |
| POST | `/api/auth/logout` | ✓ | revoke token |
| GET | `/api/auth/me` · `/api/auth/roles` | ✓ | session / role list |
| POST | `/api/auth/users` · GET `/api/auth/users` | admin | user management |
| GET | `/api/robots` | ✓ | list robots (filter `status`, `charging`) |
| GET | `/api/robots/{id}` | ✓ | robot + position/battery history |
| POST | `/api/robots` | operator | register a robot |
| PATCH | `/api/robots/{id}` | operator | update robot |
| DELETE | `/api/robots/{id}` | admin | delete robot |
| GET | `/api/tasks` | ✓ | list tasks (filter status/priority/robot) |
| POST | `/api/tasks` | operator | create task (relayed to ROS) |
| GET/PATCH/DELETE | `/api/tasks/{id}` | operator | inspect / update / delete |
| GET | `/api/fleet` | ✓ | fleet summary + status history + queue |
| GET | `/api/analytics` | ✓ | latest analytics snapshot |
| GET | `/api/analytics/history` | ✓ | analytics snapshots / metrics |
| GET | `/api/analytics/reports?window=daily\|hourly` | ✓ | aggregated reports |
| GET | `/api/reservations` | ✓ | reservations + queue history |
| GET | `/api/events` | ✓ | events (filter `type`,`severity`,`q`,`since`) |
| GET | `/api/events/export?fmt=csv\|json` | ✓ | export |
| GET | `/api/alerts` | ✓ | active + history |
| POST | `/api/alerts/{id}/ack` | operator | acknowledge alert |
| GET | `/api/maps` · POST `/api/maps` · GET `/api/maps/{id}` | supervisor | maps |
| GET | `/api/health` · `/api/live` · `/api/ready` | – | liveness / readiness |
| GET | `/api/health/components` | ✓ | per-component status |
| GET | `/api/version` | – | version info |
| GET | `/api/config` · POST `/api/config/reload` | supervisor | config + hot reload |
| GET | `/api/monitoring` · `/series` · `/components` | ✓ | CPU/mem/DB/ROS status |
| GET | `/api/services` · POST `/{name}/pause\|resume` | supervisor | background services |
| GET | `/api/logs?lines=N&level=` | ✓ | structured log tail |
| POST | `/api/ingest` · `/api/ingest/events` | ✓ | ROS bridge batches |
| WS | `/api/ws` | ✓ | real-time stream |

## WebSocket API

`ws://host:8090/api/ws?token=<token>`

Messages are JSON `{type, data}`. On connect the server sends `hello` and a
`state` snapshot; then live pushes: `robot_update`, `task_update`, `fleet`,
`reservations`, `traffic`, `analytics`, `battery`, `localization`,
`navigation`, `alerts`, `events`, `health`. Clients may send `{type:"ping"}`
and receive `{type:"pong"}`. Reconnects are client-side; every connect gets a
fresh snapshot.

## Health / readiness

- `/api/health`, `/api/live` — process liveness.
- `/api/ready` — DB reachable and ROS bridge has ingested recently.
- `/api/health/components` — `db`, `ros_bridge`, `fleet`, `dashboard`,
  `analytics`, `api` status with latencies and table sizes.
