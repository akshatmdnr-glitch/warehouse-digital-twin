# Deployment Guide

## Option A — one command (native)

```bash
./run_demo.sh
```

Builds the workspace, starts the backend, launches the ROS twin, submits demo
tasks and opens the Control Center.

## Option B — Docker Compose (production)

```bash
docker compose up -d --build
```

Services:

| Service | Image | Ports | Role |
|---|---|---|---|
| `backend` | `warehouse-backend` | 8090 | REST + WS + Swagger + SQLite |
| `ros` | `warehouse-ros` | 8080/8081/8082 | ROS + Gazebo + fleet + Control Center + ingest |
| `monitoring` | `warehouse-backend` | 9100 | monitoring dashboard |

The backend keeps its database in the `wdt-db` volume (persists across
restarts). The ROS container runs the whole graph on one host (topics never
leave the container). `restart: unless-stopped` restarts failed services
automatically.

### Configuration

Configuration is layered: built-in defaults → `backend/config/backend.yaml` →
`BACKEND_*` environment variables → runtime `POST /api/config/reload`.

Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `BACKEND_AUTH_SECRET` | (required in prod) | token signing secret |
| `BACKEND_ADMIN_PASSWORD` | `admin` | default admin password |
| `BACKEND_DB_PATH` | `./data/warehouse.db` | SQLite file |
| `BACKEND_PORT` | `8090` | API port |
| `BACKEND_LOG_FILE` / `BACKEND_LOG_LEVEL` | file / INFO | logging |
| `BACKEND_RATE_LIMIT` | 120/min | per-client auth/mutation rate limit |

## CI/CD

`.github/workflows/ci.yml` runs on every push: black, isort, flake8, pylint,
mypy; backend pytest with coverage; ROS workspace build + unit/launch tests;
Docker builds for both images; `docker compose config` validation.

## Upgrades & backups

- Schema migrations run automatically on startup (versioned).
- Background services take daily SQLite backups into `data/backups`
  (keep 7), prune time-series rows by retention, and archive old events.
- `python -m backend.cli backup` for a manual backup.
