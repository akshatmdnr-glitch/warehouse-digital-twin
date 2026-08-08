#!/usr/bin/env python3
"""Demo mission trigger — submits two concurrent tasks to the fleet dispatcher.

Publishes two /add_task CSV messages (the fleet manager's input format) after a
short startup delay. The fleet manager assigns the first task to the best idle
robot (robot1) and, because it is now busy, the second task to robot2 — so both
robots execute simultaneously. The fleet manager's reservation system keeps
their routes from colliding (a robot waits only while the other holds a shared
aisle segment, then continues automatically).

/add_task format: task_id,pickup_x,pickup_y,dropoff_x,dropoff_y,priority,required_payload
"""

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class DemoMission(Node):
    def __init__(self):
        super().__init__("demo_mission")
        self.declare_parameter("task1_id", "DEMO01")
        self.declare_parameter("task1_pickup", "-4.0,2.2")
        self.declare_parameter("task1_dropoff", "4.0,-2.2")
        self.declare_parameter("task2_id", "DEMO02")
        self.declare_parameter("task2_pickup", "0.0,0.8")
        self.declare_parameter("task2_dropoff", "4.0,2.2")
        self.declare_parameter("priority", 1)
        self.declare_parameter("start_delay", 6.0)
        self.declare_parameter("stagger", 1.5)

        self._tasks = [
            (
                self.get_parameter("task1_id").value,
                self.get_parameter("task1_pickup").value,
                self.get_parameter("task1_dropoff").value,
            ),
            (
                self.get_parameter("task2_id").value,
                self.get_parameter("task2_pickup").value,
                self.get_parameter("task2_dropoff").value,
            ),
        ]
        self._priority = int(self.get_parameter("priority").value)
        self._delay = float(self.get_parameter("start_delay").value)
        self._stagger = float(self.get_parameter("stagger").value)
        self._idx = 0

        self._pub = self.create_publisher(String, "/add_task", 10)
        self._timer = self.create_timer(self._delay, self._publish_next)
        self.get_logger().info(
            f"Demo mission ready: {len(self._tasks)} tasks after {self._delay:.1f}s "
            f"(stagger {self._stagger:.1f}s)"
        )

    def _publish_next(self):
        if self._idx >= len(self._tasks):
            self._timer.cancel()
            return
        tid, pickup, dropoff = self._tasks[self._idx]
        msg = String()
        msg.data = f"{tid},{pickup},{dropoff},{self._priority},0"
        self._pub.publish(msg)
        self.get_logger().info(
            f"Submitted {tid}: pickup=({pickup}) dropoff=({dropoff})"
        )
        self._idx += 1
        if self._idx < len(self._tasks):
            # Submit the second task after the stagger so the fleet manager
            # has already marked the first robot busy (guarantees Task2 ->
            # the other robot).
            self._timer.timer_period_ns = int(self._stagger * 1e9)
        else:
            self._timer.cancel()


def main():
    rclpy.init()
    node = DemoMission()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
