<div align="center">

# ◈ Warehouse Digital Twin

**A production-grade multi-robot warehouse simulation, fleet-management platform, operator Control Center and enterprise backend.**

ROS 2 Jazzy · Gazebo Harmonic · TurtleBot3 · FastAPI · SQLite · Docker · GitHub Actions

[![ROS2](https://img.shields.io/badge/ROS2-Jazzy-blue)](https://docs.ros.org/en/jazzy)
[![Python](https://img.shields.io/badge/Python-3.12-green)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-84%20passed-brightgreen)](#-testing)
[![Quality](https://img.shields.io/badge/lint-black%20%7C%20isort%20%7C%20flake8%20%7C%20pylint%2010%2F10%20%7C%20mypy-brightgreen)]()

</div>

---

## What is this?

A full software platform that simulates a warehouse full of autonomous
TurtleBot3 robots and operates them like a real fleet:

- **Simulation & robotics** — Gazebo world, LiDAR, custom AMCL localization,
  A\* planning, waypoint control, obstacle avoidance, SLAM tooling.
- **Fleet management** — centralized registry, heartbeat monitoring, capability
  scoring dispatch, grid-cell reservations with sliding-window traffic
  segments, head-on collision avoidance, fault recovery and battery-aware
  scheduling.
- **Operator Control Center** — a real-time web dashboard with an interactive
  map, per-robot manual controls, task/fleet management, events, alerts and
  runtime settings.
- **Production backend** — a FastAPI service with persistent SQLite storage,
  a full REST API, WebSocket streaming, role-based authentication, an event
  store, analytics history, background services, health endpoints and Swagger
  docs.

## Quick start

```bash
# one-command demo (builds, launches sim+fleet+backend, submits demo tasks)
./run_demo.sh

# or: production deployment with Docker
git clone <this repo>
docker compose up -d
```

Open:

| Service | URL |
|---|---|
| Control Center | http://localhost:8081 |
| Read-only dashboard | http://localhost:8080 |
| Backend API + Swagger | http://localhost:8090/docs |
| Monitoring | http://localhost:9100 |

## Repository layout

```
backend/            FastAPI backend (REST, WS, auth, DB, services, tests)
docs/               guides + architecture diagrams
docker/             Dockerfiles
ros2_ws/            ROS 2 workspace (nodes + launch files + maps + worlds)
scripts/            demo, backend launcher, system monitor, perf benchmark
.github/workflows/  CI pipeline
```

## Documentation

- [Installation guide](docs/INSTALLATION.md)
- [Architecture guide](docs/ARCHITECTURE.md) — diagrams in [docs/diagrams](docs/diagrams)
- [Developer guide](docs/DEVELOPER.md)
- [Operator manual](docs/OPERATOR.md)
- [API documentation](docs/API.md) (also served live at `/docs`)
- [Dashboard guide](docs/DASHBOARD.md)
- [Fleet guide](docs/FLEET.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md) · [FAQ](docs/FAQ.md)
- [Contributing](docs/CONTRIBUTING.md) · [License](LICENSE)

## Feature matrix (phases 1–10)

| Area | Status |
|---|---|
| Simulation, SLAM, localization, planning, control, obstacle avoidance | ✅ |
| Multi-robot namespacing, task scheduling (priority queue), cancellations | ✅ |
| Fleet registry, heartbeats, scoring dispatch, reservations, traffic segments | ✅ |
| Battery simulation, fault recovery, monitoring, analytics | ✅ |
| Control Center (map, controls, tasks, fleet, events, alerts, settings) | ✅ |
| Production backend (DB, REST, WebSocket, auth, health, Swagger, logging) | ✅ |
| Deployment (Docker, Compose, CI, docs, tests, monitoring, demo) | ✅ |

## Testing

```bash
# backend + ROS unit/launch tests
cd backend && python -m pytest tests/ --cov=backend
cd ros2_ws  && source /opt/ros/jazzy/setup.bash && python -m pytest src/warehouse_bringup/test/

# code quality
black --check backend/
isort --check backend/
flake8 backend/backend/
pylint --rcfile=pylintrc backend/backend/
mypy --config-file pyproject.toml backend/backend/
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
