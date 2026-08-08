"""Fleet Manager — maintains a registry of all known warehouse robots.

Subscribes: /robot_registration (std_msgs/String)
            /robot_heartbeat (std_msgs/String, robot_id)
            /robot_pose (std_msgs/String, robot_id,x,y)
            /add_task (std_msgs/String, CSV task with required_payload)
Publishes:  /fleet_status (std_msgs/String, transient-local)
            /task_assignment (std_msgs/String, CSV robot_id + task)
            /reservation_status (std_msgs/String, JSON)
            /recovery_event (std_msgs/String, JSON)
            /fleet_monitor (std_msgs/String, JSON — monitoring summary)
            /dispatch_decision (std_msgs/String, JSON — scoring debug)

Registration format (CSV): robot_id,status,current_task
                           [,payload_capacity[,max_speed[,robot_type
                           [,workload[,priority[,battery[,charging[,namespace
                           [,exec_state[,moving]]]]]]]]]]
  status:           ONLINE or OFFLINE (case-insensitive)
  current_task:     active task id, or empty
  payload_capacity: kg (float, default 0.0)
  max_speed:        m/s (float, default 0.0)
  robot_type:       str (default 'unknown')
  workload:         queued-task count (int, default 0)
  priority:         dispatch priority, higher = preferred (float, default 0.0)
  battery:          battery percentage 0-100 (float, default 100.0)
  charging:         1 if physically charging else 0 (int, default 0)
  namespace:        ROS namespace (str, default '')
  exec_state:       authoritative execution state (str, default '')
  moving:           1 if driving (path+velocity+pose change) else 0

Battery-aware scheduling: robots below low_battery_threshold (or currently
charging) are never selected for new work. If a robot's battery reaches
critical_battery_threshold while it owns a task, the task is safely released
and re-dispatched to another eligible robot and the robot is marked charging;
once it recharges (battery >= low threshold, charging flag cleared) it becomes
eligible again.
  workload:         queued-task count (int, default 0)
  priority:         dispatch priority, higher = preferred (float, default 0.0)

Pose format: robot_id,x,y (metres, in map frame)

Heartbeat format: robot_id

Task format (CSV): task_id,pickup_x,pickup_y,dropoff_x,dropoff_y
                   [,priority[,required_payload]]
  priority:        0=Low, 1=Normal, 2=High (default 1)
  required_payload: kg (float, default 0.0)

Assignment format (CSV): robot_id,task_id,pickup_x,pickup_y,
                         dropoff_x,dropoff_y,priority,required_payload

Assignment policy: EVERY eligible robot (ONLINE, not charging, battery above
the low threshold, payload_capacity >= required_payload) is scored for each
new task. Idle robots are always preferred over busy ones (a task is never
assigned to a robot already executing another task while an idle robot
exists). Among idle robots the score = distance_to_pickup + queue_penalty +
battery_penalty; busy robots (only chosen when nobody is idle) additionally
pay a current-task penalty plus an ETA penalty so the robot that will finish
first wins. The lowest score wins (ties broken by robot_id). Weights:
score_w_distance, score_w_queue, score_w_battery, score_w_current, score_w_eta.

Each robot owns its own task queue. A task is committed to a robot's queue
immediately but only dispatched (handed to the robot's task manager) once that
robot is idle and its route is free. When a robot finishes, the scheduler is
notified immediately: it assigns the highest-priority waiting task and
rebalances by moving the highest-priority queued (not-started) task from a
busy robot to the now-idle robot. Only tasks already in execution are
non-transferable.

Reservations (multi-robot coordination): each route is divided into
configurable segments (segment_size cells each). A robot reserves only a
sliding window of segments ahead of its current position (traffic_lookahead)
and releases segments behind it as it advances, so two robots never occupy
the same segment at once. Conflicts (including head-on corridor and
intersection conflicts) delay dispatch or make a robot wait until the segment
is released; waiting robots retry automatically each control tick. Head-on
routes are serialized at dispatch so robots can never meet mid-corridor.
A task_id can never hold more than one reservation.

Fault recovery: when a robot is detected OFFLINE via heartbeat timeout, its
reservations are released, its pending dispatches are returned to the queue,
and every unfinished task is re-dispatched to the next eligible robot.
Recovery keeps a task in at most one place (reservation, route-queue, or
retry-queue), so a task is never dispatched to two robots at once. Recovery
events are published on /recovery_event.

The registry is keyed by robot_id so duplicate registrations update the
existing entry (reconnection) instead of creating a new one. Liveness is
derived from /robot_heartbeat: if no heartbeat is received within
heartbeat_timeout seconds the robot is marked OFFLINE; a resumed heartbeat
marks it ONLINE again.
"""

import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String

VALID_STATUSES = ("ONLINE", "OFFLINE")


