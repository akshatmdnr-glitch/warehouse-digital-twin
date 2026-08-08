<div align="center">

# ◈ Warehouse Digital Twin

**A full digital twin of a warehouse: a Gazebo world full of autonomous TurtleBot3 robots, an execution engine that makes them move, a fleet scheduler that balances their work, an operator Control Center with a live map, and a production backend.**

ROS 2 Jazzy · Gazebo Harmonic · TurtleBot3 · FastAPI · SQLite · Docker · GitHub Actions

[![ROS2](https://img.shields.io/badge/ROS2-Jazzy-blue)](https://docs.ros.org/en/jazzy)
[![Python](https://img.shields.io/badge/Python-3.12-green)](https://www.python.org)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-84%20passed-brightgreen)](#-testing)
[![Quality](https://img.shields.io/badge/lint-black%20%7C%20isort%20%7C%20flake8%20%7C%20pylint%20%7C%20mypy-brightgreen)]()

</div>

---

## What is this?

A single platform that simulates a warehouse of autonomous mobile robots and
operates them end-to-end like a real fleet:

- **Simulation & robotics** — a 20 × 20 m Gazebo warehouse (9 racks A1–C3,
  delivery stations, charging pads, loading dock), two TurtleBot3 robots with
  LiDAR, and a full per-robot execution stack: map loading, A* planning,
  pure-pursuit control, obstacle avoidance, task execution.
- **Execution engine** — an authoritative per-robot state machine
  (IDLE → PLANNING → MOVING_TO_PICKUP → PICKING → CARRYING → DROPPING →
  RETURNING → CHARGING) computed **only** from real engine signals, plus a
  headless physics substitute so the whole platform runs without a GPU.
- **Fleet management** — a centralized dispatcher with per-robot task queues,
  idle-first weighted dispatch, route reservations with sliding-window traffic
  segments, fault recovery, and battery-aware scheduling.
- **Operator Control Center** — a real-time web app with an interactive
  **digital-twin map**, per-robot manual controls, task/fleet management,
  events, alerts, and runtime settings.
- **Production backend** — a FastAPI service with persistent SQLite storage, a
  REST API, WebSocket streaming, role-based authentication, event/alert store,
  analytics history, background services, health endpoints and Swagger docs.

Everything is observable: each robot's state, planned path, task and battery
is published on ROS topics and streamed to the web UIs in real time.

## Quick start

```bash
# full demo: builds the ROS workspace, launches sim + fleet + backend,
# submits demo tasks, and opens the Control Center
./scripts/run_demo.sh

# production deployment with Docker
git clone <this repo>
docker compose up -d
```

### Services

| Service | URL | Role |
|---|---|---|
| Control Center | http://localhost:8081 | Operator web UI + live digital-twin map |
| Read-only dashboard | http://localhost:8080 | Fleet status dashboard |
| Control Center WebSocket | http://localhost:8082 | Real-time state stream |
| Backend API + Swagger | http://localhost:8090/docs | REST API, auth, persistence |
| Monitoring | http://localhost:9100 | System + component health |

## The two-robot demo

The warehouse ships with a ready-made two-robot Gazebo demo
(`warehouse.launch.py`):

1. `robot1` and `robot2` spawn at opposite ends of the warehouse.
2. The operator (or the demo mission node) creates tasks: a **pickup** at a
   rack goal (e.g. A1) and a **dropoff** at a delivery station
   (Shipping Dock, Packing Station, Loading Dock).
3. The **fleet scheduler** assigns each task to the best idle robot, so both
   robots drive **simultaneously** along their own planned paths.
4. Each robot navigates to the rack, picks up a cube/package, carries it to the
   delivery station, drops it off, and returns home.
5. The demo visualization draws the planned path ribbons, PICKUP/DROPOFF
   markers and floating goal labels in the Gazebo world, while the Control
   Center map shows the same live state.

See [docs/OPERATOR.md](docs/OPERATOR.md) for the operator workflow.

## Architecture

Five layers:

1. **World & robot stack** — Gazebo world (`warehouse.world.sdf`), robot models,
   ROS↔Gazebo bridge, sim localization (Gazebo pose → `/amcl_pose`), planner,
   controller, obstacle monitor, task manager.
2. **Execution engine** — `robot_status_publisher` publishes the authoritative
   per-robot exec state; `task_manager` runs the pickup/dropoff FSM; a headless
   `sim_world_node` substitutes for Gazebo when no GPU is available.
3. **Fleet layer** — `fleet_manager` (registry, dispatch, queues, reservations,
   recovery), `analytics` (metrics), task sources (`demo_mission`,
   `control_center`, `backend_ingest`).
4. **Observers & UIs** — `control_center` (web UI + map + commands),
   `dashboard` (status), `visualization` / `demo_visualization` (in-world
   Gazebo rendering), `package_carrier` / `cube_carrier` (physical payloads).
5. **Production backend** — FastAPI + SQLite, bridged into ROS by
   `backend_ingest_node` (`/api/ingest` + task relay).

Full details and diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/diagrams](docs/diagrams) (architecture, deployment, ROS graph, sequence).

## Repository layout

```
backend/            FastAPI backend (REST, WS, auth, SQLite, services, tests)
docs/               guides + Graphviz architecture diagrams
docker/             Dockerfiles for the backend and ROS images
ros2_ws/
  src/warehouse_bringup/
    launch/          launch files (per-robot stack, fleet, world, top-level)
    warehouse_bringup/  ROS nodes (planner, controller, task manager, ...)
    config/          controller/planner/obstacle/bridge/gui configs
    worlds/          warehouse worlds (full + empty)
    models/          rack, stations, dock, package, robot models
    maps/            occupancy maps (warehouse_world, headless, legacy)
    web/             Control Center UI (HTML/CSS/JS + map renderer)
    test/            ROS node unit tests
scripts/             demo/headless launchers, backend launcher, monitor, perf
.github/workflows/   CI pipeline (lint, backend tests, ROS tests, docker)
```

## Documentation

- [Installation guide](docs/INSTALLATION.md)
- [Architecture guide](docs/ARCHITECTURE.md)
- [Developer guide](docs/DEVELOPER.md)
- [Operator manual](docs/OPERATOR.md)
- [API documentation](docs/API.md)
- [Dashboard guide](docs/DASHBOARD.md)
- [Fleet guide](docs/FLEET.md)
- [Deployment guide](docs/DEPLOYMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md) · [FAQ](docs/FAQ.md)
- [Contributing](docs/CONTRIBUTING.md) · [License](LICENSE)

## Feature matrix

| Area | Status |
|---|---|
| Gazebo warehouse world, LiDAR, SLAM, localization, A* planning, control, obstacle avoidance | ✅ |
| Authoritative per-robot execution state machine (exec_state) | ✅ |
| Multi-robot namespacing, task queues, pickup/dropoff task FSM | ✅ |
| Fleet registry, heartbeats, idle-first scoring dispatch, route reservations | ✅ |
| Battery simulation, charging, fault recovery, analytics | ✅ |
| Control Center (live map, controls, tasks, fleet, events, alerts, settings) | ✅ |
| Two-robot Gazebo demo (paths, markers, cube carrier, camera) | ✅ |
| Production backend (DB, REST, WebSocket, auth, health, Swagger, logging) | ✅ |
| Deployment (Docker, Compose, CI, docs, tests, monitoring, demo) | ✅ |

## Testing

```bash
# backend tests
cd backend && python -m pytest tests/ --cov=backend

# ROS node/launch tests
cd ros2_ws && source /opt/ros/jazzy/setup.bash \
  && python -m pytest src/warehouse_bringup/test/

# code quality
black --check backend/
isort --check backend/
flake8 backend/backend/
pylint --rcfile=pylintrc backend/backend/
mypy --config-file pyproject.toml backend/backend/
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
