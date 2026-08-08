"""Warehouse Control Center — operator backend bridge + web UI server.

The Control Center is a *read-only observer* of the ROS2 warehouse graph. It
never modifies robot or fleet behavior; every operator action is expressed as
a publication to an EXISTING control topic:

  Manual driving            -> /ns/cmd_vel        (geometry_msgs/TwistStamped)
  Send goal / home / charger-> /ns/goal_pose      (geometry_msgs/PoseStamped)
  Create task               -> /add_task          (std_msgs/String)
  Cancel / delete task      -> /cancel_task       (std_msgs/String)
  Reconnect robot           -> /robot_heartbeat   (std_msgs/String)

The only new topics are simulation-only controls consumed by the robot status
beacon (never by navigation): /ns/battery_command ('drain'/'recharge'/'set:x')
and /ns/control ('restart').

Observation (all existing publications):
  /fleet_status, /fleet_monitor, /analytics, /reservation_status,
  /recovery_event, /dispatch_decision, /task_assignment, /battery_status,
  /robot_pose, /map, and per-robot /map, /scan, /odom, /amcl_pose,
  /goal_pose, /plan, /task_status.

Serves (web + REST + WebSocket):
  GET  /                    -> Control Center SPA (dark, responsive)
  GET  /styles.css, /app.js, /js/*.js -> frontend assets
  GET  /api/state           -> JSON snapshot (fleet, tasks, analytics,
                               reservations, robots, map, events, alerts)
  GET  /api/events          -> event log (filterable by type/severity/search)
  GET  /api/events/export   -> filtered event log as CSV/JSON download
  GET  /api/alerts          -> active alerts + history
  GET  /api/settings        -> current settings
  POST /api/command         -> operator command (see _handle_command)
  POST /api/alerts/ack      -> acknowledge / clear an alert
  POST /api/settings        -> runtime parameter update
  ws://<host>:<ws_port>/ws  -> live state push every `push_period` seconds

Runtime settings are applied through the ROS parameter service (e.g.
/fleet_manager/set_parameters), which the fleet/analytics nodes now re-read
each control tick, so planner weights, traffic, battery and reservation
parameters take effect immediately.
"""

import json
import math
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, TwistStamped
from nav_msgs.msg import OccupancyGrid, Odometry, Path
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import GetParameters, SetParameters
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String

_MAP_MAX = 128  # max cells per map axis after downsampling

_EVENT_SEVERITY = {
    "task_assigned": "info",
    "task_created": "info",
    "task_completed": "info",
    "task_cancelled": "warning",
    "navigation_goal_received": "info",
    "navigation_completed": "info",
    "planner_updated": "info",
    "localization_initialized": "info",
    "robot_online": "info",
    "robot_offline": "high",
    "charging_started": "info",
    "charging_finished": "info",
    "reservation_granted": "info",
    "reservation_denied": "warning",
    "obstacle_detected": "warning",
    "collision_avoided": "warning",
    "planner_failure": "high",
    "goal_failure": "high",
    "robot_recovered": "info",
    "recovery": "high",
    "manual_command": "info",
    "alert_raised": "warning",
    "alert_cleared": "info",
    "system": "info",
}

_ALERT_SEVERITY = {
    "robot_offline": "high",
    "battery_low": "warning",
    "task_timeout": "high",
    "reservation_timeout": "warning",
    "traffic_congestion": "warning",
    "localization_lost": "high",
    "planner_failure": "high",
    "goal_failure": "high",
    "emergency_stop": "critical",
}


