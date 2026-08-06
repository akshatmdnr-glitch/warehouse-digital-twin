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
                           [,workload[,priority[,battery[,charging]]]]]]]
  status:           ONLINE or OFFLINE (case-insensitive)
  current_task:     active task id, or empty
  payload_capacity: kg (float, default 0.0)
  max_speed:        m/s (float, default 0.0)
  robot_type:       str (default 'unknown')
  workload:         queued-task count (int, default 0)
  priority:         dispatch priority, higher = preferred (float, default 0.0)
  battery:          battery percentage 0-100 (float, default 100.0)
  charging:         1 if charging else 0 (int, default 0)

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

Assignment policy: only ONLINE, IDLE robots whose payload_capacity >=
required_payload are eligible. Each eligible robot is scored with a weighted
sum of normalized factors — distance to pickup, current workload, robot
priority, and capability match (surplus capacity) — and the lowest score
wins (ties broken by robot_id). Weights are configurable:
score_w_distance, score_w_workload, score_w_priority, score_w_capability.
If none qualify, an assignment is published with robot_id='NONE'.

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
        self.declare_parameter("score_w_workload", 1.0)
        self.declare_parameter("score_w_priority", 1.0)
        self.declare_parameter("score_w_capability", 1.0)
        self.declare_parameter("low_battery_threshold", 30.0)
        self.declare_parameter("critical_battery_threshold", 15.0)
        self.declare_parameter("segment_size", 2)
        self.declare_parameter("traffic_lookahead", 1)

        self._publish_rate = self.get_parameter("publish_rate").value
        self._heartbeat_timeout = self.get_parameter("heartbeat_timeout").value
        self._cell_size = self.get_parameter("cell_size").value
        self._reservation_buffer = int(self.get_parameter("reservation_buffer").value)
        self._w_distance = self.get_parameter("score_w_distance").value
        self._w_workload = self.get_parameter("score_w_workload").value
        self._w_priority = self.get_parameter("score_w_priority").value
        self._w_capability = self.get_parameter("score_w_capability").value
        self._low_battery = self.get_parameter("low_battery_threshold").value
        self._critical_battery = self.get_parameter("critical_battery_threshold").value
        self._segment_size = max(1, int(self.get_parameter("segment_size").value))
        self._lookahead = max(0, int(self.get_parameter("traffic_lookahead").value))
        self._robots = {}  # robot_id -> registry entry

        # Reservation state (multi-robot coordination)
        self._reservations = (
            {}
        )  # robot_id -> {task_id, task, cells:set, activated:bool}
        self._cell_owners = {}  # (cx,cy) -> robot_id
        self._pending_dispatches = []  # queued tasks waiting for a free route
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
                "namespace": parts[10].strip() if len(parts) >= 11 else "",
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
            if len(parts) >= 11:
                entry["namespace"] = parts[10].strip()
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

        # 2. Pending dispatches committed to this robot — return to the queue.
        kept = []
        for entry in self._pending_dispatches:
            if entry["robot_id"] == robot_id:
                recovered.append(
                    {
                        "task_id": entry["task_id"],
                        "pickup": entry["pickup"],
                        "dropoff": entry["dropoff"],
                        "priority": entry["priority"],
                        "required_payload": entry["required_payload"],
                    }
                )
            else:
                kept.append(entry)
        self._pending_dispatches = kept

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
        result = self._attempt_dispatch(task)
        if result == "no_robot":
            self._retry_tasks.append(task)
            self._publish_recovery_event(
                {
                    "event": "task_recovered",
                    "task_id": task_id,
                    "reason": "waiting_for_robot",
                }
            )
        elif result == "dispatched":
            self._publish_recovery_event(
                {
                    "event": "task_recovered",
                    "task_id": task_id,
                    "reason": "redispatched",
                }
            )
        # 'queued' -> now in _pending_dispatches for the new robot

    def _process_retry_tasks(self):
        """Dispatch recovered tasks as soon as an eligible robot appears."""
        remaining = []
        for task in self._retry_tasks:
            result = self._attempt_dispatch(task)
            if result == "no_robot":
                remaining.append(task)
            elif result == "dispatched":
                self._publish_recovery_event(
                    {
                        "event": "task_recovered",
                        "task_id": task["task_id"],
                        "reason": "redispatched",
                    }
                )
            # 'queued' -> moved into _pending_dispatches, drop from retry
        self._retry_tasks = remaining

    def _control_loop(self):
        # Tunable parameters are re-read every tick so the Control Center's
        # runtime Settings editor takes effect immediately (no node restart).
        self._heartbeat_timeout = self.get_parameter("heartbeat_timeout").value
        self._cell_size = self.get_parameter("cell_size").value
        self._reservation_buffer = int(self.get_parameter("reservation_buffer").value)
        self._w_distance = self.get_parameter("score_w_distance").value
        self._w_workload = self.get_parameter("score_w_workload").value
        self._w_priority = self.get_parameter("score_w_priority").value
        self._w_capability = self.get_parameter("score_w_capability").value
        self._low_battery = self.get_parameter("low_battery_threshold").value
        self._critical_battery = self.get_parameter("critical_battery_threshold").value
        self._segment_size = max(1, int(self.get_parameter("segment_size").value))
        self._lookahead = max(0, int(self.get_parameter("traffic_lookahead").value))
        self._check_heartbeats()
        self._check_battery()
        self._check_traffic()
        self._process_retry_tasks()
        self._process_pending_dispatches()
        self._publish_reservations()
        self._publish_fleet()

    def _check_battery(self):
        """Release work and mark charging for robots that need to charge.

        A robot needs to charge when it reports `charging` (its beacon reached
        its critical threshold) or its battery is at/below the fleet critical
        threshold. Either signal safely releases its active task so another
        eligible robot can pick it up.
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
            kept = []
            for p in self._pending_dispatches:
                if p["robot_id"] == robot_id:
                    released.append(
                        {
                            "task_id": p["task_id"],
                            "pickup": p["pickup"],
                            "dropoff": p["dropoff"],
                            "priority": p["priority"],
                            "required_payload": p["required_payload"],
                        }
                    )
                else:
                    kept.append(p)
            self._pending_dispatches = kept
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
            entry["charging"] = True
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

        result = self._attempt_dispatch(task)
        if result == "no_robot":
            self.get_logger().warn(
                f"Task {task_id}: no eligible robot "
                f"(required_payload={required_payload})"
            )
            self._publish_assignment(
                "NONE", task_id, pickup, dropoff, priority, required_payload
            )

        self._publish_fleet()  # busy marking reflected immediately
        self._publish_reservations()

    # ── Reservation coordination ───────────────────────────────

    def _attempt_dispatch(self, task, robot_id=None):
        """Dispatch task to robot_id (or the best eligible robot)."""
        task_id = task["task_id"]
        if robot_id is None:
            robot, scored = self._select_robot(task["required_payload"], task["pickup"])
            self._publish_dispatch_decision(task, robot, scored)
            if robot is None:
                return "no_robot"
            robot_id = robot["robot_id"]
        else:
            robot = self._robots.get(robot_id)
            if robot is None or robot["status"] != "ONLINE":
                return "no_robot"
        robot["current_task"] = task_id  # optimistic busy — prevents re-assignment

        route_ordered = self._route_cells_ordered(task["pickup"], task["dropoff"])
        segments = self._partition_segments(route_ordered)
        route_dir = (
            task["dropoff"][0] - task["pickup"][0],
            task["dropoff"][1] - task["pickup"][1],
        )
        route_set = set(route_ordered)

        # Head-on conflicts are serialized at dispatch so opposing robots can
        # never meet mid-corridor (prevents head-on deadlocks).
        if self._has_head_on_conflict(route_set, route_dir):
            self._queue_pending(robot_id, task, route_ordered, segments, route_dir)
            self.get_logger().warn(
                f"Task {task_id}: head-on corridor conflict, queued for {robot_id}"
            )
            return "queued"

        if not self._has_conflict(robot_id, route_set) and self._reserve(
            robot_id, task, route_ordered, segments, route_dir
        ):
            self._dispatch(
                robot_id,
                task_id,
                task["pickup"],
                task["dropoff"],
                task["priority"],
                task["required_payload"],
            )
            return "dispatched"
        self._queue_pending(robot_id, task, route_ordered, segments, route_dir)
        self.get_logger().info(
            f"Task {task_id}: route conflict, queued for {robot_id} "
            f"(waiting for reservation)"
        )
        return "queued"

    def _queue_pending(self, robot_id, task, route_ordered, segments, route_dir):
        self._pending_dispatches.append(
            {
                "robot_id": robot_id,
                "task_id": task["task_id"],
                "pickup": task["pickup"],
                "dropoff": task["dropoff"],
                "priority": task["priority"],
                "required_payload": task["required_payload"],
                "route_ordered": route_ordered,
                "segments": segments,
                "route_dir": route_dir,
            }
        )

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
        for entry in self._pending_dispatches:
            if entry["task_id"] == task_id:
                return True
        for res in self._reservations.values():
            if res["task_id"] == task_id:
                return True
        for task in self._retry_tasks:
            if task["task_id"] == task_id:
                return True
        return False

    def _process_pending_dispatches(self):
        """Dispatch queued tasks in FIFO order as soon as their route is free."""
        remaining = []
        for entry in self._pending_dispatches:
            robot_id = entry["robot_id"]
            rob = self._robots.get(robot_id)
            # The robot is committed to this task; it must still be ONLINE.
            if rob is None or rob["status"] != "ONLINE":
                # Safety net: the committed robot vanished — return the task
                # to the queue so another robot can pick it up.
                self._retry_tasks.append(
                    {
                        "task_id": entry["task_id"],
                        "pickup": entry["pickup"],
                        "dropoff": entry["dropoff"],
                        "priority": entry["priority"],
                        "required_payload": entry["required_payload"],
                    }
                )
                self.get_logger().warn(
                    f'Task {entry["task_id"]}: robot {robot_id} unavailable, '
                    f"re-queued for another robot"
                )
                continue
            route_set = set(entry["route_ordered"])
            # Head-on routes stay queued until the opposing robot is done.
            if self._has_head_on_conflict(route_set, entry["route_dir"]):
                remaining.append(entry)
                continue
            if self._has_conflict(robot_id, route_set):
                remaining.append(entry)
                continue
            task = {
                "task_id": entry["task_id"],
                "pickup": entry["pickup"],
                "dropoff": entry["dropoff"],
                "priority": entry["priority"],
                "required_payload": entry["required_payload"],
            }
            if not self._reserve(
                robot_id,
                task,
                entry["route_ordered"],
                entry["segments"],
                entry["route_dir"],
            ):
                remaining.append(entry)
                continue
            self._dispatch(
                robot_id,
                entry["task_id"],
                entry["pickup"],
                entry["dropoff"],
                entry["priority"],
                entry["required_payload"],
            )
            self.get_logger().info(
                f'Task {entry["task_id"]}: reservation cleared, '
                f"dispatched to {robot_id}"
            )
            self._publish_reservations()
        self._pending_dispatches = remaining

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
                {"robot_id": e["robot_id"], "task_id": e["task_id"]}
                for e in self._pending_dispatches
            ],
            "retry_tasks": [t["task_id"] for t in self._retry_tasks],
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

    def _score_candidates(self, candidates, required_payload, pickup):
        """Score every eligible robot; lower score is better."""
        dists = [self._robot_distance(e, pickup) for e in candidates]
        known = [d for d in dists if d is not None]
        if known:
            fill = max(known)  # unknown position is treated conservatively
            dists = [d if d is not None else fill for d in dists]
        else:
            dists = [0.0] * len(candidates)
        dmax = max(dists) if dists else 0.0

        loads = [float(e.get("workload", 0.0)) for e in candidates]
        lmax = max(loads) if loads else 0.0

        pris = [float(e.get("priority", 0.0)) for e in candidates]
        prange = (max(pris) - min(pris)) if pris else 0.0

        surpluses = [
            float(e["payload_capacity"]) - required_payload for e in candidates
        ]
        srange = (max(surpluses) - min(surpluses)) if surpluses else 0.0

        scored = []
        for i, e in enumerate(candidates):
            dist_norm = (dists[i] / dmax) if dmax > 0 else 0.0
            load_norm = (loads[i] / lmax) if lmax > 0 else 0.0
            prio_norm = ((max(pris) - pris[i]) / prange) if prange > 0 else 0.0
            cap_norm = ((surpluses[i] - min(surpluses)) / srange) if srange > 0 else 0.0
            score = (
                self._w_distance * dist_norm
                + self._w_workload * load_norm
                + self._w_priority * prio_norm
                + self._w_capability * cap_norm
            )
            scored.append(
                {
                    "robot": e,
                    "score": score,
                    "distance": round(dists[i], 3),
                    "workload": loads[i],
                    "priority": pris[i],
                    "capability_match": round(cap_norm, 3),
                }
            )
        return scored

    def _select_robot(self, required_payload, pickup):
        """Choose the best eligible robot using the weighted scoring policy."""
        candidates = [
            e
            for e in self._robots.values()
            if e["status"] == "ONLINE"  # never assign OFFLINE robots
            and not e["current_task"]  # must be IDLE
            and not e.get("charging", False)  # must not be charging
            and e.get("battery", 100.0) >= self._low_battery  # must have charge
            and e["payload_capacity"] >= required_payload  # must carry it
        ]
        if not candidates:
            return None, []
        scored = self._score_candidates(candidates, required_payload, pickup)
        scored.sort(key=lambda s: (s["score"], s["robot"]["robot_id"]))  # deterministic
        return scored[0]["robot"], scored

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
                    "distance": s["distance"],
                    "workload": s["workload"],
                    "priority": s["priority"],
                    "capability_match": s["capability_match"],
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
        queued_tasks = len(self._pending_dispatches) + len(self._retry_tasks)

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
                "pending_dispatches": len(self._pending_dispatches),
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
