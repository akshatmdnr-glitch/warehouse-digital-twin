"""Robot status beacon — publishes registration, heartbeat, pose, battery and
the robot's authoritative execution state.

Subscribes: /task_state (std_msgs/String, JSON) — task manager FSM state
            /queue_info (std_msgs/String, JSON) — queued workload
            /amcl_pose (PoseWithCovarianceStamped) — position/orientation
            /odom (nav_msgs/Odometry) — actual velocity
            /plan (nav_msgs/Path) — global path existence
            /battery_command (std_msgs/String) — operator simulation control
            /control (std_msgs/String) — operator control (simulated restart)
Publishes:  /robot_registration (std_msgs/String)
            /robot_heartbeat (std_msgs/String, robot_id at publish_rate)
            /robot_pose (std_msgs/String, robot_id,x,y,yaw)
            /battery_status (std_msgs/String, JSON)
            /goal_pose (PoseStamped) — charging-station goal on critical battery

Registration format: robot_id,status,current_task
                     [,payload_capacity[,max_speed[,robot_type
                     [,workload[,priority[,battery[,charging[,namespace
                     [,exec_state[,moving]]]]]]]]]]

EXECUTION STATE MACHINE — the single source of truth the whole UI renders from.
The state is derived ONLY from the execution engine's signals (task FSM state,
global path, measured velocity and measured pose) — never invented:
    IDLE                no task, no goal, at rest
    ASSIGNED            task assigned to this robot but not yet being executed
    PLANNING            active goal but no valid path yet (or waiting to move)
    MOVING_TO_PICKUP    valid path + velocity>0 + pose changing → en route
    PICKING             at pickup, attaching package
    CARRYING            picked up, navigating toward dropoff with package
    MOVING_TO_DROPOFF   dropoff goal set, path not yet driving
    DROPPING            at dropoff, detaching package
    RETURNING           task done, returning to home / charging station
    CHARGING            ONLY when the pose is physically inside the pad bounds

DRIVING (reported as `moving`) is true only when ALL of:
    * a valid path exists, AND
    * measured velocity > motion threshold, AND
    * the measured pose is changing between beacon ticks.
`charging` is reported only when the measured pose is inside the charging pad
bounds; the moment the robot leaves the pad it falls back to a movement state.
"""

import json
import math
import time

import rclpy
from geometry_msgs.msg import Point, PoseStamped, PoseWithCovarianceStamped, Quaternion
from nav_msgs.msg import Odometry, Path
from rclpy.node import Node
from std_msgs.msg import String

# Task-manager FSM states (published on /task_state) mapped onto the execution
# state machine reported to the fleet and frontend.
_FSM_EXEC = {
    "GO_TO_PICKUP": "MOVING_TO_PICKUP",
    "PICKING": "PICKING",
    "GO_TO_DROPOFF": "CARRYING",
    "DROPPING": "DROPPING",
}


