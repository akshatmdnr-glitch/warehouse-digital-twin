"""Warehouse Task Manager with priority queue and finite state machine execution.

Subscribes: /task_assignment (std_msgs/String, CSV) — tasks assigned by the
            Fleet Manager for this robot; ignored if robot_id doesn't match.
            /cancel_task, /amcl_pose
Publishes:  /goal_pose, /task_status, /task_state, /queue_info

Assignment format: robot_id,task_id,pickup_x,pickup_y,dropoff_x,dropoff_y
                   [,priority[,required_payload]]
Priority: 0=Low, 1=Normal, 2=High (default: configurable, 1=Normal)

States: WAITING → GO_TO_PICKUP → PICKING → GO_TO_DROPOFF → DROPPING → COMPLETED
"""

import json
import math
import time

import rclpy
from geometry_msgs.msg import Point, PoseStamped, PoseWithCovarianceStamped, Quaternion
from rclpy.node import Node
from std_msgs.msg import String


class TaskManagerNode(Node):
    def __init__(self):
        super().__init__("task_manager")
        self.declare_parameter("goal_tolerance", 0.3)
        self.declare_parameter("pickup_delay", 2.0)
        self.declare_parameter("dropoff_delay", 1.5)
        self.declare_parameter("default_priority", 1)
        self.declare_parameter("robot_id", "tb3_1")

        self._tolerance = self.get_parameter("goal_tolerance").value
        self._pickup_delay = self.get_parameter("pickup_delay").value
        self._dropoff_delay = self.get_parameter("dropoff_delay").value
        self._default_priority = self.get_parameter("default_priority").value
        self._robot_id = self.get_parameter("robot_id").value

        self._tasks = []
        self._insertion_counter = 0
        self._active_task_idx = -1
        self._state = "IDLE"
        self._delay_start = 0.0

        self._current_pose = None

        self._task_sub = self.create_subscription(
            String, "/task_assignment", self._assignment_callback, 10
        )
        self._cancel_sub = self.create_subscription(
            String, "/cancel_task", self._cancel_task_callback, 10
        )
        self._pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._pose_callback, 10
        )
        self._goal_pub = self.create_publisher(PoseStamped, "/goal_pose", 10)
        self._status_pub = self.create_publisher(String, "/task_status", 10)
        self._state_pub = self.create_publisher(String, "/task_state", 10)
        self._queue_pub = self.create_publisher(String, "/queue_info", 10)

        self._timer = self.create_timer(0.5, self._control_loop)

        self.get_logger().info(
            f"Task Manager ready (robot_id={self._robot_id}, priority queue, "
            f"default_priority={self._default_priority})"
        )

    # ── Callbacks ──────────────────────────────────────────────

    def _assignment_callback(self, msg):
        try:
            parts = [p.strip() for p in msg.data.strip().split(",")]
            n = len(parts)
            if n < 6:
                self.get_logger().error(
                    f"Invalid assignment format (need >=6 fields): {msg.data}"
                )
                return
            robot_id = parts[0]
            if robot_id != self._robot_id:
                self.get_logger().info(
                    f"Assignment for {robot_id} ignored (I am {self._robot_id})"
                )
                return
            priority = int(parts[6]) if n >= 7 else self._default_priority
            priority = max(0, min(2, priority))  # clamp to 0-2
            required_payload = float(parts[7]) if n >= 8 else 0.0
            task = {
                "id": parts[1].strip(),
                "pickup": (float(parts[2]), float(parts[3])),
                "dropoff": (float(parts[4]), float(parts[5])),
                "priority": priority,
                "required_payload": required_payload,
                "status": "WAITING",
                "_insert_idx": self._insertion_counter,
            }
        except (ValueError, IndexError) as e:
            self.get_logger().error(f"Assignment parse error: {e}")
            return
        self._enqueue_task(task)

    def _enqueue_task(self, task):
        self._insertion_counter += 1
        self._tasks.append(task)
        prio_label = {0: "LOW", 1: "NORMAL", 2: "HIGH"}.get(
            task["priority"], str(task["priority"])
        )
        self.get_logger().info(
            f'Task {task["id"]} [{prio_label}] added: '
            f'pickup=({task["pickup"][0]:.1f},{task["pickup"][1]:.1f}) '
            f'→ dropoff=({task["dropoff"][0]:.1f},{task["dropoff"][1]:.1f})'
        )
        if self._state == "IDLE" and self._active_task_idx < 0:
            self._activate_next()

    def _cancel_task_callback(self, msg):
        parts = msg.data.strip().split(",")
        if len(parts) >= 2:
            # Targeted cancel: robot_id,task_id — only cancel if addressed to us.
            if parts[0].strip() != self._robot_id:
                return
            task_id = parts[1].strip()
        else:
            task_id = parts[0].strip()
        if not task_id:
            self.get_logger().error("Empty task id in cancel request")
            return
        for i, task in enumerate(self._tasks):
            if task["id"] != task_id:
                continue
            if task["status"] == "WAITING":
                self._tasks.pop(i)
                if self._active_task_idx > i:
                    self._active_task_idx -= 1
                self.get_logger().info(f"Task {task_id}: CANCELLED from queue")
                self._publish_status()
                self._publish_queue_info()
                return
            if task["status"] == "ACTIVE":
                self._tasks.pop(i)
                self._active_task_idx = -1
                self._state = "IDLE"
                self.get_logger().info(f"Task {task_id}: CANCELLED (was active)")
                self._activate_next()
                self._publish_status()
                self._publish_queue_info()
                return
            if task["status"] == "COMPLETED":
                self.get_logger().warn(
                    f"Task {task_id}: already COMPLETED, cannot cancel"
                )
                return
        self.get_logger().warn(f"Task {task_id}: not found in queue")

    def _pose_callback(self, msg):
        self._current_pose = msg.pose.pose

    # ── Control loop (timer-driven FSM — unchanged) ────────────

    def _control_loop(self):
        self._publish_status()
        self._publish_state()
        self._publish_queue_info()

        if self._state == "GO_TO_PICKUP":
            self._check_arrival(
                self._tasks[self._active_task_idx]["pickup"],
                "PICKING",
                "pickup",
            )
        elif self._state == "PICKING":
            if time.monotonic() - self._delay_start >= self._pickup_delay:
                self._transition("GO_TO_DROPOFF")
                task = self._tasks[self._active_task_idx]
                self._publish_goal(task["dropoff"])
                self.get_logger().info(f'Task {task["id"]}: heading to dropoff')
        elif self._state == "GO_TO_DROPOFF":
            self._check_arrival(
                self._tasks[self._active_task_idx]["dropoff"],
                "DROPPING",
                "dropoff",
            )
        elif self._state == "DROPPING":
            if time.monotonic() - self._delay_start >= self._dropoff_delay:
                task = self._tasks[self._active_task_idx]
                task["status"] = "COMPLETED"
                self._active_task_idx = -1
                self.get_logger().info(f'Task {task["id"]}: COMPLETED')
                self._activate_next()

    # ── Priority queue activation ──────────────────────────────

    def _transition(self, new_state):
        self._state = new_state
        self._delay_start = time.monotonic()

    def _activate_next(self):
        # Collect WAITING tasks, sort by (-priority, insertion_idx)
        waiting = [
            (t["_insert_idx"], i, t)
            for i, t in enumerate(self._tasks)
            if t["status"] == "WAITING"
        ]
        if not waiting:
            self._state = "IDLE"
            self.get_logger().info("All tasks completed — IDLE")
            return
        # Sort: higher priority (larger number) first, then FIFO
        waiting.sort(key=lambda x: (-x[2]["priority"], x[0]))
        _, original_idx, task = waiting[0]
        self._active_task_idx = original_idx
        task["status"] = "ACTIVE"
        self._transition("GO_TO_PICKUP")
        self._publish_goal(task["pickup"])
        prio_label = {0: "LOW", 1: "NORMAL", 2: "HIGH"}.get(task["priority"], "?")
        self.get_logger().info(
            f'Task {task["id"]} [{prio_label}]: navigating to pickup '
            f'({task["pickup"][0]:.1f}, {task["pickup"][1]:.1f})'
        )

    def _check_arrival(self, target_pos, next_state, label):
        px, py = self._get_robot_position()
        if px is None:
            return
        gx, gy = target_pos
        if math.hypot(gx - px, gy - py) < self._tolerance:
            self._transition(next_state)
            task = self._tasks[self._active_task_idx]
            self.get_logger().info(
                f'Task {task["id"]}: arrived at {label}, {next_state}'
            )

    def _get_robot_position(self):
        if self._current_pose is None:
            return None, None
        p = self._current_pose.position
        return p.x, p.y

    # ── Publishers ─────────────────────────────────────────────

    def _publish_goal(self, pos):
        x, y = pos
        goal = PoseStamped()
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.header.frame_id = "map"
        goal.pose.position = Point(x=x, y=y, z=0.0)
        goal.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
        self._goal_pub.publish(goal)

    def _publish_status(self):
        status_list = []
        for t in self._tasks:
            status_list.append(
                {
                    "id": t["id"],
                    "status": t["status"],
                    "priority": t["priority"],
                    "pickup": list(t["pickup"]),
                    "dropoff": list(t["dropoff"]),
                }
            )
        msg = String()
        msg.data = json.dumps(status_list)
        self._status_pub.publish(msg)

    def _publish_state(self):
        active = (
            self._tasks[self._active_task_idx]["id"]
            if self._active_task_idx >= 0 and self._active_task_idx < len(self._tasks)
            else None
        )
        data = {"state": self._state, "active_task": active}
        msg = String()
        msg.data = json.dumps(data)
        self._state_pub.publish(msg)

    def _publish_queue_info(self):
        waiting = sorted(
            [
                (t["_insert_idx"], t)
                for i, t in enumerate(self._tasks)
                if t["status"] == "WAITING"
            ],
            key=lambda x: (-x[1]["priority"], x[0]),
        )
        pending = [{"id": t["id"], "priority": t["priority"]} for _, t in waiting]
        active = (
            {
                "id": self._tasks[self._active_task_idx]["id"],
                "priority": self._tasks[self._active_task_idx]["priority"],
            }
            if self._active_task_idx >= 0 and self._active_task_idx < len(self._tasks)
            else None
        )
        data = {
            "queue_length": len(self._tasks),
            "active_task": active,
            "execution_order": [item["id"] for item in pending],
            "pending_tasks": pending,
        }
        msg = String()
        msg.data = json.dumps(data)
        self._queue_pub.publish(msg)


def main():
    rclpy.init()
    node = TaskManagerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
