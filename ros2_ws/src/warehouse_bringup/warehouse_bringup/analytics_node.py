"""Warehouse analytics — independent observer that computes fleet performance.

This node only observes existing global topics and never modifies robot or
fleet behavior:

  Subscribes: /fleet_status (String, JSON)
              /robot_pose   (String, robot_id,x,y)
              /reservation_status (String, JSON)
  Publishes:  /analytics    (String, JSON summary)

Metrics (per robot): distance traveled, completed tasks, active / idle /
charging time, battery usage. Fleet metrics: total completed tasks, active /
idle / offline robots, rolling averages of task duration, queue wait time and
reservation wait time.

Rolling statistics keep the last `rolling_window` samples; the summary is
published every `publish_period` seconds.
"""

import json
import math
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def _default_robot(now):
    return {
        "status": "?",
        "battery": 100.0,
        "charging": False,
        "current_task": "",
        "distance": 0.0,
        "completed": 0,
        "active_time": 0.0,
        "idle_time": 0.0,
        "charging_time": 0.0,
        "battery_usage": 0.0,
        "prev_battery": None,
        "last_seen": now,
    }


class AnalyticsNode(Node):
    def __init__(self):
        super().__init__("analytics")
        self.declare_parameter("publish_period", 2.0)
        self.declare_parameter("rolling_window", 20)

        self._publish_period = float(self.get_parameter("publish_period").value)
        self._window = max(1, int(self.get_parameter("rolling_window").value))

        self._robots = {}  # robot_id -> metric dict
        self._task_start = {}  # robot_id -> time task became active
        self._pos = {}  # robot_id -> (x, y)
        self._prev_task = {}  # robot_id -> last seen current_task
        self._prev_state = {}  # robot_id -> last observed state dict
        self._busy_streak = {}  # robot_id -> consecutive busy observations
        self._pending_since = {}  # task_id -> time first seen queued
        self._max_seg = {}  # robot_id -> max reserved segment index
        self._blocked_since = {}  # robot_id -> time it became reservation-blocked

        # Rolling samples
        self._task_durations = []
        self._queue_waits = []
        self._reservation_waits = []

        self._fleet_sub = self.create_subscription(
            String, "/fleet_status", self._fleet_callback, 10
        )
        self._pose_sub = self.create_subscription(
            String, "/robot_pose", self._pose_callback, 10
        )
        self._res_sub = self.create_subscription(
            String, "/reservation_status", self._reservation_callback, 10
        )
        self._analytics_pub = self.create_publisher(String, "/analytics", 10)

        self._timer = self.create_timer(self._publish_period, self._publish_analytics)

        self.get_logger().info(
            f"Analytics ready (publish_period={self._publish_period}s, "
            f"rolling_window={self._window})"
        )

    # ── Observers ──────────────────────────────────────────────

    def _fleet_callback(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        now = time.time()
        for r in data.get("robots", []):
            rid = r.get("robot_id")
            if not rid:
                continue
            st = self._robots.setdefault(rid, _default_robot(now))
            dt = max(0.0, now - st["last_seen"])
            st["last_seen"] = now

            # Time accrual: the interval since the previous observation is
            # attributed to the state the robot was in during that interval.
            prev = self._prev_state.get(rid)
            if prev is not None:
                if prev.get("charging"):
                    st["charging_time"] += dt
                elif prev.get("current_task"):
                    st["active_time"] += dt
                elif prev.get("status") == "ONLINE":
                    st["idle_time"] += dt
            self._prev_state[rid] = {
                "status": r.get("status"),
                "current_task": r.get("current_task"),
                "charging": bool(r.get("charging", False)),
            }

            # Battery usage (cumulative discharge).
            battery = r.get("battery")
            if battery is not None:
                if st["prev_battery"] is None:
                    st["prev_battery"] = battery
                st["battery_usage"] += max(0.0, st["prev_battery"] - battery)
                st["prev_battery"] = battery

            # Task completion: a stable busy phase ending in idle while ONLINE
            # and not charging. A busy phase is "stable" once observed across
            # two consecutive fleet_status messages, which filters out the
            # transient optimistic-busy flicker during dispatch.
            prev = self._prev_task.get(rid, "")
            cur = r.get("current_task") or ""
            if cur:
                self._busy_streak[rid] = self._busy_streak.get(rid, 0) + 1
                if not prev:
                    self._task_start[rid] = now
            else:
                if (
                    self._busy_streak.get(rid, 0) >= 2
                    and r.get("status") == "ONLINE"
                    and not r.get("charging")
                ):
                    st["completed"] += 1
                    self._push_sample(
                        self._task_durations, now - self._task_start.get(rid, now)
                    )
                self._busy_streak[rid] = 0
            self._prev_task[rid] = cur

            st["status"] = r.get("status", st["status"])
            st["battery"] = battery if battery is not None else st["battery"]
            st["charging"] = bool(r.get("charging", st["charging"]))
            st["current_task"] = cur

    def _pose_callback(self, msg):
        try:
            parts = msg.data.strip().split(",")
            rid = parts[0]
            x = float(parts[1])
            y = float(parts[2])
        except (ValueError, IndexError):
            return
        st = self._robots.setdefault(rid, _default_robot(time.time()))
        prev = self._pos.get(rid)
        if prev is not None:
            st["distance"] += math.hypot(x - prev[0], y - prev[1])
        self._pos[rid] = (x, y)

    def _reservation_callback(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, TypeError):
            return
        now = time.time()

        # Queue wait: time a task spends in the pending-dispatches queue.
        pending = {
            p.get("task_id")
            for p in data.get("pending_dispatches", [])
            if p.get("task_id")
        }
        for tid in pending:
            if tid not in self._pending_since:
                self._pending_since[tid] = now
        for tid in list(self._pending_since):
            if tid not in pending:
                self._push_sample(self._queue_waits, now - self._pending_since.pop(tid))

        # Reservation wait: time a robot's segment window is blocked.
        active = set()
        for r in data.get("reservations", []):
            rid = r.get("robot_id")
            if not rid:
                continue
            active.add(rid)
            segs = r.get("segments_reserved") or []
            total = r.get("total_segments", 0)
            max_seg = max(segs) if segs else -1
            prev_max = self._max_seg.get(rid, -1)
            blocked = max_seg >= 0 and max_seg < total - 1 and max_seg <= prev_max
            if blocked:
                self._blocked_since.setdefault(rid, now)
            elif rid in self._blocked_since:
                self._push_sample(
                    self._reservation_waits, now - self._blocked_since.pop(rid)
                )
            self._max_seg[rid] = max_seg
        for rid in list(self._blocked_since):
            if rid not in active:
                self._push_sample(
                    self._reservation_waits, now - self._blocked_since.pop(rid)
                )

    # ── Utilities ──────────────────────────────────────────────

    def _push_sample(self, samples, value):
        if value >= 0:
            samples.append(value)
            if len(samples) > self._window:
                del samples[0]

    @staticmethod
    def _mean(samples):
        return (sum(samples) / len(samples)) if samples else None

    # ── Publisher ──────────────────────────────────────────────

    def _publish_analytics(self):
        # Re-read the rolling window so the Control Center's runtime Settings
        # editor can change it without restarting this node.
        self._window = max(1, int(self.get_parameter("rolling_window").value))
        robots = []
        active = idle = offline = 0
        for rid in sorted(self._robots):
            st = self._robots[rid]
            if st["status"] == "OFFLINE":
                offline += 1
            elif st["charging"]:
                pass  # charging robots are neither active nor idle
            elif st["current_task"]:
                active += 1
            else:
                idle += 1
            robots.append(
                {
                    "robot_id": rid,
                    "status": st["status"],
                    "battery": round(st["battery"], 1),
                    "charging": st["charging"],
                    "distance_traveled": round(st["distance"], 2),
                    "completed_tasks": st["completed"],
                    "active_time": round(st["active_time"], 1),
                    "idle_time": round(st["idle_time"], 1),
                    "charging_time": round(st["charging_time"], 1),
                    "battery_usage": round(st["battery_usage"], 1),
                }
            )
        fleet = {
            "total_robots": len(self._robots),
            "active_robots": active,
            "idle_robots": idle,
            "offline_robots": offline,
            "total_completed_tasks": sum(
                st["completed"] for st in self._robots.values()
            ),
            "avg_task_duration": (
                round(self._mean(self._task_durations), 3)
                if self._task_durations
                else None
            ),
            "avg_queue_wait": (
                round(self._mean(self._queue_waits), 3) if self._queue_waits else None
            ),
            "avg_reservation_wait": (
                round(self._mean(self._reservation_waits), 3)
                if self._reservation_waits
                else None
            ),
            "task_duration_samples": len(self._task_durations),
            "queue_wait_samples": len(self._queue_waits),
            "reservation_wait_samples": len(self._reservation_waits),
        }
        data = {
            "timestamp": round(time.time(), 3),
            "publish_period": self._publish_period,
            "fleet": fleet,
            "robots": robots,
        }
        msg = String()
        msg.data = json.dumps(data)
        self._analytics_pub.publish(msg)


def main():
    rclpy.init()
    node = AnalyticsNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
