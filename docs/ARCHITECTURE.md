# Architecture Guide

Diagrams are rendered in [docs/diagrams](diagrams) (`architecture`, `ros2_graph`,
`deployment`, `sequence` — SVG/PNG/PDF).

## Layers

1. **Simulation & robot stack** (`ros2_ws/.../warehouse_bringup`)
   - Gazebo world + TurtleBot3 spawn (namespaced per robot).
   - Custom AMCL localization, A* planner, waypoint controller, obstacle
     monitor, task manager (priority queue FSM).
   - Per-robot status beacon: registration, heartbeat, pose, battery simulation.

2. **Fleet layer** (`fleet_manager_node`)
   - Central registry keyed by `robot_id`; liveness from heartbeats.
   - Scoring dispatch (distance / workload / priority / capability weights).
   - Grid-cell reservations split into traffic segments with a sliding window;
     head-on routes are serialized at dispatch.
   - Battery-aware scheduling, fault recovery (task requeue on robot failure),
     monitoring summaries and dispatch-decision debug output.

3. **Observers** (`analytics_node`, `dashboard_node`, `control_center_node`)
   - Pure subscribers; never modify robot/fleet behavior.
   - Analytics compute rolling metrics; Control Center aggregates state, derives
     events/alerts, serves the SPA (HTTP + WebSocket) and publishes operator
     commands to **existing** topics only (`/cmd_vel`, `/goal_pose`, `/add_task`,
     `/cancel_task`, `/robot_heartbeat`).

4. **Production backend** (`backend/`, FastAPI)
   - Isolated from ROS: no `rclpy`. Persists to SQLite, exposes REST + WebSocket
     + auth + health + Swagger. Data flows in through the ingest bridge.

5. **Bridge** (`backend_ingest_node`)
   - Subscribes existing topics, POSTs batches to `/api/ingest`; relays
     backend-created tasks into ROS via `/add_task`.

## Key topics

| Topic | Direction | Format |
|---|---|---|
| `/robot_registration` | robot → fleet | CSV (11 fields incl. namespace) |
| `/robot_heartbeat` | robot → fleet | `robot_id` |
| `/robot_pose` | robot → fleet/observers | `robot_id,x,y,yaw` |
| `/add_task` | operator → fleet | `task_id,px,py,dx,dy[,prio[,payload]]` |
| `/task_assignment` | fleet → task manager | `robot_id,task_id,px,py,dx,dy,prio,payload` |
| `/cancel_task` | fleet/operator → task manager | `robot_id,task_id` or `task_id` |
| `/fleet_status`, `/fleet_monitor` | fleet → observers | JSON |
| `/reservation_status` | fleet → observers | JSON (segments/cells) |
| `/recovery_event`, `/dispatch_decision` | fleet → observers | JSON |
| `/analytics` | analytics → observers | JSON |
| `/ns/cmd_vel`, `/ns/goal_pose`, `/ns/plan`, `/ns/task_status` | per robot | TwistStamped / PoseStamped / Path / JSON |

## Event & alert derivation

Events and alerts are derived by comparing batches against the stored database
(previous robot status / charging / task state), so nothing is lost on restart:

- `robot_online / robot_offline / charging_started / charging_finished`
- `task_assigned / task_completed / navigation_completed / reservation_granted`
- `battery_low` (crossing the fleet threshold while not charging)
- Alerts: `robot_offline`, `battery_low`, `task_timeout`, `reservation_timeout`,
  `traffic_congestion`, `localization_lost`, `planner_failure`, `goal_failure`,
  `emergency_stop`.

## Consistency guarantees

- Robots/tasks never double-dispatched: the fleet keeps each task in exactly one
  of {reservation, route queue, retry queue}.
- Reservations only released after a reservation is observed activated.
- The backend and ROS never write each other's data: the bridge is the only
  data path, and task relay is the only command path.