class FleetManagerNode(Node):
    def __init__(self):
        super().__init__("fleet_manager")
        self.declare_parameter("publish_rate", 1.0)
        self.declare_parameter("heartbeat_timeout", 3.0)
        self.declare_parameter("cell_size", 1.0)
        self.declare_parameter("reservation_buffer", 0)
        self.declare_parameter("score_w_distance", 1.0)
        self.declare_parameter("score_w_queue", 1.0)
        self.declare_parameter("score_w_battery", 1.0)
        self.declare_parameter("score_w_current", 10.0)
        self.declare_parameter("score_w_eta", 1.0)
        self.declare_parameter("low_battery_threshold", 30.0)
        self.declare_parameter("critical_battery_threshold", 15.0)
        self.declare_parameter("segment_size", 2)
        self.declare_parameter("traffic_lookahead", 1)

        self._publish_rate = self.get_parameter("publish_rate").value
        self._heartbeat_timeout = self.get_parameter("heartbeat_timeout").value
        self._cell_size = self.get_parameter("cell_size").value
        self._reservation_buffer = int(self.get_parameter("reservation_buffer").value)
        self._w_distance = self.get_parameter("score_w_distance").value
        self._w_queue = self.get_parameter("score_w_queue").value
        self._w_battery = self.get_parameter("score_w_battery").value
        self._w_current = self.get_parameter("score_w_current").value
        self._w_eta = self.get_parameter("score_w_eta").value
        self._low_battery = self.get_parameter("low_battery_threshold").value
        self._critical_battery = self.get_parameter("critical_battery_threshold").value
        self._segment_size = max(1, int(self.get_parameter("segment_size").value))
        self._lookahead = max(0, int(self.get_parameter("traffic_lookahead").value))
        self._robots = {}  # robot_id -> registry entry

        # ── Per-robot task queues (multi-robot dispatch) ──
        # Every robot owns its own queue of committed tasks. A task lives here
        # until it is actually handed to that robot's task manager (dispatch).
        # Queued-but-not-started tasks are transferable during rebalancing.
        self._robot_queues = {}  # robot_id -> [task, ...] (committed, queued)
        self._waiting_tasks = []  # tasks with no eligible robot yet
        self._task_seq = 0  # global FIFO counter for deterministic ordering

        # Reservation state (multi-robot coordination)
        self._reservations = (
            {}
        )  # robot_id -> {task_id, task, cells:set, activated:bool}
        self._cell_owners = {}  # (cx,cy) -> robot_id
        self._retry_tasks = []  # recovered tasks waiting for an eligible robot

        # Monitoring counters
        self._tasks_submitted = 0
        self._tasks_completed = 0
        self._reservations_released = 0

        self._reg_sub = self.create_subscription(
            String, "/robot_registration", self._registration_callback, 10
        )
        self._hb_sub = self.create_subscription(
            String, "/robot_heartbeat", self._heartbeat_callback, 10
        )
        self._pose_sub = self.create_subscription(
            String, "/robot_pose", self._pose_callback, 10
        )
        self._task_sub = self.create_subscription(
            String, "/add_task", self._task_callback, 10
        )

        qos = QoSProfile(depth=10, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._fleet_pub = self.create_publisher(String, "/fleet_status", qos)
        self._assignment_pub = self.create_publisher(String, "/task_assignment", 10)
        self._reservation_pub = self.create_publisher(String, "/reservation_status", 10)
        self._recovery_pub = self.create_publisher(String, "/recovery_event", 10)
        self._monitor_pub = self.create_publisher(String, "/fleet_monitor", 10)
        self._decision_pub = self.create_publisher(String, "/dispatch_decision", 10)
        self._cancel_pub = self.create_publisher(String, "/cancel_task", 10)

        self._timer = self.create_timer(1.0 / self._publish_rate, self._control_loop)

        self.get_logger().info(
            f"Fleet Manager ready (publish_rate={self._publish_rate} Hz, "
            f"heartbeat_timeout={self._heartbeat_timeout} s, "
            f"cell_size={self._cell_size} m, buffer={self._reservation_buffer})"
        )

    def _registration_callback(self, msg):
        try:
            parts = [p.strip() for p in msg.data.strip().split(",")]
            if len(parts) < 2:
                self.get_logger().error(
                    f"Invalid registration (need >=2 fields): {msg.data}"
                )
                return
            robot_id = parts[0]
            status = parts[1].upper()
            current_task = parts[2].strip() if len(parts) >= 3 else ""
            payload_capacity = float(parts[3]) if len(parts) >= 4 else 0.0
            max_speed = float(parts[4]) if len(parts) >= 5 else 0.0
            robot_type = parts[5].strip() if len(parts) >= 6 else "unknown"
            workload = float(parts[6]) if len(parts) >= 7 else 0.0
            priority = float(parts[7]) if len(parts) >= 8 else 0.0
            battery = float(parts[8]) if len(parts) >= 9 else 100.0
            charging = int(parts[9]) if len(parts) >= 10 else 0
            namespace = parts[10].strip() if len(parts) >= 11 else ""
            exec_state = parts[11].strip() if len(parts) >= 12 else ""
            moving = bool(int(parts[12])) if len(parts) >= 13 else False
            if status not in VALID_STATUSES:
                self.get_logger().warn(
                    f'Unknown status "{parts[1]}" for {robot_id}, ignoring'
                )
                return
            if (
                payload_capacity < 0.0
                or max_speed < 0.0
                or workload < 0.0
                or priority < 0.0
                or not (0.0 <= battery <= 100.0)
                or charging not in (0, 1)
            ):
                self.get_logger().error(f"Invalid field for {robot_id}, ignoring")
                return
        except (ValueError, IndexError) as e:
            self.get_logger().error(f"Registration parse error: {e}")
            return

        now = time.time()
        entry = self._robots.get(robot_id)
        if entry is None:
            self._robots[robot_id] = {
                "robot_id": robot_id,
                "status": status,
                "current_task": current_task,
                "last_seen": now,
                "payload_capacity": payload_capacity,
                "max_speed": max_speed,
                "robot_type": robot_type,
                "workload": workload,
                "priority": priority,
                "battery": battery,
                "charging": bool(charging),
                "namespace": namespace,
                "exec_state": exec_state,
                "moving": moving,
                "x": None,
                "y": None,
                "yaw": None,
            }
            self.get_logger().info(f"Robot {robot_id} registered ({status})")
        else:
            # Reconnection / update — refresh the existing entry, never duplicate.
            if entry["status"] != status:
                self.get_logger().info(
                    f'Robot {robot_id} status: {entry["status"]} → {status}'
                )
            entry["status"] = status
            entry["current_task"] = current_task
            entry["payload_capacity"] = payload_capacity
            entry["max_speed"] = max_speed
            entry["robot_type"] = robot_type
            entry["workload"] = workload
            entry["priority"] = priority
            entry["battery"] = battery
            entry["charging"] = bool(charging)
            entry["exec_state"] = exec_state
            entry["moving"] = moving
            if namespace:
                entry["namespace"] = namespace
            entry["last_seen"] = now

        self._publish_fleet()  # immediate refresh
        self._check_reservation_release(robot_id, current_task)

    def _pose_callback(self, msg):
        try:
            parts = [p.strip() for p in msg.data.strip().split(",")]
            if len(parts) < 3:
                self.get_logger().error(
                    f"Invalid pose format (need robot_id,x,y): {msg.data}"
                )
                return
            robot_id = parts[0]
            x = float(parts[1])
            y = float(parts[2])
            yaw = float(parts[3]) if len(parts) >= 4 else 0.0
        except (ValueError, IndexError) as e:
            self.get_logger().error(f"Pose parse error: {e}")
            return
        entry = self._robots.get(robot_id)
        if entry is None:
            self.get_logger().warn(f"Pose from unregistered robot {robot_id}, ignoring")
            return
        entry["x"] = x
        entry["y"] = y
        entry["yaw"] = yaw

    def _check_reservation_release(self, robot_id, current_task):
        """Release a robot's reservation when it reports its task complete.

        A reservation is only released once the robot was observed executing
        the reserved task ('activated'), so a transient idle report during the
        dispatch/activation window can never free the cells prematurely.
        """
        res = self._reservations.get(robot_id)
        if res is None:
            return
        if current_task == res["task_id"]:
            res["activated"] = True
        elif current_task == "" and res["activated"]:
            self._tasks_completed += 1  # normal completion
            self._release_reservations(robot_id)
            self._publish_reservations()
            # The robot just went idle: immediately notify the scheduler so it
            # picks up its next queued task, or pulls work from another robot.
            self._rebalance()

    def _heartbeat_callback(self, msg):
        robot_id = msg.data.strip()
        if not robot_id:
            self.get_logger().error("Empty robot id in heartbeat")
            return
        entry = self._robots.get(robot_id)
        if entry is None:
            self.get_logger().warn(
                f"Heartbeat from unregistered robot {robot_id}, ignoring"
            )
            return
        entry["last_seen"] = time.time()
        if entry["status"] != "ONLINE":
            # Robot reconnected — mark ONLINE and refresh immediately.
            entry["status"] = "ONLINE"
            self.get_logger().info(f"Robot {robot_id} reconnected — ONLINE")
            self._publish_fleet()

    def _check_heartbeats(self):
        now = time.time()
        changed = False
        for robot_id, entry in self._robots.items():
            if entry["status"] == "OFFLINE":
                continue
            if now - entry["last_seen"] > self._heartbeat_timeout:
                entry["status"] = "OFFLINE"
                changed = True
                self.get_logger().warn(
                    f"Robot {robot_id}: no heartbeat for >{self._heartbeat_timeout}s "
                    f"— marked OFFLINE"
                )
                self._recover_robot(robot_id)
        if changed:
            self._publish_fleet()

    def _recover_robot(self, robot_id):
        """Recover everything a failed robot owned and re-dispatch its tasks."""
        recovered = []

        # 1. Active reservation (task being executed) — release cells.
        res = self._reservations.get(robot_id)
        if res is not None:
            recovered.append(res["task"])
            self._release_reservations(robot_id)

        # 2. Queued (not-started) tasks committed to this robot — return them
        #    to the waiting pool so another robot picks them up.
        queued = self._robot_queues.pop(robot_id, [])
        recovered.extend(queued)

        # 3. Clean the robot's commitment so it can never be re-selected.
        rob = self._robots.get(robot_id)
        if rob is not None:
            rob["current_task"] = ""

        if not recovered:
            self._publish_recovery_event(
                {"event": "robot_failed", "robot_id": robot_id, "tasks_recovered": []}
            )
            return

        self._publish_recovery_event(
            {
                "event": "robot_failed",
                "robot_id": robot_id,
                "tasks_recovered": [t["task_id"] for t in recovered],
            }
        )
        self.get_logger().warn(
            f"Robot {robot_id} failed: recovering {len(recovered)} task(s)"
        )
        for task in recovered:
            self._requeue_task(task)
        self._publish_reservations()

    def _requeue_task(self, task):
        """Re-dispatch a recovered task to the next eligible robot."""
        task_id = task["task_id"]
        if self._task_exists(task_id):
            self.get_logger().warn(
                f"Task {task_id}: already queued/reserved, skipping recovery"
            )
            return
        result = self._schedule_task(task)
        if result == "no_robot":
            self._waiting_tasks.append(task)
            self._publish_recovery_event(
                {
                    "event": "task_recovered",
                    "task_id": task_id,
                    "reason": "waiting_for_robot",
                }
            )
        else:
            self._publish_recovery_event(
                {
                    "event": "task_recovered",
                    "task_id": task_id,
                    "reason": "redispatched",
                }
            )

    def _process_retry_tasks(self):
        """Dispatch recovered tasks as soon as an eligible robot appears."""
        if not self._retry_tasks:
            return
        remaining = []
        for task in self._retry_tasks:
            result = self._schedule_task(task)
            if result == "no_robot":
                remaining.append(task)
            else:
                self._publish_recovery_event(
                    {
                        "event": "task_recovered",
                        "task_id": task["task_id"],
                        "reason": "redispatched",
                    }
                )
            # 'assigned' -> moved into the robot's per-robot queue
        self._retry_tasks = remaining

    def _control_loop(self):
        # Tunable parameters are re-read every tick so the Control Center's
        # runtime Settings editor takes effect immediately (no node restart).
        self._heartbeat_timeout = self.get_parameter("heartbeat_timeout").value
        self._cell_size = self.get_parameter("cell_size").value
        self._reservation_buffer = int(self.get_parameter("reservation_buffer").value)
        self._w_distance = self.get_parameter("score_w_distance").value
        self._w_queue = self.get_parameter("score_w_queue").value
        self._w_battery = self.get_parameter("score_w_battery").value
        self._w_current = self.get_parameter("score_w_current").value
        self._w_eta = self.get_parameter("score_w_eta").value
        self._low_battery = self.get_parameter("low_battery_threshold").value
        self._critical_battery = self.get_parameter("critical_battery_threshold").value
        self._segment_size = max(1, int(self.get_parameter("segment_size").value))
        self._lookahead = max(0, int(self.get_parameter("traffic_lookahead").value))
        self._check_heartbeats()
        self._check_battery()
        self._check_traffic()
        self._process_retry_tasks()
        self._process_pending_dispatches()  # -> _rebalance()
        self._publish_reservations()
        self._publish_fleet()

    def _check_battery(self):
        """Release work for robots that need to charge.

        A robot needs to charge when it reports `charging` (its beacon is
        physically docked on a pad) or its battery is at/below the fleet
        critical threshold. Either signal safely releases its active task so
        another eligible robot can pick it up.

        The `charging` flag itself is NEVER set here — it is owned by the
        robot's beacon, which reports it ONLY while the measured pose is inside
        the charging pad bounds. The fleet keeps low-battery robots out of new
        dispatch via the battery eligibility check in _select_robot.
        """
        for robot_id, entry in self._robots.items():
            needs_charge = (
                entry.get("charging", False)
                or entry.get("battery", 100.0) <= self._critical_battery
            )
            if not needs_charge:
                continue
            released = []
            res = self._reservations.get(robot_id)
            if res is not None:
                released.append(res["task"])
                self._release_reservations(robot_id)
            # Queued (not-started) tasks committed to this robot return to the
            # waiting pool so another robot picks them up.
            queued = self._robot_queues.pop(robot_id, [])
            released.extend(queued)
            if released:
                entry["current_task"] = ""
                self.get_logger().warn(
                    f"Robot {robot_id} critical battery — releasing "
                    f"{len(released)} task(s) for charging"
                )
                self._publish_recovery_event(
                    {
                        "event": "robot_charging",
                        "robot_id": robot_id,
                        "tasks_released": [t["task_id"] for t in released],
                    }
                )
                # Cancel the released task on the old robot's task manager so it
                # actually stops before the task is re-dispatched elsewhere.
                for t in released:
                    self._publish_cancel(robot_id, t["task_id"])
                for t in released:
                    self._requeue_task(t)
            self._publish_reservations()

    def _task_callback(self, msg):
        try:
            parts = [p.strip() for p in msg.data.strip().split(",")]
            if len(parts) < 5:
                self.get_logger().error(
                    f"Invalid task format (need >=5 fields): {msg.data}"
                )
                return
            task_id = parts[0]
            pickup = (float(parts[1]), float(parts[2]))
            dropoff = (float(parts[3]), float(parts[4]))
            priority = int(parts[5]) if len(parts) >= 6 else 1
            required_payload = float(parts[6]) if len(parts) >= 7 else 0.0
            priority = max(0, min(2, priority))  # clamp to 0-2
            if required_payload < 0.0:
                self.get_logger().error(f"Negative required_payload: {msg.data}")
                return
        except (ValueError, IndexError) as e:
            self.get_logger().error(f"Task parse error: {e}")
            return

        task = {
            "task_id": task_id,
            "pickup": pickup,
            "dropoff": dropoff,
            "priority": priority,
            "required_payload": required_payload,
        }
        if self._task_exists(task_id):
            self.get_logger().warn(f"Task {task_id}: duplicate, ignoring")
            return

        self._tasks_submitted += 1

        result = self._schedule_task(task)
        if result == "no_robot":
            self.get_logger().warn(
                f"Task {task_id}: no eligible robot yet "
                f"(required_payload={required_payload}) — waiting"
            )
            self._publish_assignment(
                "NONE", task_id, pickup, dropoff, priority, required_payload
            )

        self._publish_fleet()  # busy marking reflected immediately
        self._publish_reservations()

    # ── Multi-robot dispatcher ─────────────────────────────────

    def _eligible(self, robot):
        """A robot that can take new work right now."""
        return (
            robot["status"] == "ONLINE"
            and not robot.get("charging", False)
            and robot.get("battery", 100.0) >= self._low_battery
        )

    def _busy(self, robot):
        return bool(robot.get("current_task"))

    def _estimate_eta(self, robot):
        """Seconds until a robot frees up, including its queued (not-started)
        work. 0 if the robot is idle. Used for the 'will finish first' rule —
        a robot with queued tasks is not free after its current task."""
        rid = robot["robot_id"]
        if not self._busy(robot):
            return 0.0
        speed = max(float(robot.get("max_speed", 0.0)) or 0.22, 0.05)
        total = 0.0
        res = self._reservations.get(rid)
        if res is not None:
            n = len(res["segments"])
            remaining = n - max(0, res["segment_index"])
            total += remaining * self._segment_size * self._cell_size / speed
        # Queued tasks still need to be executed before the robot is free.
        for task in self._robot_queues.get(rid, []):
            d = math.hypot(
                task["dropoff"][0] - task["pickup"][0],
                task["dropoff"][1] - task["pickup"][1],
            )
            total += d / speed
        return max(0.0, total)

    def _schedule_task(self, task):
        """Assign a task to the best robot's queue, or the waiting pool.

        Returns 'assigned', 'no_robot' or 'queued'. The task is committed to a
        robot's per-robot queue; it is only actually dispatched (handed to the
        robot's task manager) once that robot is idle and its route is free.
        """
        task_id = task["task_id"]
        self._task_seq += 1
        task["_seq"] = self._task_seq

        eligible = [
            r for r in self._robots.values()
            if self._eligible(r) and r["payload_capacity"] >= task["required_payload"]
        ]
        if not eligible:
            self._waiting_tasks.append(task)
            return "no_robot"

        best, scored = self._select_robot(task, eligible)
        self._publish_dispatch_decision(task, best, scored)
        robot_id = best["robot_id"]
        self._robot_queues.setdefault(robot_id, []).append(task)
        self.get_logger().info(
            f"Task {task_id}: committed to {robot_id} "
            f"(queue {len(self._robot_queues[robot_id])}, busy={self._busy(best)})"
        )
        self._dispatch_next(robot_id)
        return "assigned"

    def _select_robot(self, task, eligible=None):
        """Pick the best robot for a task.

        Priority order (hard), matching the operator-facing rules:
          1. Idle robot — a task is never assigned to a robot already executing
             another task while an idle eligible robot exists;
          2. robot that will finish first (lowest ETA);
          3. robot with the smallest queue;
          4. robot nearest to the pickup;
          5. robot with the highest battery.

        Candidates are ordered by that lexicographic key. The weighted
        score (distance + queue + battery + current-task penalty) is still
        computed and used to break exact ties deterministically.
        """
        if eligible is None:
            eligible = [
                r for r in self._robots.values()
                if self._eligible(r)
                and r["payload_capacity"] >= task["required_payload"]
            ]
        if not eligible:
            return None, []

        def _key(robot):
            return (
                0 if not self._busy(robot) else 1,   # 1. idle first
                round(self._estimate_eta(robot), 3),  # 2. finish first
                len(self._robot_queues.get(robot["robot_id"], [])),  # 3. queue
                self._robot_distance(robot, task["pickup"]) or 999.0,  # 4. nearest
                -float(robot.get("battery", 100.0)),  # 5. highest battery
            )

        ordered = sorted(eligible, key=_key)
        best = ordered[0]
        scored = [
            {
                "robot": r,
                "score": round(self._score_robot(r, task), 3),
            }
            for r in ordered
        ]
        return best, scored

    def _score_robot(self, robot, task):
        """Lower is better:
        distance_to_pickup + queue_penalty + battery_penalty
        + current_task_penalty (+ ETA penalty for busy robots)."""
        dist = self._robot_distance(robot, task["pickup"])
        dist = dist if dist is not None else 20.0  # unknown pose -> worst case
        queue_len = len(self._robot_queues.get(robot["robot_id"], []))
        battery = float(robot.get("battery", 100.0))
        busy = 1 if self._busy(robot) else 0
        eta = self._estimate_eta(robot)
        return (
            self._w_distance * dist
            + self._w_queue * queue_len
            + self._w_battery * (100.0 - battery)
            + self._w_current * busy
            + self._w_eta * eta
        )

    def _dispatch_next(self, robot_id):
        """Hand the next queued task to robot_id if it is idle and the route
        is free. Returns True if a task was dispatched."""
        robot = self._robots.get(robot_id)
        queue = self._robot_queues.get(robot_id, [])
        if robot is None or self._busy(robot):
            return False
        if not self._eligible(robot):
            return False
        while queue:
            # Highest priority first (larger priority value), then FIFO.
            task = max(queue, key=lambda t: (t["priority"], -t["_seq"]))
            if self._try_dispatch(robot, task):
                queue.remove(task)
                return True
            # Route blocked — leave it queued and retry on a later tick.
            return False
        return False

    def _try_dispatch(self, robot, task):
        """Reserve the route and publish the assignment for a queued task."""
        robot_id = robot["robot_id"]
        task_id = task["task_id"]
        route_ordered = self._route_cells_ordered(task["pickup"], task["dropoff"])
        segments = self._partition_segments(route_ordered)
        route_dir = (
            task["dropoff"][0] - task["pickup"][0],
            task["dropoff"][1] - task["pickup"][1],
        )

        # Only the sliding window the robot will reserve right now must be free.
        # The whole future route is NOT pre-emptively blocked, so a second robot
        # can start on a shared corridor and follow at a safe distance instead
        # of idling until the first robot finishes its entire route.
        window_cells = set()
        for i in range(0, min(self._lookahead + 1, len(segments))):
            window_cells.update(segments[i])

        if self._has_conflict(robot_id, window_cells):
            self.get_logger().info(
                f"Task {task_id}: route window blocked, queued for {robot_id} "
                f"(waiting for reservation)"
            )
            return False
        if not self._reserve(robot_id, task, route_ordered, segments, route_dir):
            return False
        robot["current_task"] = task_id  # now executing — blocks re-assignment
        self._dispatch(
            robot_id,
            task_id,
            task["pickup"],
            task["dropoff"],
            task["priority"],
            task["required_payload"],
        )
        return True

    # ── Dynamic rebalancing ────────────────────────────────────

    def _rebalance(self):
        """Keep every available robot busy.

        * dispatch each idle robot's next queued task;
        * pull tasks out of the waiting pool as soon as an eligible robot exists;
        * move the highest-priority queued (not-started) task from a busy robot
          to an idle robot so work stays balanced.
        """
        progress = True
        while progress:
            progress = False

            # 1. Dispatch queued work for every idle robot.
            for rid in list(self._robot_queues):
                if self._dispatch_next(rid):
                    progress = True

            # 2. Assign waiting tasks to the best available robot.
            if self._waiting_tasks:
                remaining = []
                for task in self._waiting_tasks:
                    eligible = [
                        r for r in self._robots.values()
                        if self._eligible(r)
                        and r["payload_capacity"] >= task["required_payload"]
                    ]
                    if not eligible:
                        remaining.append(task)
                        continue
                    best, _ = self._select_robot(task, eligible)
                    self._robot_queues.setdefault(best["robot_id"], []).append(task)
                    self._dispatch_next(best["robot_id"])
                    progress = True
                self._waiting_tasks = remaining

            # 3. Steal the highest-priority queued task from a busy robot for
            #    any idle robot with an empty queue.
            for rid, robot in self._robots.items():
                if not self._eligible(robot) or self._busy(robot):
                    continue
                if self._robot_queues.get(rid):
                    continue  # this robot already has work lined up
                donor = self._pick_donor(rid, robot)
                if donor is None:
                    continue
                task = self._pop_best_task(donor)
                self._robot_queues.setdefault(rid, []).append(task)
                self.get_logger().info(
                    f"Task {task['task_id']}: rebalanced {donor} → {rid}"
                )
                self._dispatch_next(rid)
                progress = True
                break  # re-evaluate idle/steal conditions next pass

        if progress or self._waiting_tasks:
            self._publish_reservations()

    def _pick_donor(self, rid, robot):
        """Choose the robot with the most queued work to steal from. Only
        non-starting (queued) tasks are eligible — a task already in execution
        is never transferred."""
        best_donor = None
        best_score = -1
        for donor_id, queue in self._robot_queues.items():
            if donor_id == rid or not queue:
                continue
            donor_robot = self._robots.get(donor_id)
            if donor_robot is None or not self._busy(donor_robot):
                continue  # only steal from robots actually executing something
            if donor_robot["payload_capacity"] < queue[0]["required_payload"]:
                continue
            # Prefer the donor with the most queued tasks.
            if len(queue) > best_score:
                best_score = len(queue)
                best_donor = donor_id
        return best_donor

    def _pop_best_task(self, robot_id):
        queue = self._robot_queues[robot_id]
        task = max(queue, key=lambda t: (t["priority"], -t["_seq"]))
        queue.remove(task)
        if not queue:
            del self._robot_queues[robot_id]
        return task

    def _route_cells_ordered(self, pickup, dropoff):
        """Deterministically rasterize pickup->dropoff into an ordered cell list."""
        px, py = pickup
        dx, dy = dropoff
        cell = max(self._cell_size, 1e-3)
        cells = []
        dist = math.hypot(dx - px, dy - py)
        steps = max(1, int(math.ceil(dist / (cell / 2.0))))
        last = None
        for i in range(steps + 1):
            t = i / steps
            x = px + (dx - px) * t
            y = py + (dy - py) * t
            cx = int(math.floor(x / cell))
            cy = int(math.floor(y / cell))
            if (cx, cy) != last:
                cells.append((cx, cy))
                last = (cx, cy)
        return cells

    def _route_cells(self, pickup, dropoff):
        """Set of all route cells (used for whole-route conflict checks)."""
        return set(self._route_cells_ordered(pickup, dropoff))

    def _partition_segments(self, route_ordered):
        """Split an ordered route into consecutive reservable segments."""
        seg_size = self._segment_size
        return [
            route_ordered[i : i + seg_size]
            for i in range(0, len(route_ordered), seg_size)
        ]

    def _add_cell_with_buffer(self, cells, cx, cy):
        b = self._reservation_buffer
        for ox in range(-b, b + 1):
            for oy in range(-b, b + 1):
                cells.add((cx + ox, cy + oy))

    def _has_conflict(self, robot_id, cells):
        for c in cells:
            owner = self._cell_owners.get(c)
            if owner is not None and owner != robot_id:
                return True
        return False

    @staticmethod
    def _directions_opposed(a, b):
        dot = a[0] * b[0] + a[1] * b[1]
        return dot < 0.0

    def _has_head_on_conflict(self, route_set, route_dir):
        """Report whether another robot's active route overlaps head-on."""
        for other in self._reservations.values():
            if not (route_set & set(other["route"])):
                continue
            if self._directions_opposed(route_dir, other["route_dir"]):
                return True
        return False

    def _reserve(self, robot_id, task, route_ordered, segments, route_dir):
        if robot_id in self._reservations:
            return False  # robot already holds a reservation
        res = {
            "task_id": task["task_id"],
            "task": dict(task),
            "route": route_ordered,
            "segments": segments,
            "route_dir": route_dir,
            "segment_index": 0,
            "reserved_segment_indices": set(),
            "head_on": False,
            "activated": False,
            "cells": set(),
        }
        self._reservations[robot_id] = res
        # Reserve the initial traffic window (current + lookahead segments).
        n = len(segments)
        for i in range(0, min(self._lookahead + 1, n)):
            if self._has_conflict(robot_id, set(segments[i])):
                continue
            self._reserve_segment(robot_id, res, i)
        self._refresh_owned_cells(res)
        return True

    def _reserve_segment(self, robot_id, res, seg_idx):
        for c in res["segments"][seg_idx]:
            self._cell_owners[c] = robot_id
        res["reserved_segment_indices"].add(seg_idx)

    def _release_segment(self, robot_id, res, seg_idx):
        res["reserved_segment_indices"].discard(seg_idx)
        for c in res["segments"][seg_idx]:
            if self._cell_owners.get(c) == robot_id:
                del self._cell_owners[c]

    def _refresh_owned_cells(self, res):
        cells = set()
        for i in res["reserved_segment_indices"]:
            cells.update(res["segments"][i])
        res["cells"] = cells

    def _release_reservations(self, robot_id):
        res = self._reservations.pop(robot_id, None)
        if res is None:
            return False
        for c in res["cells"]:
            if self._cell_owners.get(c) == robot_id:
                del self._cell_owners[c]
        self._reservations_released += 1
        self.get_logger().info(
            f'Reservation released for {robot_id} (task {res["task_id"]})'
        )
        return True

    def _current_segment_index(self, entry, res):
        """Segment the robot currently occupies, based on its reported pose."""
        if entry.get("x") is None or entry.get("y") is None:
            return res["segment_index"]
        cell = (
            int(entry["x"] / max(self._cell_size, 1e-3)),
            int(entry["y"] / max(self._cell_size, 1e-3)),
        )
        idx = res["segment_index"]
        for i, seg in enumerate(res["segments"]):
            if cell in seg:
                idx = max(idx, i)
        return idx

    def _check_traffic(self):
        """Progressively reserve segments ahead and release segments behind."""
        changed = False
        for robot_id, res in list(self._reservations.items()):
            entry = self._robots.get(robot_id)
            if entry is None:
                continue
            n = len(res["segments"])
            if n == 0:
                continue
            cur = max(0, min(self._current_segment_index(entry, res), n - 1))
            res["segment_index"] = cur
            # Release segments the robot has exited.
            for i in list(res["reserved_segment_indices"]):
                if i < cur:
                    self._release_segment(robot_id, res, i)
                    changed = True
            # Try to reserve the window ahead (retried each tick).
            window = range(cur, min(cur + self._lookahead + 1, n))
            for i in window:
                if i in res["reserved_segment_indices"]:
                    continue
                seg_cells = set(res["segments"][i])
                if self._has_conflict(robot_id, seg_cells):
                    # Label head-on corridor conflicts for debugging.
                    owner = next(
                        (
                            o
                            for c in seg_cells
                            if (o := self._cell_owners.get(c)) and o != robot_id
                        ),
                        None,
                    )
                    if owner is not None:
                        ores = self._reservations.get(owner)
                        if ores is not None and self._directions_opposed(
                            res["route_dir"], ores["route_dir"]
                        ):
                            res["head_on"] = True
                    continue  # wait; retried on the next tick
                self._reserve_segment(robot_id, res, i)
                changed = True
            self._refresh_owned_cells(res)
        if changed:
            self._publish_reservations()

    def _task_exists(self, task_id):
        for queue in self._robot_queues.values():
            if any(t["task_id"] == task_id for t in queue):
                return True
        for task in self._waiting_tasks:
            if task["task_id"] == task_id:
                return True
        for res in self._reservations.values():
            if res["task_id"] == task_id:
                return True
        for task in self._retry_tasks:
            if task["task_id"] == task_id:
                return True
        return False

    def _process_pending_dispatches(self):
        """Legacy hook — route-conflict dispatch is handled by _rebalance(),
        which re-tries queued routes every control tick."""
        self._rebalance()

    def _dispatch(self, robot_id, task_id, pickup, dropoff, priority, required_payload):
        self._publish_assignment(
            robot_id, task_id, pickup, dropoff, priority, required_payload
        )
        self.get_logger().info(
            f"Task {task_id}: assigned to {robot_id} "
            f'(cap={self._robots[robot_id]["payload_capacity"]}, '
            f"required={required_payload})"
        )

    def _publish_reservations(self):
        res_list = []
        for robot_id in sorted(self._reservations):
            r = self._reservations[robot_id]
            res_list.append(
                {
                    "robot_id": robot_id,
                    "task_id": r["task_id"],
                    "activated": r["activated"],
                    "segment_index": r["segment_index"],
                    "segments_reserved": sorted(r["reserved_segment_indices"]),
                    "total_segments": len(r["segments"]),
                    "head_on": r["head_on"],
                    "cells": [[c[0], c[1]] for c in sorted(r["cells"])],
                }
            )
        data = {
            "reservation_count": len(res_list),
            "pending_dispatches": [
                {"robot_id": rid, "task_id": t["task_id"]}
                for rid, queue in sorted(self._robot_queues.items())
                for t in queue
            ],
            "retry_tasks": [t["task_id"] for t in self._retry_tasks],
            "waiting_tasks": [t["task_id"] for t in self._waiting_tasks],
            "robot_queues": {
                rid: [t["task_id"] for t in queue]
                for rid, queue in sorted(self._robot_queues.items())
            },
            "reservations": res_list,
        }
        msg = String()
        msg.data = json.dumps(data)
        self._reservation_pub.publish(msg)

    def _publish_recovery_event(self, data):
        msg = String()
        msg.data = json.dumps(data)
        self._recovery_pub.publish(msg)

    def _publish_cancel(self, robot_id, task_id):
        # Targeted cancel: only the addressed robot's task manager cancels it.
        msg = String()
        msg.data = f"{robot_id},{task_id}"
        self._cancel_pub.publish(msg)

    def _robot_distance(self, entry, pickup):
        if entry.get("x") is None or entry.get("y") is None:
            return None
        return math.hypot(entry["x"] - pickup[0], entry["y"] - pickup[1])

    def _publish_dispatch_decision(self, task, selected, scored):
        data = {
            "task_id": task["task_id"],
            "required_payload": task["required_payload"],
            "selected_robot": selected["robot_id"] if selected else None,
            "score": round(scored[0]["score"], 6) if scored else None,
            "candidates": [
                {
                    "robot_id": s["robot"]["robot_id"],
                    "score": round(s["score"], 6),
                    "busy": self._busy(s["robot"]),
                    "queue_len": len(self._robot_queues.get(s["robot"]["robot_id"], [])),
                    "battery": s["robot"].get("battery", 100.0),
                }
                for s in scored
            ],
        }
        msg = String()
        msg.data = json.dumps(data)
        self._decision_pub.publish(msg)

    def _publish_assignment(
        self, robot_id, task_id, pickup, dropoff, priority, required_payload
    ):
        msg = String()
        msg.data = (
            f"{robot_id},{task_id},{pickup[0]},{pickup[1]},"
            f"{dropoff[0]},{dropoff[1]},{priority},{required_payload}"
        )
        self._assignment_pub.publish(msg)

    def _publish_fleet(self):
        robots = sorted(self._robots.values(), key=lambda r: r["robot_id"])
        data = {
            "robot_count": len(robots),
            "robots": robots,
        }
        msg = String()
        msg.data = json.dumps(data)
        self._fleet_pub.publish(msg)
        self._publish_fleet_monitor()  # derived summary, kept in sync

    def _publish_fleet_monitor(self):
        """Publish the complete fleet monitoring summary."""
        now = time.time()
        total = len(self._robots)
        online = sum(1 for e in self._robots.values() if e["status"] == "ONLINE")
        offline = total - online
        idle = sum(
            1
            for e in self._robots.values()
            if e["status"] == "ONLINE" and not e["current_task"]
        )
        busy = online - idle

        active_tasks = len(self._reservations)
        queued_tasks = (
            sum(len(q) for q in self._robot_queues.values())
            + len(self._waiting_tasks)
            + len(self._retry_tasks)
        )

        hb_robots = []
        for robot_id in sorted(self._robots):
            e = self._robots[robot_id]
            age = now - e["last_seen"] if e.get("last_seen") else None
            hb_robots.append(
                {
                    "robot_id": robot_id,
                    "status": e["status"],
                    "heartbeat_age": round(age, 3) if age is not None else None,
                }
            )
        online_ages = [
            now - e["last_seen"]
            for e in self._robots.values()
            if e["status"] == "ONLINE" and e.get("last_seen")
        ]

        data = {
            "fleet": {
                "total_robots": total,
                "online_robots": online,
                "offline_robots": offline,
                "idle_robots": idle,
                "busy_robots": busy,
            },
            "tasks": {
                "queued": queued_tasks,
                "active": active_tasks,
                "completed": self._tasks_completed,
                "submitted": self._tasks_submitted,
            },
            "reservations": {
                "active": active_tasks,
                "reserved_cells": len(self._cell_owners),
                "pending_dispatches": sum(len(q) for q in self._robot_queues.values()),
                "waiting_tasks": len(self._waiting_tasks),
                "retry_tasks": len(self._retry_tasks),
                "released_total": self._reservations_released,
            },
            "heartbeat": {
                "online_robots": online,
                "offline_robots": offline,
                "newest_age": round(min(online_ages), 3) if online_ages else None,
                "oldest_age": round(max(online_ages), 3) if online_ages else None,
                "robots": hb_robots,
            },
        }
        msg = String()
        msg.data = json.dumps(data)
        self._monitor_pub.publish(msg)


def main():
    rclpy.init()
    node = FleetManagerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