class RobotStatusPublisher(Node):
    def __init__(self):
        super().__init__("robot_status_publisher")
        self.declare_parameter("robot_id", "tb3_1")
        self.declare_parameter("status", "ONLINE")
        self.declare_parameter("publish_rate", 1.0)
        self.declare_parameter("payload_capacity", 0.0)
        self.declare_parameter("max_speed", 0.0)
        self.declare_parameter("robot_type", "unknown")
        self.declare_parameter("priority", 0.0)
        # Battery parameters
        self.declare_parameter("initial_battery", 100.0)
        self.declare_parameter("discharge_rate", 1.0)  # % per second
        self.declare_parameter("charge_rate", 10.0)  # % per second
        self.declare_parameter("critical_battery_threshold", 15.0)
        self.declare_parameter("charge_complete_threshold", 100.0)
        self.declare_parameter("low_battery_threshold", 30.0)
        self.declare_parameter("charging_station_x", 0.0)
        self.declare_parameter("charging_station_y", 5.0)
        # Execution-state sensing
        self.declare_parameter("charging_pad_radius", 0.7)
        self.declare_parameter("motion_threshold", 0.02)  # m/s
        self.declare_parameter("pose_change_threshold", 0.01)  # m over window
        self.declare_parameter("pose_window", 0.25)  # seconds for pose-change check

        self._robot_id = self.get_parameter("robot_id").value
        self._status = self.get_parameter("status").value.upper()
        self._publish_rate = self.get_parameter("publish_rate").value
        self._payload_capacity = self.get_parameter("payload_capacity").value
        self._max_speed = self.get_parameter("max_speed").value
        self._robot_type = self.get_parameter("robot_type").value
        self._priority = self.get_parameter("priority").value
        self._current_task = ""
        self._workload = 0
        self._pose = None  # (x, y, yaw)
        self._pose_hist = []  # (monotonic_time, x, y) — short history for motion

        self._pad_radius = self.get_parameter("charging_pad_radius").value
        self._motion_threshold = self.get_parameter("motion_threshold").value
        self._pose_change = self.get_parameter("pose_change_threshold").value
        self._pose_window = self.get_parameter("pose_window").value

        self._battery = float(self.get_parameter("initial_battery").value)
        self._discharge_rate = self.get_parameter("discharge_rate").value
        self._charge_rate = self.get_parameter("charge_rate").value
        self._critical = self.get_parameter("critical_battery_threshold").value
        self._charge_complete = self.get_parameter("charge_complete_threshold").value
        self._low = self.get_parameter("low_battery_threshold").value
        self._station = (
            self.get_parameter("charging_station_x").value,
            self.get_parameter("charging_station_y").value,
        )
        self._want_charge = False  # robot *wants* to charge (battery/operator)
        self._charging = False  # physically charging (pose inside pad bounds)
        self._battery_state = "IDLE"
        self._last_tick = time.monotonic()
        self._battery_forced_drain = False
        self._restart_until = None

        # ── Execution-state signals ──
        self._fsm_state = "IDLE"  # task manager FSM state (from /task_state)
        self._has_plan = False    # a global path is currently published
        self._speed = 0.0         # measured linear velocity (from /odom)
        self._goal = None         # last seen /goal_pose (x, y)

        self._state_sub = self.create_subscription(
            String, "/task_state", self._state_callback, 10
        )
        self._queue_sub = self.create_subscription(
            String, "/queue_info", self._queue_callback, 10
        )
        self._amcl_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._pose_callback, 10
        )
        self._odom_sub = self.create_subscription(
            Odometry, "/odom", self._odom_callback, 10
        )
        self._plan_sub = self.create_subscription(
            Path, "/plan", self._plan_callback, 10
        )
        self._goal_sub = self.create_subscription(
            PoseStamped, "/goal_pose", self._goal_callback, 10
        )
        self._bat_cmd_sub = self.create_subscription(
            String, "battery_command", self._battery_command_callback, 10
        )
        self._ctrl_sub = self.create_subscription(
            String, "control", self._control_callback, 10
        )
        self._reg_pub = self.create_publisher(String, "/robot_registration", 10)
        self._hb_pub = self.create_publisher(String, "/robot_heartbeat", 10)
        self._pose_pub = self.create_publisher(String, "/robot_pose", 10)
        self._battery_pub = self.create_publisher(String, "/battery_status", 10)
        self._goal_pub = self.create_publisher(PoseStamped, "/goal_pose", 10)

        self._timer = self.create_timer(1.0 / self._publish_rate, self._control_loop)

        self.get_logger().info(
            f"Robot {self._robot_id} status beacon ready "
            f"({self._status}, {self._publish_rate} Hz, battery={self._battery:.0f}%)"
        )

    def _state_callback(self, msg):
        try:
            data = json.loads(msg.data)
            self._fsm_state = data.get("state", "IDLE")
            self._current_task = data.get("active_task", "") or ""
        except (ValueError, TypeError):
            pass  # keep last known state on malformed input

    def _queue_callback(self, msg):
        try:
            data = json.loads(msg.data)
            self._workload = int(data.get("queue_length", 0))
        except (ValueError, TypeError):
            pass  # keep last known workload on malformed input

    def _pose_callback(self, msg):
        p = msg.pose.pose
        q = p.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        )
        self._pose = (p.position.x, p.position.y, yaw)
        self._pose_hist.append((time.monotonic(), p.position.x, p.position.y))
        # Keep only a short sliding window of pose samples.
        cutoff = time.monotonic() - max(self._pose_window * 3, 1.0)
        while self._pose_hist and self._pose_hist[0][0] < cutoff:
            self._pose_hist.pop(0)

    def _odom_callback(self, msg):
        self._speed = msg.twist.twist.linear.x

    def _plan_callback(self, msg):
        # A path is "present" only if it has real waypoints (>=2 distinct cells).
        self._has_plan = len(msg.poses) >= 2

    def _goal_callback(self, msg):
        self._goal = (msg.pose.position.x, msg.pose.position.y)

    # ── Operator simulation controls ───────────────────────────

    def _battery_command_callback(self, msg):
        """Apply a battery simulation command: drain / recharge / set:<pct>."""
        cmd = msg.data.strip().lower()
        if cmd in ("drain", "discharge"):
            self._battery_forced_drain = True
            self.get_logger().info(f"{self._robot_id}: forced battery drain")
        elif cmd == "recharge":
            self._battery_forced_drain = False
            if not self._want_charge:
                self._want_charge = True
                self._publish_charging_goal()
            self.get_logger().info(f"{self._robot_id}: forced recharge")
        elif cmd.startswith("set:"):
            try:
                pct = max(0.0, min(100.0, float(cmd.split(":", 1)[1])))
                self._battery = pct
                self.get_logger().info(f"{self._robot_id}: battery set to {pct:.0f}%")
            except ValueError:
                self.get_logger().error(
                    f'{self._robot_id}: bad battery_command "{msg.data}"'
                )
        else:
            self.get_logger().warn(
                f'{self._robot_id}: unknown battery_command "{msg.data}"'
            )

    def _control_callback(self, msg):
        """Apply an operator control command (currently 'restart')."""
        cmd = msg.data.strip().lower()
        if cmd == "restart" and self._restart_until is None:
            self._restart_until = time.monotonic() + 2.0
            self._status = "OFFLINE"
            self.get_logger().warn(f"{self._robot_id}: restarting (simulated reboot)")
        elif cmd != "restart":
            self.get_logger().warn(
                f'{self._robot_id}: unknown control command "{msg.data}"'
            )

    # ── Battery simulation ─────────────────────────────────────

    def _update_battery(self, dt):
        # Physical charging requires being inside the pad — leaving the pad
        # immediately drops back to a movement state (charging flag cleared).
        self._charging = self._want_charge and self._at_charging_pad()

        if self._charging:
            self._battery = min(100.0, self._battery + self._charge_rate * dt)
            self._battery_state = "CHARGING"
            if self._battery >= self._charge_complete:
                self._want_charge = False
                self._battery_state = "IDLE"
                self._battery_forced_drain = False
                self.get_logger().info(
                    f"{self._robot_id}: charging complete ({self._battery:.0f}%) "
                    f"— rejoined fleet"
                )
        elif self._battery_forced_drain:
            # Operator forced-drain simulation (regardless of task).
            self._battery = max(0.0, self._battery - self._discharge_rate * dt)
            self._battery_state = "DISCHARGING"
        elif self._current_task and not self._want_charge:
            # Driving / executing a task drains the battery.
            self._battery = max(0.0, self._battery - self._discharge_rate * dt)
            self._battery_state = "DISCHARGING"
        else:
            self._battery_state = "IDLE"

        if not self._want_charge and self._battery <= self._critical:
            self._want_charge = True
            self._publish_charging_goal()
            self.get_logger().warn(
                f"{self._robot_id}: critical battery ({self._battery:.1f}%) "
                f"— navigating to charging station {self._station}"
            )

    def _at_charging_pad(self):
        if self._pose is None:
            return False
        d = math.hypot(self._pose[0] - self._station[0],
                       self._pose[1] - self._station[1])
        return d <= self._pad_radius

    def _publish_charging_goal(self):
        goal = PoseStamped()
        goal.header.stamp = self.get_clock().now().to_msg()
        goal.header.frame_id = "map"
        goal.pose.position = Point(x=self._station[0], y=self._station[1], z=0.0)
        goal.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
        self._goal_pub.publish(goal)
        self.get_logger().info(
            f"{self._robot_id}: goal → charging station "
            f"({self._station[0]}, {self._station[1]})"
        )

    def _publish_battery(self):
        msg = String()
        msg.data = json.dumps(
            {
                "robot_id": self._robot_id,
                "battery": round(self._battery, 1),
                "charging": self._charging,
                "state": self._battery_state,
            }
        )
        self._battery_pub.publish(msg)

    # ── Execution state machine ────────────────────────────────

    def _pose_changing(self):
        """True when the robot moved a detectable distance over a short time
        window. Uses a sliding window of pose samples so even slow motion
        (0.05 m/s) is caught without flapping on jitter."""
        if self._pose is None or len(self._pose_hist) < 2:
            return False
        now = time.monotonic()
        # The earliest sample within the window.
        origin = None
        for t, x, y in self._pose_hist:
            if now - t <= self._pose_window:
                origin = (x, y)
                break
        if origin is None:
            origin = (self._pose_hist[0][1], self._pose_hist[0][2])
        d = math.hypot(self._pose[0] - origin[0], self._pose[1] - origin[1])
        return d >= self._pose_change

    def _moving(self):
        """DRIVING is true ONLY when path + velocity>0 + pose is changing."""
        return self._has_plan and self._speed > self._motion_threshold \
            and self._pose_changing()

    def _exec_state(self):
        """Derive the authoritative execution state from engine signals."""
        if self._status != "ONLINE":
            return "OFFLINE"

        # Physical charging takes precedence over every other state.
        if self._charging:
            return "CHARGING"

        # Wants to charge but not yet at the pad → returning to the charger.
        if self._want_charge:
            return "RETURNING"

        if not self._current_task:
            # No active task. RETURNING only while actually navigating (e.g. a
            # home/operator goal) — a stale goal with no motion is IDLE.
            if self._moving() or self._want_charge:
                return "RETURNING"
            return "IDLE"

        if self._fsm_state == "IDLE":
            # A task is assigned but the task manager has not started it.
            return "ASSIGNED"

        exec_state = _FSM_EXEC.get(self._fsm_state, "ASSIGNED")

        # Movement states require a valid path AND actual motion. Without them
        # the robot is still PLANNING (or waiting to move).
        if exec_state in ("MOVING_TO_PICKUP", "CARRYING"):
            if not self._moving():
                return "PLANNING"
        elif exec_state == "MOVING_TO_DROPOFF":
            if not self._has_plan:
                return "PLANNING"
        return exec_state

    # ── Periodic publishing ────────────────────────────────────

    def _publish_heartbeat(self):
        msg = String()
        msg.data = self._robot_id
        self._hb_pub.publish(msg)

    def _publish_pose(self):
        if self._pose is not None:
            msg = String()
            msg.data = (
                f"{self._robot_id},{self._pose[0]},{self._pose[1]},"
                f"{self._pose[2]}"
            )
            self._pose_pub.publish(msg)

    def _control_loop(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        # Simulated reboot: publish OFFLINE for the restart window, then back ONLINE.
        if self._restart_until is not None and now >= self._restart_until:
            self._restart_until = None
            self._status = "ONLINE"
            self.get_logger().info(f"{self._robot_id}: restart complete — back ONLINE")
        self._update_battery(dt)
        self._publish_battery()
        self._publish_heartbeat()
        self._publish_pose()
        self._publish_registration()
        # Pose history is pruned in _pose_callback; nothing else to update here.

    def _publish_registration(self):
        exec_state = self._exec_state()
        moving = 1 if self._moving() else 0
        msg = String()
        msg.data = (
            f"{self._robot_id},{self._status},{self._current_task},"
            f"{self._payload_capacity},{self._max_speed},{self._robot_type},"
            f"{self._workload},{self._priority},"
            f"{round(self._battery, 1)},{1 if self._charging else 0},"
            f"{self.get_namespace()},{exec_state},{moving}"
        )
        self._reg_pub.publish(msg)


def main():
    rclpy.init()
    node = RobotStatusPublisher()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
