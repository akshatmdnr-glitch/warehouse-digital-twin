# Operator Manual

## Starting a shift

```bash
./run_demo.sh              # 2 robots + backend + Control Center + demo tasks
```

Open **http://localhost:8081** (Control Center) and **http://localhost:8090/docs**
(backend/Swagger). Log in to the backend with the administrator credentials
(default `admin` / `admin` — change in production via `BACKEND_ADMIN_PASSWORD`).

## Control Center tabs

| Tab | What you can do |
|---|---|
| **Dashboard** | fleet/task/analytics KPIs, robot table, reservation table |
| **Map** | occupancy map, robots, paths, goals, charging stations, reserved corridors; zoom/pan/auto-center, click a robot to select it |
| **Robots** | manual control: move forward/backward, rotate, stop, emergency stop, resume, return home, return charger, send goal, cancel goal; pause/resume |
| **Tasks** | create/cancel/delete tasks, set priority, manual assignment, automatic assignment, queue + history |
| **Fleet** | enable/disable, drain/recharge battery (simulation), restart, reconnect, heartbeat + reservation ownership |
| **Events** | live log with filtering, search and CSV/JSON export |
| **Alerts** | active alerts + history; acknowledge |
| **Monitoring** | API CPU/memory, request counts, WS clients, component status |
| **Settings** | runtime editing of planner weights, traffic, battery, reservation, fleet, analytics parameters + dashboard theme/refresh + backend URL |

## Key operations

- **Send a robot somewhere**: Robots tab → select robot → set X/Y/Yaw → *Send
  Goal* (or *Charger* / *Home*).
- **Emergency**: *Emergency Stop* (publishes a zero velocity, raises a critical
  alert). *Resume* releases it.
- **Create work**: Tasks tab → set pickup/dropoff/priority → *Create Task*. The
  fleet auto-assigns by scoring; the backend relays it into ROS.
- **Take a robot out of service**: Fleet tab → *disable* (bridge-level), or let
  the battery logic do it (a robot below `low_battery_threshold` gets no new
  work; at `critical_battery_threshold` it releases its task and charges).

## Safety notes

- Manual velocity commands are best-effort overrides and can conflict with the
  task manager in live operation — prefer goals to raw velocity.
- `Drain battery` and `Restart` are **simulation** controls consumed only by
  the status beacon.
