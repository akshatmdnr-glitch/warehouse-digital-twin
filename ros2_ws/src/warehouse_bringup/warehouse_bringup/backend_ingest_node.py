"""ROS -> Backend ingest bridge.

A thin observer that forwards existing warehouse topics to the production
backend (/api/ingest) and relays operator-created tasks from the backend into
ROS. All business logic (event derivation, analytics, alerts, persistence)
lives in the backend service — this node keeps ROS isolated from it.

Subscribes (observation only): /fleet_status, /fleet_monitor, /analytics,
    /reservation_status, /recovery_event, /task_assignment, /battery_status,
    /robot_pose, and per-robot /task_status (created dynamically).

Publishes (task relay): /add_task, /cancel_task.

Parameters:
    backend_url      base URL of the backend, e.g. http://localhost:8090
    ingest_period    seconds between batch POSTs (default 1.0)
    poll_period      seconds between pending-task polls (default 1.0)
    bridge_username  backend user for the ingest token (default admin)
    bridge_password  password for the ingest token (default admin)
"""

import json
import time
import urllib.error
import urllib.request

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

GLOBAL_TOPICS = (
    "/fleet_status",
    "/fleet_monitor",
    "/analytics",
    "/reservation_status",
    "/recovery_event",
    "/task_assignment",
    "/battery_status",
    "/robot_pose",
)


