"""Robot status beacon — publishes registration, heartbeat, pose, battery.

Subscribes: /task_state (std_msgs/String, JSON) — for current task
            /queue_info (std_msgs/String, JSON) — for workload
            /amcl_pose (PoseWithCovarianceStamped) — for position/orientation
            /battery_command (std_msgs/String) — operator simulation control
            /control (std_msgs/String) — operator control (simulated restart)
Publishes:  /robot_registration (std_msgs/String)
            /robot_heartbeat (std_msgs/String, robot_id at publish_rate)
            /robot_pose (std_msgs/String, robot_id,x,y,yaw)
            /battery_status (std_msgs/String, JSON)
            /goal_pose (PoseStamped) — charging-station goal on critical battery

Registration format: robot_id,status,current_task
                     [,payload_capacity[,max_speed[,robot_type
                     [,workload[,priority[,battery[,charging[,namespace]]]]]]]]

Battery behaviour: battery drains while the robot is executing a task and
recharges while charging. When battery reaches the critical threshold the
beacon enters the CHARGING state, publishes a goal to the (configurable)
charging station, charges back up to charge_complete_threshold and rejoins
the fleet (charging flag cleared).

Operator simulation controls (namespaced /ns/battery_command and /ns/control):
  battery_command: 'drain' | 'recharge' | 'set:<percent>' — forced-drain
      simulation, forced recharge (also sends the charging goal), or a direct
      battery set. These only affect the simulated battery, never navigation.
  control: 'restart' — simulated reboot: publish OFFLINE for 2 s, then ONLINE.
"""

import json
import math
import time

import rclpy
from geometry_msgs.msg import Point, PoseStamped, PoseWithCovarianceStamped, Quaternion
from rclpy.node import Node
from std_msgs.msg import String


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

        self._robot_id = self.get_parameter("robot_id").value
        self._status = self.get_parameter("status").value.upper()
        self._publish_rate = self.get_parameter("publish_rate").value
        self._payload_capacity = self.get_parameter("payload_capacity").value
        self._max_speed = self.get_parameter("max_speed").value
        self._robot_type = self.get_parameter("robot_type").value
        self._priority = self.get_parameter("priority").value
        self._current_task = ""
        self._workload = 0
        self._pose = None  # (x, y)

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
        self._charging = False
        self._battery_state = "IDLE"
        self._last_tick = time.monotonic()
        self._battery_forced_drain = False
        self._restart_until = None

        self._state_sub = self.create_subscription(
            String, "/task_state", self._state_callback, 10
        )
        self._queue_sub = self.create_subscription(
            String, "/queue_info", self._queue_callback, 10
        )
        self._amcl_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._pose_callback, 10
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
            self._current_task = data.get("active_task", "") or ""
        except (ValueError, TypeError):
            pass  # keep last known task on malformed input

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

    # ── Operator simulation controls ───────────────────────────

    def _battery_command_callback(self, msg):
        """Apply a battery simulation command: drain / recharge / set:<pct>."""
        cmd = msg.data.strip().lower()
        if cmd in ("drain", "discharge"):
            self._battery_forced_drain = True
            self.get_logger().info(f"{self._robot_id}: forced battery drain")
        elif cmd == "recharge":
            self._battery_forced_drain = False
            if not self._charging:
                self._charging = True
                self._battery_state = "CHARGING"
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
        if self._charging:
            self._battery = min(100.0, self._battery + self._charge_rate * dt)
            self._battery_state = "CHARGING"
            if self._battery >= self._charge_complete:
                self._charging = False
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
        elif self._current_task:
            # Driving / executing a task drains the battery.
            self._battery = max(0.0, self._battery - self._discharge_rate * dt)
            self._battery_state = "DISCHARGING"
        else:
            self._battery_state = "IDLE"

        if not self._charging and self._battery <= self._critical:
            self._charging = True
            self._battery_state = "CHARGING"
            self._publish_charging_goal()
            self.get_logger().warn(
                f"{self._robot_id}: critical battery ({self._battery:.1f}%) "
                f"— navigating to charging station {self._station}"
            )

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

    # ── Periodic publishing ────────────────────────────────────

    def _publish_heartbeat(self):
        msg = String()
        msg.data = self._robot_id
        self._hb_pub.publish(msg)

    def _publish_pose(self):
        if self._pose is not None:
            msg = String()
            msg.data = (
                f"{self._robot_id},{self._pose[0]},{self._pose[1]}," f"{self._pose[2]}"
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

    def _publish_registration(self):
        msg = String()
        msg.data = (
            f"{self._robot_id},{self._status},{self._current_task},"
            f"{self._payload_capacity},{self._max_speed},{self._robot_type},"
            f"{self._workload},{self._priority},"
            f"{round(self._battery, 1)},{1 if self._charging else 0},"
            f"{self.get_namespace()}"
        )
        self._reg_pub.publish(msg)


def main():
    rclpy.init()
    node = RobotStatusPublisher()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