class ControlCenterNode(Node):
    def __init__(self):
        super().__init__("control_center")
        self.declare_parameter("http_port", 8081)
        self.declare_parameter("ws_port", 8082)
        self.declare_parameter("push_period", 0.5)
        self.declare_parameter("events_max", 500)
        self.declare_parameter("web_dir", "")
        # Alert thresholds (editable at runtime through the Settings API).
        self.declare_parameter("task_timeout", 180.0)
        self.declare_parameter("reservation_timeout", 60.0)
        self.declare_parameter("localization_timeout", 10.0)
        self.declare_parameter("planner_timeout", 15.0)
        self.declare_parameter("obstacle_range", 0.5)
        # Manual-drive defaults.
        self.declare_parameter("move_speed", 0.2)
        self.declare_parameter("rotate_speed", 0.5)
        self.declare_parameter("move_duration", 1.5)
        # Production backend integration (dashboard-uses-backend).
        self.declare_parameter("backend_url", "")
        self.declare_parameter("backend_user", "admin")
        self.declare_parameter("backend_password", "admin")

        self._http_port = int(self.get_parameter("http_port").value)
        self._ws_port = int(self.get_parameter("ws_port").value)
        self._push_period = float(self.get_parameter("push_period").value)
        self._events_max = int(self.get_parameter("events_max").value)
        self._web_dir = self.get_parameter("web_dir").value
        self._backend_url = self.get_parameter("backend_url").value.rstrip("/")
        self._backend_user = self.get_parameter("backend_user").value
        self._backend_password = self.get_parameter("backend_password").value
        self._backend_token = None
        self._backend_ok = None

        # ── Observed state (aggregated from existing publications) ──
        self._fleet = {"robots": [], "robot_count": 0}
        self._fleet_monitor = {}
        self._analytics = {}
        self._reservations = {
            "reservations": [],
            "pending_dispatches": [],
            "retry_tasks": [],
        }
        self._map = None
        self._battery_status = {}
        self._tasks = {}  # task_id -> tracker dict (bridge-derived)
        self._robot_state = {}  # robot_id -> per-robot observation state

        # ── Events, alerts, settings ──
        self._lock = threading.Lock()
        self._events = []  # newest first
        self._throttle_map = {}
        self._alerts = {}  # alert key -> active alert dict
        self._alert_history = []  # newest first
        self._settings = self._default_settings()
        self._known_params = {}  # (node, key) -> last known value (from GetParameters)

        # ── Per-robot entities (created dynamically on fleet discovery) ──
        self._entities = {}  # robot_id -> {'ns', 'subs':[...], 'cmd_pub', 'goal_pub'}

        # ── Manual drive state ──
        self._manual = {}  # robot_id -> (lin, ang, until_monotonic)

        # ── Subscriptions (global observers) ──
        self._fleet_sub = self.create_subscription(
            String, "/fleet_status", self._on_fleet, 10
        )
        self._monitor_sub = self.create_subscription(
            String, "/fleet_monitor", self._on_monitor, 10
        )
        self._analytics_sub = self.create_subscription(
            String, "/analytics", self._on_analytics, 10
        )
        self._res_sub = self.create_subscription(
            String, "/reservation_status", self._on_reservations, 10
        )
        self._recovery_sub = self.create_subscription(
            String, "/recovery_event", self._on_recovery, 10
        )
        self._decision_sub = self.create_subscription(
            String, "/dispatch_decision", self._on_decision, 10
        )
        self._assignment_sub = self.create_subscription(
            String, "/task_assignment", self._on_assignment, 10
        )
        self._battery_sub = self.create_subscription(
            String, "/battery_status", self._on_battery, 10
        )
        self._pose_sub = self.create_subscription(
            String, "/robot_pose", self._on_pose, 10
        )
        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._on_map, 10
        )
        self._map1_sub = self.create_subscription(
            OccupancyGrid, "/robot1/map", self._on_map, 10
        )
        self._map2_sub = self.create_subscription(
            OccupancyGrid, "/robot2/map", self._on_map, 10
        )

        # ── Command publishers (existing control topics) ──
        self._add_task_pub = self.create_publisher(String, "/add_task", 10)
        self._cancel_pub = self.create_publisher(String, "/cancel_task", 10)
        self._assignment_pub = self.create_publisher(String, "/task_assignment", 10)
        self._hb_pub = self.create_publisher(String, "/robot_heartbeat", 10)

        # ── Periodic tasks ──
        self._supervisor_timer = self.create_timer(1.0, self._supervisor_tick)
        self._drive_timer = self.create_timer(0.1, self._manual_drive_tick)
        self._param_refresh_timer = self.create_timer(5.0, self._refresh_params)
        self._backend_timer = self.create_timer(30.0, self._backend_refresh)
        self._backend_refresh()  # immediate connect at startup

        # ── Web servers (non-blocking) ──
        self._httpd = ThreadingHTTPServer(
            ("0.0.0.0", self._http_port), self._make_handler()
        )
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self._ws_clients = []
        threading.Thread(target=self._run_ws_server, daemon=True).start()

        self.get_logger().info(
            f"Control Center ready — HTTP :{self._http_port}, "
            f"WS :{self._ws_port} (push {self._push_period}s)"
        )

    # ════════════════════════════════════════════════════════════
    # Topic observers
    # ════════════════════════════════════════════════════════════

    def _safe_json(self, text, default=None):
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return default if default is not None else {}

    def _on_fleet(self, msg):
        data = self._safe_json(msg.data, {})
        self._fleet = data
        self._derive_fleet_events(data)
        for r in data.get("robots", []):
            self._ensure_entities(r.get("robot_id"), r.get("namespace", ""))
            self._update_task_tracker(r)

    def _on_monitor(self, msg):
        self._fleet_monitor = self._safe_json(msg.data, {})

    def _on_analytics(self, msg):
        self._analytics = self._safe_json(msg.data, {})

    def _on_reservations(self, msg):
        prev = self._reservations
        self._reservations = self._safe_json(msg.data, self._reservations)
        self._derive_reservation_events(prev, self._reservations)

    def _on_recovery(self, msg):
        data = self._safe_json(msg.data, {})
        etype = data.get("event")
        rid = data.get("robot_id", "")
        if etype == "robot_failed":
            tasks = data.get("tasks_recovered", [])
            self._log_event(
                "goal_failure",
                "high",
                rid,
                f"Robot {rid} failed — recovered {len(tasks)} task(s)",
            )
            self._raise_alert(
                f"goal_failure:{rid}",
                "high",
                "Goal failure",
                f"Robot {rid} failed its goal",
                rid,
            )
        elif etype in ("task_recovered", "robot_recovered"):
            self._log_event(
                "robot_recovered",
                "info",
                rid,
                f'Task {data.get("task_id", "?")} recovered'
                f' (reason: {data.get("reason", "?")})',
            )
        elif etype == "robot_charging":
            self._log_event(
                "charging_started",
                "info",
                rid,
                f'Robot {rid} released {len(data.get("tasks_released", []))} '
                f"task(s) and is charging",
            )

    def _on_decision(self, msg):
        data = self._safe_json(msg.data, {})
        tid = data.get("task_id")
        sel = data.get("selected_robot")
        if tid:
            t = self._tasks.setdefault(
                tid,
                {
                    "id": tid,
                    "status": "PENDING",
                    "robot": "",
                    "priority": 1,
                    "pickup": None,
                    "dropoff": None,
                    "first_seen": time.time(),
                },
            )
            if sel:
                t["robot"] = sel
                t["status"] = "ASSIGNED"

    def _on_assignment(self, msg):
        parts = [p.strip() for p in msg.data.strip().split(",")]
        if len(parts) < 6:
            return
        robot, tid = parts[0], parts[1]
        try:
            pickup = [float(parts[2]), float(parts[3])]
            dropoff = [float(parts[4]), float(parts[5])]
            priority = int(parts[6]) if len(parts) >= 7 else 1
        except ValueError:
            return
        t = self._tasks.setdefault(
            tid,
            {
                "id": tid,
                "status": "ASSIGNED",
                "robot": robot,
                "priority": priority,
                "pickup": pickup,
                "dropoff": dropoff,
                "first_seen": time.time(),
            },
        )
        t.update(
            {
                "robot": robot,
                "status": "ASSIGNED",
                "priority": priority,
                "pickup": pickup,
                "dropoff": dropoff,
            }
        )
        if robot and robot != "NONE":
            self._log_event(
                "task_assigned", "info", robot, f"Task {tid} assigned to {robot}"
            )
            self._clear_alert(f"task_timeout:{robot}")

    def _on_battery(self, msg):
        data = self._safe_json(msg.data, {})
        rid = data.get("robot_id")
        if not rid:
            return
        self._battery_status[rid] = data

    def _on_pose(self, msg):
        # robot_id,x,y[,yaw] — same stream used by the fleet (observation only).
        parts = msg.data.strip().split(",")
        if len(parts) < 3:
            return
        try:
            rid, x, y = parts[0], float(parts[1]), float(parts[2])
            yaw = float(parts[3]) if len(parts) >= 4 else 0.0
        except ValueError:
            return
        st = self._robot_state.setdefault(rid, self._fresh_robot_state())
        st["x"] = x
        st["y"] = y
        st["yaw"] = yaw

    def _on_map(self, msg):
        width, height = msg.info.width, msg.info.height
        step = max(1, (width * height) // (_MAP_MAX * _MAP_MAX))
        self._map = {
            "width": width,
            "height": height,
            "resolution": msg.info.resolution,
            "origin": [msg.info.origin.position.x, msg.info.origin.position.y],
            "step": step,
            "data": list(msg.data[::step]),
        }

    # ── Per-robot observers (created dynamically) ──────────────

    def _fresh_robot_state(self):
        return {
            "x": None,
            "y": None,
            "yaw": None,
            "speed_lin": None,
            "speed_ang": None,
            "scan_min": None,
            "obstacle": False,
            "collision_ts": 0.0,
            "amcl_ts": None,
            "localization_init": False,
            "goal": None,
            "plan": None,
            "plan_sig": None,
            "plan_ts": None,
            "task_status": [],
            "task_completed": set(),
            "estop": False,
            "paused": False,
            "disabled": False,
        }

    def _topic_for(self, ns, name):
        ns = (ns or "").strip("/")
        return f"/{ns}/{name}" if ns else f"/{name}"

    def _ensure_entities(self, robot_id, namespace):
        """Create per-robot subscriptions/publishers once the fleet reports it."""
        if not robot_id:
            return
        existing = self._entities.get(robot_id)
        if existing and existing["ns"] == namespace:
            return
        if existing:  # namespace changed — recreate
            for sub in existing["subs"]:
                self.destroy_subscription(sub)
        ns = namespace or ""
        subs = [
            self.create_subscription(
                LaserScan,
                self._topic_for(ns, "scan"),
                lambda m, rid=robot_id: self._on_scan(rid, m),
                10,
            ),
            self.create_subscription(
                Odometry,
                self._topic_for(ns, "odom"),
                lambda m, rid=robot_id: self._on_odom(rid, m),
                10,
            ),
            self.create_subscription(
                PoseWithCovarianceStamped,
                self._topic_for(ns, "amcl_pose"),
                lambda m, rid=robot_id: self._on_amcl(rid, m),
                10,
            ),
            self.create_subscription(
                PoseStamped,
                self._topic_for(ns, "goal_pose"),
                lambda m, rid=robot_id: self._on_goal(rid, m),
                10,
            ),
            self.create_subscription(
                Path,
                self._topic_for(ns, "plan"),
                lambda m, rid=robot_id: self._on_plan(rid, m),
                10,
            ),
            self.create_subscription(
                String,
                self._topic_for(ns, "task_status"),
                lambda m, rid=robot_id: self._on_task_status(rid, m),
                10,
            ),
        ]
        cmd_pub = self.create_publisher(
            TwistStamped, self._topic_for(ns, "cmd_vel"), 10
        )
        goal_pub = self.create_publisher(
            PoseStamped, self._topic_for(ns, "goal_pose"), 10
        )
        bat_pub = self.create_publisher(
            String, self._topic_for(ns, "battery_command"), 10
        )
        ctrl_pub = self.create_publisher(String, self._topic_for(ns, "control"), 10)
        self._entities[robot_id] = {
            "ns": ns,
            "subs": subs,
            "cmd_pub": cmd_pub,
            "goal_pub": goal_pub,
            "bat_pub": bat_pub,
            "ctrl_pub": ctrl_pub,
        }
        self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        self.get_logger().debug(f'Control Center observing {robot_id} (ns={ns or "/"})')

    def _on_scan(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        near = min((r for r in msg.ranges if math.isfinite(r)), default=float("inf"))
        st["scan_min"] = round(near, 3)
        thresh = self._settings["alerts"]["obstacle_range"]
        obstacle = near < thresh
        if obstacle and not st["obstacle"]:
            if self._throttle(f"obstacle:{robot_id}", 2.0):
                moving = (st["speed_lin"] or 0.0) > 0.05
                st["collision_ts"] = time.time()
                self._log_event(
                    "obstacle_detected",
                    "warning",
                    robot_id,
                    f"Obstacle {near:.2f}m ahead",
                )
                if moving:
                    self._log_event(
                        "collision_avoided",
                        "warning",
                        robot_id,
                        f"Collision avoided — stopped at {near:.2f}m",
                    )
        st["obstacle"] = obstacle

    def _on_odom(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        st["speed_lin"] = round(msg.twist.twist.linear.x, 3)
        st["speed_ang"] = round(msg.twist.twist.angular.z, 3)

    def _on_amcl(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        if not st["localization_init"]:
            st["localization_init"] = True
            self._log_event(
                "localization_initialized",
                "info",
                robot_id,
                f"Localization initialized for {robot_id}",
            )
        st["amcl_ts"] = time.time()
        self._clear_alert(f"localization_lost:{robot_id}")

    def _on_goal(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        p = msg.pose.position
        q = msg.pose.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        )
        st["goal"] = [round(p.x, 3), round(p.y, 3), round(yaw, 3)]
        if self._throttle(f"goal:{robot_id}", 1.5):
            self._log_event(
                "navigation_goal_received",
                "info",
                robot_id,
                f"Navigation goal ({p.x:.2f}, {p.y:.2f})",
            )

    def _on_plan(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        pts = [
            (round(p.pose.position.x, 2), round(p.pose.position.y, 2))
            for p in msg.poses
        ]
        sig = len(pts)
        if sig > 2:
            sig = (pts[0], pts[-1], len(pts))
        st["plan"] = pts
        st["plan_ts"] = time.time()
        if sig != st["plan_sig"]:
            st["plan_sig"] = sig
            self._clear_alert(f"planner_failure:{robot_id}")
            if self._throttle(f"plan:{robot_id}", 2.0):
                self._log_event(
                    "planner_updated",
                    "info",
                    robot_id,
                    f"Planner updated — path with {len(pts)} points",
                )

    def _on_task_status(self, robot_id, msg):
        st = self._robot_state.setdefault(robot_id, self._fresh_robot_state())
        try:
            tasks = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        st["task_status"] = tasks
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            status = t.get("status")
            track = self._tasks.setdefault(
                tid,
                {
                    "id": tid,
                    "status": status,
                    "robot": robot_id,
                    "priority": t.get("priority", 1),
                    "pickup": t.get("pickup"),
                    "dropoff": t.get("dropoff"),
                    "first_seen": time.time(),
                },
            )
            track.update(
                {
                    "robot": robot_id,
                    "status": status,
                    "priority": t.get("priority", track["priority"]),
                }
            )
            if status == "COMPLETED" and tid not in st["task_completed"]:
                st["task_completed"].add(tid)
                self._log_event(
                    "task_completed", "info", robot_id, f"Task {tid} completed"
                )
                self._log_event(
                    "navigation_completed",
                    "info",
                    robot_id,
                    f"Navigation completed for task {tid}",
                )
                self._clear_alert(f"task_timeout:{robot_id}")
                track["status"] = "COMPLETED"
        for tid in list(self._tasks):
            t = self._tasks[tid]
            if (
                t.get("status") == "ASSIGNED"
                and t.get("robot") == robot_id
                and tid not in {x.get("id") for x in tasks}
            ):
                if t.get("seen_dispatched"):
                    t["status"] = "CANCELLED"
                    self._log_event(
                        "task_cancelled", "warning", robot_id, f"Task {tid} cancelled"
                    )
            elif t.get("robot") == robot_id:
                t["seen_dispatched"] = True

    # ════════════════════════════════════════════════════════════
    # Event & alert derivation
    # ════════════════════════════════════════════════════════════

    def _log_event(self, etype, severity=None, robot="", message=""):
        with self._lock:
            self._events.insert(
                0,
                {
                    "ts": round(time.time(), 3),
                    "type": etype,
                    "severity": severity or _EVENT_SEVERITY.get(etype, "info"),
                    "robot": robot or "",
                    "message": message,
                },
            )
            if len(self._events) > self._events_max:
                del self._events[self._events_max :]

    def _throttle(self, key, interval):
        now = time.time()
        last = self._throttle_map.get(key, 0.0)
        if now - last < interval:
            return False
        self._throttle_map[key] = now
        return True

    def _derive_fleet_events(self, data):
        for r in data.get("robots", []):
            rid = r.get("robot_id")
            if not rid:
                continue
            st = self._robot_state.setdefault(rid, self._fresh_robot_state())
            status = r.get("status")
            if st.get("_last_status") not in (None, status):
                if status == "OFFLINE":
                    self._log_event(
                        "robot_offline", "high", rid, f"Robot {rid} is OFFLINE"
                    )
                    self._raise_alert(
                        f"robot_offline:{rid}",
                        "high",
                        "Robot offline",
                        f"Robot {rid} lost connectivity",
                        rid,
                    )
                else:
                    self._log_event(
                        "robot_online", "info", rid, f"Robot {rid} is back ONLINE"
                    )
                    self._clear_alert(f"robot_offline:{rid}")
            st["_last_status"] = status
            charging = bool(r.get("charging", False))
            if (
                st.get("_last_charging") is not None
                and st["_last_charging"] != charging
            ):
                if charging:
                    self._log_event(
                        "charging_started", "info", rid, f"Robot {rid} started charging"
                    )
                else:
                    self._log_event(
                        "charging_finished",
                        "info",
                        rid,
                        f"Robot {rid} finished charging",
                    )
                    self._clear_alert(f"battery_low:{rid}")
            st["_last_charging"] = charging

    def _derive_reservation_events(self, prev, cur):
        prev_owners = {r.get("robot_id") for r in prev.get("reservations", [])}
        cur_owners = {r.get("robot_id") for r in cur.get("reservations", [])}
        for rid in cur_owners - prev_owners:
            self._log_event(
                "reservation_granted", "info", rid, f"Reservation granted to {rid}"
            )
            self._clear_alert(f"reservation_timeout:{rid}")
        prev_pending = {p.get("task_id") for p in prev.get("pending_dispatches", [])}
        cur_pending = {p.get("task_id") for p in cur.get("pending_dispatches", [])}
        for tid in cur_pending - prev_pending:
            self._log_event(
                "reservation_denied",
                "warning",
                "",
                f"Task {tid} queued — reservation denied (route conflict)",
            )

    def _update_task_tracker(self, r):
        rid = r.get("robot_id")
        task_id = r.get("current_task") or ""
        st = self._robot_state.setdefault(rid, self._fresh_robot_state())
        if task_id:
            track = self._tasks.setdefault(
                task_id,
                {
                    "id": task_id,
                    "status": "ASSIGNED",
                    "robot": rid,
                    "priority": 1,
                    "pickup": None,
                    "dropoff": None,
                    "first_seen": time.time(),
                },
            )
            # The fleet commits a task optimistically as soon as it selects a
            # robot, even while the route is still queued (pending dispatch).
            # A task is only RUNNING once the robot's execution engine has
            # actually started working it — derived from the authoritative
            # exec_state, never from the fleet's current_task alone.
            exec_state = r.get("exec_state", "") or ""
            running_states = {
                "MOVING_TO_PICKUP", "PICKING", "CARRYING",
                "MOVING_TO_DROPOFF", "DROPPING", "PLANNING", "ASSIGNED",
            }
            if exec_state in running_states:
                track.update({"status": "RUNNING", "robot": rid})
            elif track.get("status") != "RUNNING":
                track.update({"status": "ASSIGNED", "robot": rid})
            if not track.get("run_started"):
                track["run_started"] = time.time()
        prev_task = st.get("_prev_fleet_task")
        if prev_task and task_id != prev_task:
            self._clear_alert(f"task_timeout:{rid}")
        st["_prev_fleet_task"] = task_id

    # ── Supervisor (alert generation, runs on the spin thread) ──

    def _supervisor_tick(self):
        s = self._settings
        battery_low = float(s["battery"]["low_battery_threshold"])
        for r in self._fleet.get("robots", []):
            rid = r.get("robot_id")
            if not rid:
                continue
            st = self._robot_state.setdefault(rid, self._fresh_robot_state())
            now = time.time()
            # Robot offline
            if r.get("status") == "OFFLINE":
                self._raise_alert(
                    f"robot_offline:{rid}",
                    "high",
                    "Robot offline",
                    f"Robot {rid} lost connectivity",
                    rid,
                )
            else:
                self._clear_alert(f"robot_offline:{rid}")
            # Battery low
            battery = r.get("battery")
            if battery is not None and not r.get("charging") and battery <= battery_low:
                self._raise_alert(
                    f"battery_low:{rid}",
                    "warning",
                    "Battery low",
                    f"Robot {rid} battery {battery:.0f}%",
                    rid,
                )
            elif r.get("charging") or (battery is not None and battery > battery_low):
                self._clear_alert(f"battery_low:{rid}")
            # Task timeout
            if r.get("current_task"):
                started = st.get("_task_timeout_start") or now
                st["_task_timeout_start"] = started
                if now - started > float(s["alerts"]["task_timeout"]):
                    self._raise_alert(
                        f"task_timeout:{rid}",
                        "high",
                        "Task timeout",
                        f'Robot {rid} task "{r["current_task"]}" '
                        f'exceeded {float(s["alerts"]["task_timeout"]):.0f}s',
                        rid,
                    )
            else:
                st["_task_timeout_start"] = None
                self._clear_alert(f"task_timeout:{rid}")
            # Localization lost
            if st["localization_init"]:
                if st.get("amcl_ts") is not None and now - st["amcl_ts"] > float(
                    s["alerts"]["localization_timeout"]
                ):
                    self._raise_alert(
                        f"localization_lost:{rid}",
                        "high",
                        "Localization lost",
                        f"Robot {rid} pose stale > "
                        f'{float(s["alerts"]["localization_timeout"]):.0f}s',
                        rid,
                    )
            # Planner failure (busy robot with no fresh plan)
            if r.get("current_task"):
                if st.get("plan_ts") is None or now - st["plan_ts"] > float(
                    s["alerts"]["planner_timeout"]
                ):
                    self._raise_alert(
                        f"planner_failure:{rid}",
                        "high",
                        "Planner failure",
                        f"Robot {rid} has no valid plan for "
                        f'{float(s["alerts"]["planner_timeout"]):.0f}s',
                        rid,
                    )
            else:
                self._clear_alert(f"planner_failure:{rid}")
        # Reservation timeout (robot waiting on a blocked segment window)
        for res in self._reservations.get("reservations", []):
            rid = res.get("robot_id")
            segs = res.get("segments_reserved") or []
            total = res.get("total_segments", 0)
            blocked = not segs and total > 0
            st = self._robot_state.setdefault(rid, self._fresh_robot_state())
            now = time.time()
            if blocked:
                st["_blocked_start"] = st.get("_blocked_start") or now
                if now - st["_blocked_start"] > float(
                    s["alerts"]["reservation_timeout"]
                ):
                    self._raise_alert(
                        f"reservation_timeout:{rid}",
                        "warning",
                        "Reservation timeout",
                        f"Robot {rid} waiting for a free segment "
                        f'>{float(s["alerts"]["reservation_timeout"]):.0f}s',
                        rid,
                    )
            else:
                st["_blocked_start"] = None
                self._clear_alert(f"reservation_timeout:{rid}")
        # Traffic congestion
        pending = self._reservations.get("pending_dispatches", [])
        head_on = any(
            r.get("head_on") for r in self._reservations.get("reservations", [])
        )
        if len(pending) >= 2 or head_on:
            self._raise_alert(
                "traffic_congestion",
                "warning",
                "Traffic congestion",
                f"{len(pending)} queued dispatch(s), "
                f'{"head-on corridor detected" if head_on else "network busy"}',
            )
        else:
            self._clear_alert("traffic_congestion")

    def _raise_alert(self, key, severity, title, message, robot=""):
        with self._lock:
            if key in self._alerts:
                return
            alert = {
                "id": key,
                "type": key.split(":")[0],
                "severity": severity,
                "title": title,
                "message": message,
                "robot": robot or "",
                "ts": round(time.time(), 3),
                "active": True,
            }
            self._alerts[key] = alert
            self._alert_history.insert(0, dict(alert))
            if len(self._alert_history) > 200:
                del self._alert_history[200:]
        self._log_event("alert_raised", severity, robot, f"{title}: {message}")

    def _clear_alert(self, key, message=None):
        with self._lock:
            alert = self._alerts.pop(key, None)
            if not alert:
                return
            alert["active"] = False
            alert["cleared_ts"] = round(time.time(), 3)
        self._log_event(
            "alert_cleared",
            "info",
            alert.get("robot", ""),
            message or f'Alert cleared: {alert["title"]}',
        )

    def ack_alert(self, alert_id):
        self._clear_alert(alert_id, "Acknowledged by operator")

    # ════════════════════════════════════════════════════════════
    # Settings
    # ════════════════════════════════════════════════════════════

    def _default_settings(self):
        return {
            "planner_weights": {
                "node": "fleet_manager",
                "score_w_distance": 1.0,
                "score_w_queue": 1.0,
                "score_w_battery": 1.0,
                "score_w_current": 10.0,
                "score_w_eta": 1.0,
            },
            "traffic": {
                "node": "fleet_manager",
                "segment_size": 2,
                "traffic_lookahead": 1,
                "reservation_buffer": 0,
                "cell_size": 1.0,
            },
            "battery": {
                "node": "fleet_manager",
                "low_battery_threshold": 30.0,
                "critical_battery_threshold": 15.0,
            },
            "fleet": {
                "node": "fleet_manager",
                "heartbeat_timeout": 3.0,
                "publish_rate": 1.0,
            },
            "analytics": {
                "node": "analytics",
                "publish_period": 2.0,
                "rolling_window": 20,
            },
            "alerts": {
                "node": "control_center",
                "task_timeout": 180.0,
                "reservation_timeout": 60.0,
                "localization_timeout": 10.0,
                "planner_timeout": 15.0,
                "obstacle_range": 0.5,
            },
            "manual": {
                "node": "control_center",
                "move_speed": 0.2,
                "rotate_speed": 0.5,
                "move_duration": 1.5,
            },
            "dashboard": {
                "node": "frontend",
                "refresh_rate": 1.0,
                "theme": "dark",
                "auto_center": True,
            },
            "stations": {
                "node": "control_center",
                "robot1": [0.0, 8.0],
                "robot2": [0.0, -8.0],
            },
            "homes": {
                "node": "control_center",
                "robot1": [0.0, 5.0],
                "robot2": [0.0, -5.0],
            },
            "backend": {
                "node": "control_center",
                "url": "",
                "user": "admin",
                "password": "admin",
            },
        }

    def _param_clients(self):
        return {
            "fleet_manager": self.create_client(
                SetParameters, "/fleet_manager/set_parameters"
            ),
            "analytics": self.create_client(SetParameters, "/analytics/set_parameters"),
        }

    def apply_setting(self, group, key, value):
        node = self._settings.get(group, {}).get("node", "control_center")
        with self._lock:
            if group not in self._settings:
                return {"ok": False, "error": f"Unknown group {group}"}
            if key not in self._settings[group]:
                return {"ok": False, "error": f"Unknown key {group}.{key}"}
            if group in ("stations", "homes"):
                self._settings[group][key] = [float(v) for v in value]
            else:
                self._settings[group][key] = value
        self._log_event("system", "info", "", f"Settings: {group}.{key} = {value}")
        if group == "backend" and key == "url":
            self._backend_refresh()  # reconnect immediately on URL change
        if node in ("fleet_manager", "analytics"):
            ok, err = self._set_ros_param(node, key, value)
            return {"ok": ok, "error": err, "node": node, "key": key, "value": value}
        return {"ok": True, "node": "bridge", "key": key, "value": value}

    def _set_ros_param(self, node_name, key, value):
        client = self._param_clients().get(node_name)
        if not client or not client.service_is_ready():
            return False, f"{node_name} parameter service not ready"
        req = SetParameters.Request()
        req.parameters = [Parameter(name=key, value=self._to_param_value(value))]
        future = client.call_async(req)
        # Remember the request; result checked on the spin thread.
        self._pending_param_sets = getattr(self, "_pending_param_sets", [])
        self._pending_param_sets.append(future)
        return True, None

    @staticmethod
    def _to_param_value(value):
        pv = ParameterValue()
        if isinstance(value, bool):
            pv.type = ParameterType.PARAMETER_BOOL
            pv.bool_value = value
        elif isinstance(value, int):
            pv.type = ParameterType.PARAMETER_INTEGER
            pv.integer_value = value
        elif isinstance(value, float):
            pv.type = ParameterType.PARAMETER_DOUBLE
            pv.double_value = value
        else:
            pv.type = ParameterType.PARAMETER_STRING
            pv.string_value = str(value)
        return pv

    def _refresh_params(self):
        """Read current ROS parameters into the settings view (async)."""
        for node_name in ("fleet_manager", "analytics"):
            client = self.create_client(GetParameters, f"/{node_name}/get_parameters")
            if not client.service_is_ready():
                continue
            keys = [
                k
                for k in self._settings
                if self._settings[k].get("node") == node_name
                for k in self._settings[k]
                if k != "node"
            ]
            if not keys:
                continue
            req = GetParameters.Request()
            req.names = keys
            future = client.call_async(req)

            def _cb(fut, node_name=node_name, keys=keys):
                try:
                    resp = fut.result()
                except Exception:
                    return
                for name, val in zip(keys, resp.values):
                    with self._lock:
                        for group in self._settings:
                            if (
                                self._settings[group].get("node") == node_name
                                and name in self._settings[group]
                            ):
                                self._settings[group][name] = self._value_out(val)
                client.destroy()

            future.add_done_callback(_cb)

    @staticmethod
    def _value_out(pv):
        if pv.type == ParameterType.PARAMETER_BOOL:
            return pv.bool_value
        if pv.type == ParameterType.PARAMETER_INTEGER:
            return pv.integer_value
        if pv.type == ParameterType.PARAMETER_DOUBLE:
            return pv.double_value
        return pv.string_value

    # ════════════════════════════════════════════════════════════
    # Operator commands
    # ════════════════════════════════════════════════════════════

    def handle_command(self, cmd):
        action = cmd.get("action")
        robot = cmd.get("robot")
        handler = getattr(self, f"_cmd_{action}", None)
        if handler is None:
            return {"ok": False, "error": f'Unknown action "{action}"'}
        try:
            return handler(cmd, robot)
        except Exception as e:  # defensive: never crash the HTTP server
            self.get_logger().error(f"Command {action} failed: {e}")
            return {"ok": False, "error": str(e)}

    # ---- Part 3: manual robot control ----
    def _cmd_move(self, cmd, robot):
        direction = cmd.get("direction", "forward")
        speed = float(cmd.get("speed") or self._settings["manual"]["move_speed"])
        lin = speed if direction == "forward" else -speed
        return self._start_manual(robot, lin, 0.0, cmd)

    def _cmd_rotate(self, cmd, robot):
        direction = cmd.get("direction", "left")
        speed = float(cmd.get("speed") or self._settings["manual"]["rotate_speed"])
        ang = speed if direction == "left" else -speed
        return self._start_manual(robot, 0.0, ang, cmd)

    def _start_manual(self, robot, lin, ang, cmd):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        duration = float(
            cmd.get("duration") or self._settings["manual"]["move_duration"]
        )
        with self._lock:
            self._manual[robot] = (lin, ang, time.monotonic() + duration)
        self._log_event(
            "manual_command",
            "info",
            robot,
            f'Manual: {cmd.get("action")} {cmd.get("direction", "")} '
            f"(v={lin:.2f}, w={ang:.2f})",
        )
        return {
            "ok": True,
            "action": cmd.get("action"),
            "robot": robot,
            "linear": lin,
            "angular": ang,
        }

    def _cmd_stop(self, cmd, robot):
        return self._stop_robot(robot, "Stop command")

    def _cmd_estop(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["estop"] = True
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._log_event("manual_command", "critical", robot, "EMERGENCY STOP")
        self._raise_alert(
            f"estop:{robot}",
            "critical",
            "Emergency stop",
            f"Emergency stop engaged for {robot}",
            robot,
        )
        return {"ok": True, "action": "estop", "robot": robot}

    def _cmd_resume(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["estop"] = False
            self._robot_state[robot]["paused"] = False
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._clear_alert(f"estop:{robot}", "Robot resumed by operator")
        self._log_event("manual_command", "info", robot, "Resume (estop released)")
        return {"ok": True, "action": "resume", "robot": robot}

    def _cmd_home(self, cmd, robot):
        x, y = self._settings["homes"].get(robot, [0.0, 0.0])
        return self._cmd_goal(
            {"action": "goal", "robot": robot, "x": x, "y": y, "yaw": 0.0}, robot
        )

    def _cmd_charger(self, cmd, robot):
        x, y = self._settings["stations"].get(robot, [0.0, 5.0])
        return self._cmd_goal(
            {"action": "goal", "robot": robot, "x": x, "y": y, "yaw": 0.0}, robot
        )

    def _cmd_goal(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        x = float(cmd.get("x", 0.0))
        y = float(cmd.get("y", 0.0))
        yaw = float(cmd.get("yaw", 0.0))
        with self._lock:
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._publish_goal(robot, x, y, yaw)
        self._log_event(
            "manual_command", "info", robot, f"Goal sent ({x:.2f}, {y:.2f}, {yaw:.2f})"
        )
        return {
            "ok": True,
            "action": "goal",
            "robot": robot,
            "x": x,
            "y": y,
            "yaw": yaw,
        }

    def _cmd_cancel_goal(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        self._stop_robot(robot, "Cancel goal")
        return {"ok": True, "action": "cancel_goal", "robot": robot}

    def _stop_robot(self, robot, reason):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._log_event("manual_command", "info", robot, reason)
        return {"ok": True, "action": "stop", "robot": robot}

    # ---- Part 4: task management ----
    def _cmd_create_task(self, cmd, robot):
        try:
            task_id = cmd.get("task_id") or f"T{int(time.time())}"
            px, py = float(cmd["px"]), float(cmd["py"])
            dx, dy = float(cmd["dx"]), float(cmd["dy"])
            priority = int(cmd.get("priority", 1))
            payload = float(cmd.get("required_payload", 0.0))
        except (KeyError, ValueError) as e:
            return {"ok": False, "error": f"Bad task fields: {e}"}
        msg = String()
        msg.data = f"{task_id},{px},{py},{dx},{dy},{priority},{payload}"
        self._add_task_pub.publish(msg)
        self._tasks.setdefault(
            task_id,
            {
                "id": task_id,
                "status": "PENDING",
                "robot": "",
                "priority": priority,
                "pickup": [px, py],
                "dropoff": [dx, dy],
                "first_seen": time.time(),
            },
        )
        self._log_event(
            "task_created",
            "info",
            "",
            f"Task {task_id} created " f"({px:.1f},{py:.1f}) → ({dx:.1f},{dy:.1f})",
        )
        return {"ok": True, "task_id": task_id}

    def _cmd_cancel_task(self, cmd, robot):
        task_id = cmd.get("task_id")
        if not task_id:
            return {"ok": False, "error": "task_id required"}
        target = robot
        if not target:
            track = self._tasks.get(task_id)
            if track and track.get("robot"):
                target = track["robot"]
        msg = String()
        msg.data = f"{target},{task_id}" if target else task_id
        self._cancel_pub.publish(msg)
        track = self._tasks.get(task_id)
        if track:
            track["status"] = "CANCELLED"
        self._log_event(
            "task_cancelled", "warning", target or "", f"Task {task_id} cancelled"
        )
        return {"ok": True, "task_id": task_id, "robot": target or ""}

    def _cmd_delete_task(self, cmd, robot):
        return self._cmd_cancel_task(cmd, robot)

    def _cmd_set_priority(self, cmd, robot):
        task_id = cmd.get("task_id")
        try:
            priority = max(0, min(2, int(cmd.get("priority", 1))))
        except ValueError:
            return {"ok": False, "error": "Bad priority"}
        track = self._tasks.get(task_id)
        if not track:
            return {"ok": False, "error": f"Unknown task {task_id}"}
        # Re-submit with the new priority so the fleet/task-manager honour it.
        pickup = track.get("pickup") or [0.0, 0.0]
        dropoff = track.get("dropoff") or [0.0, 0.0]
        self._cmd_cancel_task({"action": "cancel_task", "task_id": task_id}, robot)
        msg = String()
        msg.data = (
            f"{task_id},{pickup[0]},{pickup[1]},{dropoff[0]},{dropoff[1]},"
            f"{priority},0"
        )
        self._add_task_pub.publish(msg)
        track["priority"] = priority
        track["status"] = "PENDING"
        self._log_event(
            "task_created",
            "info",
            "",
            f"Task {task_id} re-submitted with priority {priority}",
        )
        return {"ok": True, "task_id": task_id, "priority": priority}

    def _cmd_pause(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["paused"] = True
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._log_event("manual_command", "info", robot, "Task paused (motion held)")
        return {"ok": True, "action": "pause", "robot": robot}

    def _cmd_resume_task(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["paused"] = False
            self._robot_state[robot]["estop"] = False
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._clear_alert(f"estop:{robot}", "Task resumed by operator")
        self._log_event("manual_command", "info", robot, "Task resumed")
        return {"ok": True, "action": "resume_task", "robot": robot}

    def _cmd_assign_robot(self, cmd, robot):
        task_id = cmd.get("task_id")
        target = cmd.get("robot")
        if not task_id or not target:
            return {"ok": False, "error": "task_id and robot required"}
        track = self._tasks.get(task_id)
        if track is None:
            return {"ok": False, "error": f"Unknown task {task_id}"}
        pickup = track.get("pickup") or [0.0, 0.0]
        dropoff = track.get("dropoff") or [0.0, 0.0]
        priority = int(track.get("priority", 1))
        self._cancel_pub.publish(String(data=f"{task_id}"))
        msg = String()
        msg.data = (
            f"{target},{task_id},{pickup[0]},{pickup[1]},"
            f"{dropoff[0]},{dropoff[1]},{priority},0"
        )
        self._assignment_pub.publish(msg)
        track["robot"] = target
        track["status"] = "ASSIGNED"
        self._log_event(
            "task_assigned",
            "info",
            target,
            f"Task {task_id} manually assigned to {target}",
        )
        return {"ok": True, "task_id": task_id, "robot": target}

    def _cmd_automatic(self, cmd, robot):
        self._log_event("system", "info", "", "Assignment returned to automatic mode")
        return {"ok": True, "action": "automatic"}

    # ---- Part 5: fleet management ----
    def _cmd_drain(self, cmd, robot):
        return self._send_beacon(
            robot, "battery_command", "drain", "Battery drain (simulation)"
        )

    def _cmd_recharge(self, cmd, robot):
        return self._send_beacon(
            robot, "battery_command", "recharge", "Battery recharge (simulation)"
        )

    def _cmd_set_battery(self, cmd, robot):
        try:
            pct = max(0.0, min(100.0, float(cmd.get("percent", 100.0))))
        except ValueError:
            return {"ok": False, "error": "Bad percent"}
        return self._send_beacon(
            robot, "battery_command", f"set:{pct:.0f}", f"Battery set to {pct:.0f}%"
        )

    def _cmd_restart(self, cmd, robot):
        return self._send_beacon(
            robot, "control", "restart", "Restart (simulated reboot)"
        )

    def _cmd_reconnect(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        msg = String()
        msg.data = robot
        self._hb_pub.publish(msg)
        self._log_event("robot_online", "info", robot, "Reconnect requested")
        return {"ok": True, "action": "reconnect", "robot": robot}

    def _cmd_disable(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["disabled"] = True
            self._manual.pop(robot, None)
        self._publish_twist(robot, 0.0, 0.0)
        self._log_event("manual_command", "warning", robot, "Robot disabled")
        return {"ok": True, "action": "disable", "robot": robot}

    def _cmd_enable(self, cmd, robot):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        with self._lock:
            self._robot_state[robot]["disabled"] = False
        self._log_event("manual_command", "info", robot, "Robot enabled")
        return {"ok": True, "action": "enable", "robot": robot}

    def _send_beacon(self, robot, topic, payload, label):
        if not self._require_robot(robot):
            return {"ok": False, "error": f'Unknown robot "{robot}"'}
        ent = self._entities[robot]
        pub = ent["bat_pub"] if topic == "battery_command" else ent["ctrl_pub"]
        pub.publish(String(data=payload))
        self._log_event("manual_command", "info", robot, label)
        return {"ok": True, "action": topic, "robot": robot, "value": payload}

    # ---- Helpers ----
    def _require_robot(self, robot):
        return robot in self._entities

    def _publish_twist(self, robot, lin, ang):
        ent = self._entities.get(robot)
        if not ent or ent["cmd_pub"] is None:
            return
        twist = TwistStamped()
        twist.header.stamp = self.get_clock().now().to_msg()
        twist.header.frame_id = "base_link"
        twist.twist.linear.x = float(lin)
        twist.twist.angular.z = float(ang)
        ent["cmd_pub"].publish(twist)

    def _publish_goal(self, robot, x, y, yaw):
        ent = self._entities.get(robot)
        if not ent or ent["goal_pub"] is None:
            return
        goal = PoseStamped()
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.header.frame_id = "map"
        goal.pose.position.x = float(x)
        goal.pose.position.y = float(y)
        goal.pose.position.z = 0.0
        goal.pose.orientation.z = math.sin(float(yaw) / 2.0)
        goal.pose.orientation.w = math.cos(float(yaw) / 2.0)
        ent["goal_pub"].publish(goal)

    def _manual_drive_tick(self):
        now = time.monotonic()
        with self._lock:
            for rid in list(self._manual):
                lin, ang, until = self._manual[rid]
                if now < until:
                    self._publish_twist(rid, lin, ang)
                else:
                    del self._manual[rid]
                    self._publish_twist(rid, 0.0, 0.0)

    # ════════════════════════════════════════════════════════════
    # Production backend proxy (dashboard-uses-backend)
    # ════════════════════════════════════════════════════════════

    def _backend_refresh(self):
        # Runtime-editable via Settings (backend group), falling back to the
        # ROS parameter set at launch.
        settings_url = self._settings.get("backend", {}).get("url") or ""
        self._backend_url = (
            settings_url or self.get_parameter("backend_url").value or ""
        ).rstrip("/")
        self._backend_user = self._settings.get("backend", {}).get("user", "admin")
        self._backend_password = self._settings.get("backend", {}).get(
            "password", "admin"
        )
        if not self._backend_url:
            self._backend_ok = None
            return
        try:
            if not self._backend_token:
                import urllib.request as _ur

                req = _ur.Request(
                    f"{self._backend_url}/api/auth/login",
                    data=json.dumps(
                        {
                            "username": self._backend_user,
                            "password": self._backend_password,
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with _ur.urlopen(req, timeout=5) as resp:
                    self._backend_token = json.loads(resp.read())["token"]
            req = urllib.request.Request(
                f"{self._backend_url}/api/health",
                headers={"Authorization": f"Bearer {self._backend_token}"},
            )
            with urllib.request.urlopen(req, timeout=5):
                self._backend_ok = True
        except Exception as e:
            self.get_logger().warn(f"Backend refresh failed: {e}")
            self._backend_token = None
            self._backend_ok = False

    def _backend_get(self, path):
        if not self._backend_url or not self._backend_token:
            return None
        try:
            req = urllib.request.Request(
                f"{self._backend_url}{path}",
                headers={"Authorization": f"Bearer {self._backend_token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    # ════════════════════════════════════════════════════════════
    # Snapshot
    # ════════════════════════════════════════════════════════════

    def _snapshot(self):
        robots_raw = self._fleet.get("robots", [])
        robots = []
        active = idle = charging = offline = 0
        now = time.time()
        for r in robots_raw:
            rid = r.get("robot_id")
            st = self._robot_state.get(rid, self._fresh_robot_state())
            battery = r.get("battery")
            exec_state = r.get("exec_state", "") or ""
            is_charging = exec_state == "CHARGING" or bool(r.get("charging", False))
            status = r.get("status")
            entry = {
                "id": rid,
                "namespace": r.get("namespace", ""),
                "status": status,
                "x": st.get("x"),
                "y": st.get("y"),
                "yaw": st.get("yaw"),
                "battery": battery,
                "charging": is_charging,
                "exec_state": exec_state or "UNKNOWN",
                "moving": bool(r.get("moving", False)),
                "current_task": r.get("current_task", ""),
                "speed": {"lin": st.get("speed_lin"), "ang": st.get("speed_ang")},
                "goal": st.get("goal"),
                "path": st.get("plan"),
                "obstacle": st.get("obstacle"),
                "scan_min": st.get("scan_min"),
                "localization": "ok" if st.get("localization_init") else "waiting",
                "estop": st.get("estop", False),
                "paused": st.get("paused", False),
                "disabled": st.get("disabled", False),
                "robot_type": r.get("robot_type", ""),
                "payload_capacity": r.get("payload_capacity"),
                "workload": r.get("workload"),
                "max_speed": r.get("max_speed"),
                "heartbeat_age": (
                    round(now - r.get("last_seen", now), 1)
                    if r.get("last_seen")
                    else None
                ),
            }
            for a in self._analytics.get("robots", []):
                if a.get("robot_id") == rid:
                    entry.update(
                        {
                            "active_time": a.get("active_time"),
                            "idle_time": a.get("idle_time"),
                            "charging_time": a.get("charging_time"),
                            "battery_usage": a.get("battery_usage"),
                            "distance": a.get("distance_traveled"),
                            "completed_tasks": a.get("completed_tasks"),
                        }
                    )
                    break
            for res in self._reservations.get("reservations", []):
                if res.get("robot_id") == rid:
                    entry["reservation"] = {
                        "task_id": res.get("task_id"),
                        "segment_index": res.get("segment_index"),
                        "segments_reserved": res.get("segments_reserved"),
                        "total_segments": res.get("total_segments"),
                        "head_on": res.get("head_on"),
                    }
                    break
            robots.append(entry)
            # Fleet-state counters derive from the authoritative exec_state.
            if status == "OFFLINE":
                offline += 1
            elif exec_state == "CHARGING":
                charging += 1
            elif exec_state and exec_state != "IDLE" and exec_state != "UNKNOWN":
                active += 1
            elif r.get("current_task"):
                active += 1
            else:
                idle += 1

        a_robots = self._analytics.get("robots", [])
        total_time = sum(
            a.get("active_time", 0.0)
            + a.get("idle_time", 0.0)
            + a.get("charging_time", 0.0)
            for a in a_robots
        )
        active_time = sum(a.get("active_time", 0.0) for a in a_robots)
        utilization = round(active_time / total_time, 3) if total_time > 0 else None
        an_fleet = self._analytics.get("fleet", {})
        fleet_mon_tasks = self._fleet_monitor.get("tasks", {})

        tasks = self._collect_tasks()
        stations = []
        for rid, xy in self._settings["stations"].items():
            if rid == "node":
                continue
            stations.append({"robot": rid, "x": xy[0], "y": xy[1]})

        with self._lock:
            events = self._events[:80]
            alerts = sorted(self._alerts.values(), key=lambda a: a["ts"], reverse=True)

        return {
            "timestamp": round(now, 3),
            "control_center": {
                "http_port": self._http_port,
                "ws_port": self._ws_port,
                "push_period": self._push_period,
                "backend_url": self._backend_url,
                "backend_ok": self._backend_ok,
            },
            "fleet": {
                "total": len(robots),
                "active": active,
                "idle": idle,
                "charging": charging,
                "offline": offline,
                "completed": fleet_mon_tasks.get(
                    "completed", an_fleet.get("total_completed_tasks", 0)
                ),
                "queue_length": fleet_mon_tasks.get("queued", 0),
            },
            "analytics": {
                "avg_task_duration": an_fleet.get("avg_task_duration"),
                "avg_queue_wait": an_fleet.get("avg_queue_wait"),
                "avg_reservation_wait": an_fleet.get("avg_reservation_wait"),
                "utilization": utilization,
                "total_distance": round(
                    sum(a.get("distance_traveled", 0.0) for a in a_robots), 2
                ),
                "total_battery_usage": round(
                    sum(a.get("battery_usage", 0.0) for a in a_robots), 1
                ),
                "throughput": an_fleet.get("total_completed_tasks"),
                "task_duration_samples": an_fleet.get("task_duration_samples"),
                "queue_wait_samples": an_fleet.get("queue_wait_samples"),
                "reservation_wait_samples": an_fleet.get("reservation_wait_samples"),
            },
            "robots": robots,
            "tasks": tasks,
            "reservations": {
                "list": [
                    {
                        "robot_id": r.get("robot_id"),
                        "task_id": r.get("task_id"),
                        "segment_index": r.get("segment_index"),
                        "segments_reserved": r.get("segments_reserved"),
                        "total_segments": r.get("total_segments"),
                        "head_on": r.get("head_on"),
                        "cells": r.get("cells"),
                    }
                    for r in self._reservations.get("reservations", [])
                ],
                "queue": [
                    {"robot_id": p.get("robot_id"), "task_id": p.get("task_id")}
                    for p in self._reservations.get("pending_dispatches", [])
                ]
                + [
                    {"robot_id": "waiting", "task_id": t}
                    for t in self._reservations.get("waiting_tasks", [])
                ]
                + [
                    {"robot_id": "—", "task_id": t}
                    for t in self._reservations.get("retry_tasks", [])
                ],
                "pending_count": (
                    len(self._reservations.get("pending_dispatches", []))
                    + sum(len(q) for q in self._reservations.get("robot_queues", {}).values())
                    + len(self._reservations.get("waiting_tasks", []))
                ),
                "retry_count": len(self._reservations.get("retry_tasks", [])),
            },
            "events": events,
            "alerts": {
                "active": alerts,
                "history": self._alert_history[:100],
            },
            "settings": self._settings,
            "charging_stations": stations,
            "map": self._map,
        }

    def _collect_tasks(self):
        """Merge bridge-tracked tasks into the UI task list + counts."""
        counts = {"pending": 0, "running": 0, "completed": 0, "cancelled": 0}
        tasks = []
        for tid, t in self._tasks.items():
            status = t.get("status", "PENDING")
            row = {
                "id": tid,
                "status": status,
                "robot": t.get("robot", ""),
                "priority": t.get("priority", 1),
                "pickup": t.get("pickup"),
                "dropoff": t.get("dropoff"),
                "first_seen": t.get("first_seen"),
            }
            if status in ("RUNNING", "ACTIVE"):
                counts["running"] += 1
            elif status == "COMPLETED":
                counts["completed"] += 1
            elif status == "CANCELLED":
                counts["cancelled"] += 1
            else:
                counts["pending"] += 1
            tasks.append(row)
        tasks.sort(key=lambda x: x.get("first_seen") or 0)
        return {"counts": counts, "list": tasks[-200:]}

    # ════════════════════════════════════════════════════════════
    # WebSocket server
    # ════════════════════════════════════════════════════════════

    def _run_ws_server(self):
        import asyncio

        import websockets

        async def _push_loop():
            while True:
                await asyncio.sleep(self._push_period)
                try:
                    payload = json.dumps({"type": "state", "data": self._snapshot()})
                except Exception:
                    continue
                for ws in list(self._ws_clients):
                    try:
                        await ws.send(payload)
                    except Exception:
                        try:
                            self._ws_clients.remove(ws)
                        except ValueError:
                            pass

        async def _handler(ws):
            self._ws_clients.append(ws)
            try:
                await ws.send(json.dumps({"type": "state", "data": self._snapshot()}))
                async for _ in ws:
                    pass
            finally:
                try:
                    self._ws_clients.remove(ws)
                except ValueError:
                    pass

        async def _main():
            async with websockets.serve(_handler, "0.0.0.0", self._ws_port):
                await _push_loop()

        asyncio.run(_main())

    # ════════════════════════════════════════════════════════════
    # HTTP server
    # ════════════════════════════════════════════════════════════

    def _resolve_web_dir(self):
        if self._web_dir and os.path.isdir(self._web_dir):
            return self._web_dir
        try:
            from ament_index_python.packages import get_package_share_directory

            share = os.path.join(
                get_package_share_directory("warehouse_bringup"), "web"
            )
            if os.path.isdir(share):
                return share
        except Exception:
            pass
        # Fall back to the source tree (development).
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (
            os.path.join(here, "..", "..", "web"),
            os.path.join(here, "..", "..", "..", "web"),
        ):
            if os.path.isdir(cand):
                return cand
        return None

    def _make_handler(self):
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    if self.path == "/" or self.path.startswith("/index"):
                        self._serve_file("index.html", "text/html; charset=utf-8")
                    elif self.path == "/styles.css":
                        self._serve_file("styles.css", "text/css; charset=utf-8")
                    elif self.path.startswith("/js/"):
                        self._serve_file(
                            self.path.lstrip("/"),
                            "application/javascript; charset=utf-8",
                        )
                    elif self.path.startswith("/api/state"):
                        self._json(json.dumps(node._snapshot()))
                    elif self.path.startswith("/api/events/export"):
                        self._export_events()
                    elif self.path.startswith("/api/events"):
                        self._json(
                            json.dumps(node._filtered_events(self._query_params()))
                        )
                    elif self.path.startswith("/api/alerts"):
                        self._json(
                            json.dumps(
                                {
                                    "active": node._alerts_active(),
                                    "history": node._alerts_history(),
                                }
                            )
                        )
                    elif self.path.startswith("/api/backend/"):
                        # Proxy persistent history from the production backend.
                        kind = self.path.split("/api/backend/", 1)[1].split("?")[0]
                        limit = self._query_params().get("limit", "50")
                        target = {
                            "robots": "/api/robots",
                            "tasks": f"/api/tasks?limit={limit}",
                            "events": f"/api/events?limit={limit}",
                            "alerts": "/api/alerts",
                            "analytics": "/api/analytics",
                            "monitoring": "/api/monitoring",
                            "components": "/api/health/components",
                        }.get(kind)
                        if target is None:
                            self._json(
                                json.dumps({"ok": False, "error": "unknown kind"})
                            )
                        else:
                            data = node._backend_get(target)
                            if data is None:
                                self._json(
                                    json.dumps(
                                        {"ok": False, "error": "backend unreachable"}
                                    )
                                )
                            else:
                                self._json(json.dumps({"ok": True, "data": data}))
                    elif self.path.startswith("/api/settings"):
                        self._json(json.dumps(node._settings))
                    elif self.path.startswith("/api/"):
                        self._json(json.dumps({"ok": False, "error": "not found"}))
                    else:
                        # Static assets (app.js, styles.css, js/*, icons…): serve
                        # the file; fall back to index.html (SPA) when missing.
                        name = self.path.split("?", 1)[0].lstrip("/") or "index.html"
                        if not self._serve_file(name, self._ctype(name)):
                            self._serve_file("index.html", "text/html; charset=utf-8")
                except Exception as e:
                    self._json(json.dumps({"ok": False, "error": str(e)}))

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(length).decode() or "{}")
                except Exception:
                    body = {}
                if self.path.startswith("/api/command"):
                    result = node.handle_command(body)
                    self._json(json.dumps(result))
                elif self.path.startswith("/api/alerts/ack"):
                    node.ack_alert(body.get("id", ""))
                    self._json(json.dumps({"ok": True}))
                elif self.path.startswith("/api/settings"):
                    result = node.apply_setting(
                        body.get("group", ""), body.get("key", ""), body.get("value")
                    )
                    self._json(json.dumps(result))
                else:
                    self._json(json.dumps({"ok": False, "error": "not found"}))

            # ── helpers ──
            def _query_params(self):
                import urllib.parse

                q = (
                    urllib.parse.parse_qs(self.path.split("?", 1)[1])
                    if "?" in self.path
                    else {}
                )
                return {k: v[0] for k, v in q.items()}

            def _serve_file(self, name, ctype):
                root = node._resolve_web_dir()
                if not root:
                    self._json(json.dumps({"ok": False, "error": "web assets missing"}))
                    return False
                path = os.path.normpath(os.path.join(root, name))
                if not path.startswith(os.path.normpath(root)) or not os.path.isfile(
                    path
                ):
                    return False
                data = open(path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
                return True

            @staticmethod
            def _ctype(name):
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                return {
                    "html": "text/html; charset=utf-8",
                    "css": "text/css; charset=utf-8",
                    "js": "application/javascript; charset=utf-8",
                    "mjs": "application/javascript; charset=utf-8",
                    "json": "application/json",
                    "svg": "image/svg+xml",
                    "png": "image/png",
                    "ico": "image/x-icon",
                }.get(ext, "application/octet-stream")

            def _export_events(self):
                params = self._query_params()
                rows = node._filtered_events(params)
                fmt = params.get("format", "json")
                if fmt == "csv":
                    header = "timestamp,severity,type,robot,message"
                    lines = [header]
                    for e in rows:
                        msg = e["message"].replace(",", " ").replace('"', "'")
                        lines.append(
                            f"{e['ts']},{e['severity']},{e['type']},"
                            f"{e['robot']},\"{msg}\""
                        )
                    body = "\n".join(lines)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="warehouse_events.csv"',
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body.encode())
                else:
                    self._json(json.dumps(rows))

            def _json(self, body):
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                pass  # quiet

        return Handler

    def _alerts_active(self):
        with self._lock:
            return sorted(self._alerts.values(), key=lambda a: a["ts"], reverse=True)

    def _alerts_history(self):
        return self._alert_history[:100]

    def _filtered_events(self, params):
        etype = params.get("type")
        severity = params.get("severity")
        q = (params.get("q") or "").lower()
        limit = int(params.get("limit", 100))
        with self._lock:
            rows = self._events[:500]
        out = []
        for e in rows:
            if etype and e["type"] != etype:
                continue
            if severity and e["severity"] != severity:
                continue
            if (
                q
                and q
                not in (
                    e["message"]
                    + " "
                    + e["type"]
                    + " "
                    + e["robot"]
                    + " "
                    + e["severity"]
                ).lower()
            ):
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out


def main():
    rclpy.init(args=sys.argv[1:] if len(sys.argv) > 1 else None)
    node = ControlCenterNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