class BackendIngestNode(Node):
    def __init__(self):
        super().__init__("backend_ingest")
        self.declare_parameter("backend_url", "http://localhost:8090")
        self.declare_parameter("ingest_period", 1.0)
        self.declare_parameter("poll_period", 1.0)
        self.declare_parameter("bridge_username", "admin")
        self.declare_parameter("bridge_password", "admin")

        self._base = self.get_parameter("backend_url").value.rstrip("/")
        self._ingest_period = float(self.get_parameter("ingest_period").value)
        self._poll_period = float(self.get_parameter("poll_period").value)
        self._user = self.get_parameter("bridge_username").value
        self._password = self.get_parameter("bridge_password").value

        # Latest observed snapshots.
        self._fleet = {"robots": [], "robot_count": 0}
        self._monitor = {}
        self._analytics = {}
        self._reservations = {}
        self._battery = {}
        self._recovery_events = []
        self._assignments = {}  # task_id -> robot_id (from /task_assignment)
        self._per_robot_tasks = {}  # robot_id -> task_status list
        self._poses = {}  # robot_id -> (x, y, yaw)

        self._token = None
        self._last_ingest = 0.0
        self._assignment_events = []

        for topic in GLOBAL_TOPICS:
            self.create_subscription(String, topic, self._make_cb(topic), 10)
        self._entities = {}

        self._add_task_pub = self.create_publisher(String, "/add_task", 10)
        self._cancel_pub = self.create_publisher(String, "/cancel_task", 10)

        self.create_timer(self._ingest_period, self._ingest_tick)
        self.create_timer(self._poll_period, self._relay_tick)

        self.get_logger().info(
            f"Backend ingest ready -> {self._base} "
            f"(ingest {self._ingest_period}s, poll {self._poll_period}s)"
        )

    # ── topic observers ────────────────────────────────────────

    def _make_cb(self, topic):
        def cb(msg):
            # CSV-formatted topics (not JSON).
            if topic == "/robot_pose":
                self._parse_pose(msg.data)
                return
            if topic == "/task_assignment":
                self._parse_assignment(msg.data)
                return
            try:
                data = json.loads(msg.data)
            except (ValueError, TypeError):
                return
            if topic == "/fleet_status":
                self._fleet = data
                for r in data.get("robots", []):
                    self._ensure_task_entities(
                        r.get("robot_id"), r.get("namespace", "")
                    )
            elif topic == "/fleet_monitor":
                self._monitor = data
            elif topic == "/analytics":
                self._analytics = data
            elif topic == "/reservation_status":
                self._reservations = data
            elif topic == "/recovery_event":
                self._recovery_events.append(data)
                if len(self._recovery_events) > 200:
                    del self._recovery_events[:-200]
            elif topic == "/task_assignment":
                self._parse_assignment(msg.data)
            elif topic == "/battery_status":
                self._battery[data.get("robot_id", "")] = data
            elif topic == "/robot_pose":
                self._parse_pose(msg.data)

        return cb

    def _parse_assignment(self, raw):
        parts = [p.strip() for p in raw.strip().split(",")]
        if len(parts) >= 2 and parts[0] not in ("NONE", ""):
            tid, robot = parts[1], parts[0]
            if tid not in self._assignments:
                self._assignment_events.append(
                    {
                        "type": "task_assigned",
                        "severity": "info",
                        "robot": robot,
                        "message": f"Task {tid} assigned to {robot}",
                        "ts": time.time(),
                    }
                )
                if len(self._assignment_events) > 200:
                    del self._assignment_events[:-200]
            self._assignments[tid] = robot

    def _parse_pose(self, raw):
        parts = raw.strip().split(",")
        if len(parts) >= 3:
            try:
                self._poses[parts[0]] = (
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]) if len(parts) >= 4 else 0.0,
                )
            except ValueError:
                pass

    def _ensure_task_entities(self, robot_id, namespace):
        if robot_id in self._entities:
            return
        ns = (namespace or "").strip("/")

        def on_tasks(rid):
            def cb(msg):
                try:
                    self._per_robot_tasks[rid] = json.loads(msg.data)
                except (ValueError, TypeError):
                    pass

            return cb

        topic = f"/{ns}/task_status" if ns else "/task_status"
        sub = self.create_subscription(String, topic, on_tasks(robot_id), 10)
        self._entities[robot_id] = sub

    # ── batch assembly ─────────────────────────────────────────

    def _build_batch(self):
        now = time.time()
        robots = []
        for r in self._fleet.get("robots", []):
            rid = r.get("robot_id")
            pose = self._poses.get(rid)
            bat = self._battery.get(rid)
            robots.append(
                {
                    **r,
                    "x": pose[0] if pose else r.get("x"),
                    "y": pose[1] if pose else r.get("y"),
                    "yaw": pose[2] if pose else r.get("yaw"),
                    "battery": bat.get("battery") if bat else r.get("battery"),
                    "charging": (
                        bool(bat.get("charging")) if bat else bool(r.get("charging"))
                    ),
                    "last_seen": r.get("last_seen", now),
                }
            )

        tasks = []
        for rid, status_list in self._per_robot_tasks.items():
            for t in status_list:
                tasks.append(
                    {
                        "task_id": t.get("id"),
                        "status": t.get("status"),
                        "robot_id": rid,
                        "priority": t.get("priority", 1),
                        "pickup": t.get("pickup"),
                        "dropoff": t.get("dropoff"),
                    }
                )
        for tid, rid in self._assignments.items():
            if tid not in {t["task_id"] for t in tasks}:
                tasks.append(
                    {
                        "task_id": tid,
                        "status": "ASSIGNED",
                        "robot_id": rid,
                        "priority": 1,
                        "pickup": None,
                        "dropoff": None,
                    }
                )

        reservations = [
            {
                "robot_id": r.get("robot_id"),
                "task_id": r.get("task_id"),
                "status": "ACTIVE",
                "segments": r.get("total_segments", 0),
                "head_on": bool(r.get("head_on")),
            }
            for r in self._reservations.get("reservations", [])
        ]

        batch = {
            "ts": now,
            "robots": robots,
            "positions": [
                {"robot_id": rid, "x": p[0], "y": p[1], "yaw": p[2], "ts": now}
                for rid, p in self._poses.items()
            ],
            "batteries": [
                {
                    "robot_id": rid,
                    "battery": b.get("battery"),
                    "charging": b.get("charging"),
                    "ts": now,
                }
                for rid, b in self._battery.items()
            ],
            "tasks": tasks,
            "fleet": {
                "total_robots": len(robots),
                "online_robots": sum(1 for r in robots if r.get("status") == "ONLINE"),
                "offline_robots": sum(
                    1 for r in robots if r.get("status") == "OFFLINE"
                ),
                "idle_robots": sum(
                    1
                    for r in robots
                    if r.get("status") == "ONLINE" and not r.get("current_task")
                ),
                "busy_robots": sum(
                    1
                    for r in robots
                    if r.get("status") == "ONLINE" and r.get("current_task")
                ),
                "charging_robots": sum(1 for r in robots if r.get("charging")),
                **self._monitor.get("tasks", {}),
            },
            "queue": [
                {
                    "robot_id": p.get("robot_id"),
                    "task_id": p.get("task_id"),
                    "event": "queued",
                    "ts": now,
                }
                for p in self._reservations.get("pending_dispatches", [])
            ],
            "reservations": reservations,
            "events": self._recovery_events + self._assignment_events,
            "analytics": self._analytics or None,
        }
        self._recovery_events = []
        self._assignment_events = []
        return batch

    # ── HTTP helpers ───────────────────────────────────────────

    def _ensure_token(self):
        if self._token:
            return self._token
        body = json.dumps({"username": self._user, "password": self._password}).encode()
        req = urllib.request.Request(
            f"{self._base}/api/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                self._token = json.loads(resp.read())["token"]
        except Exception as e:
            self.get_logger().warn(f"Backend login failed: {e}")
            return None
        return self._token

    def _post(self, path, payload=None, method="POST"):
        token = self._ensure_token()
        if not token:
            return None
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self._base}{path}",
            data=data,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception as e:
            self._token = None  # force re-login next tick
            self.get_logger().warn(f"Backend {path} failed: {e}")
            return None

    # ── timers ─────────────────────────────────────────────────

    def _ingest_tick(self):
        if not self._base:
            return  # no backend configured
        now = time.time()
        if now - self._last_ingest < self._ingest_period:
            return
        self._last_ingest = now
        result = self._post("/api/ingest", self._build_batch())
        if result is None:
            return
        self._last_ingest = now
        result = self._post("/api/ingest", self._build_batch())
        if result is None:
            return

    def _relay_tick(self):
        """Publish backend PENDING tasks to ROS (/add_task) and mark ASSIGNED."""
        if not self._base:
            return  # no backend configured
        pending = self._post("/api/tasks?status=PENDING", method="GET")
        if not isinstance(pending, list):
            return
        for t in pending:
            tid = t.get("task_id")
            if not tid or tid in self._assignments:
                continue
            msg = String()
            msg.data = (
                f'{tid},{t.get("pickup_x", 0)},{t.get("pickup_y", 0)},'
                f'{t.get("dropoff_x", 0)},{t.get("dropoff_y", 0)},'
                f'{t.get("priority", 1)},{t.get("required_payload", 0)}'
            )
            self._add_task_pub.publish(msg)
            self._assignments[tid] = ""
            self.get_logger().info(f"Relayed task {tid} to ROS /add_task")
            self._post(
                f"/api/tasks/{tid}",
                {"status": "ASSIGNED", "robot_id": t.get("robot_id", "")},
                method="PATCH",
            )


def main():
    rclpy.init()
    node = BackendIngestNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
