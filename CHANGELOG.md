# Changelog

## [1.0.0] — 2026-08-06

### Added (Phase 8 — Control Center)
- Real-time operator Control Center: live dashboard, interactive occupancy map
  (zoom/pan/auto-center/selection), manual robot controls, task management,
  fleet management, event viewer with filtering/search/export, alerts, runtime
  settings (dark theme, responsive).

### Added (Phase 9 — Production backend)
- FastAPI backend: SQLite persistence (migrations, automatic restore), REST API
  (robots/tasks/fleet/analytics/reservations/events/alerts/maps/health/version),
  WebSocket streaming (multi-client, reconnect), role-based authentication
  (operator/supervisor/admin, read-only mode), persisted event + alert system,
  analytics metrics with hourly/daily reports, background services (cleanup,
  archiving, pruning, health monitoring, heartbeat verification, backups,
  migrations), YAML + env configuration with hot reload, OpenAPI/Swagger,
  health/readiness/liveness endpoints, structured rotating logs, rate limiting.
- ROS ingest bridge (`backend_ingest_node`) and task relay; dashboard-uses-backend
  proxy in the Control Center.

### Added (Phase 10 — Deployment & DevOps)
- Dockerfiles (ROS, backend) + Docker Compose (backend, ros, monitoring).
- GitHub Actions CI (lint, backend tests, ROS build/tests, Docker build).
- ROS unit + launch-validation tests; pytest coverage.
- Code quality (black/isort/flake8/pylint/mypy) with zero errors.
- Full documentation set + Graphviz architecture diagrams.
- Monitoring service, performance benchmark, one-command demo (`./run_demo.sh`).

### Fixed
- Control Center `stations`/`homes` settings group leaking the `node` key.
- Backend ingest CSV-topic parsing (`/robot_pose`, `/task_assignment`).
- WebSocket broadcasts from non-async threads; FastAPI dependency annotations.
