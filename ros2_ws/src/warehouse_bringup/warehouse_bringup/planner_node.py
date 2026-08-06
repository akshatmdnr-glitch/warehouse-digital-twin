"""A* global path planner on an occupancy grid.

Subscribes: /map, /amcl_pose, /goal_pose
Publishes:  /plan (nav_msgs/Path)
"""

import heapq
import math

import rclpy
from geometry_msgs.msg import Point, PoseStamped, PoseWithCovarianceStamped, Quaternion
from nav_msgs.msg import OccupancyGrid, Path
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

# 8-connected neighbor offsets (dx, dy, cost_multiplier)
_NEIGHBORS = [
    (0, 1, 1.0),
    (1, 0, 1.0),
    (0, -1, 1.0),
    (-1, 0, 1.0),
    (1, 1, math.sqrt(2)),
    (1, -1, math.sqrt(2)),
    (-1, 1, math.sqrt(2)),
    (-1, -1, math.sqrt(2)),
]


def _euler_to_quaternion(yaw):
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return Quaternion(x=0.0, y=0.0, z=sy, w=cy)


class PlannerNode(Node):
    def __init__(self):
        super().__init__("planner")
        self.declare_parameter("grid_cell_size", 0.05)
        self.declare_parameter("obstacle_cost", 100.0)
        self.declare_parameter("inflation_radius_cells", 2)

        self._cell_size = self.get_parameter("grid_cell_size").value
        self._obstacle_cost = self.get_parameter("obstacle_cost").value
        self._inflation = self.get_parameter("inflation_radius_cells").value

        self._map_data = None
        self._map_info = None
        self._width = 0
        self._height = 0

        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._map_callback, 10
        )
        self._goal_sub = self.create_subscription(
            PoseStamped, "/goal_pose", self._goal_callback, 10
        )
        self._pose_sub = self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._pose_callback, 10
        )
        self._plan_pub = self.create_publisher(Path, "/plan", 10)

        self._current_pose = None
        self._current_goal = None

        self.get_logger().info("Planner ready, waiting for map...")

    def _map_callback(self, msg):
        self._map_data = msg.data
        self._map_info = msg.info
        self._width = msg.info.width
        self._height = msg.info.height
        if self._current_pose and self._current_goal:
            self._plan()

    def _pose_callback(self, msg):
        pose_stamped = PoseStamped()
        pose_stamped.header = msg.header
        pose_stamped.pose = msg.pose.pose
        self._current_pose = pose_stamped
        if self._current_goal:
            self._plan()

    def _goal_callback(self, msg):
        self._current_goal = msg
        if self._current_pose:
            self._plan()

    def _plan(self):
        if not self._map_data or not self._map_info:
            return

        start = self._current_pose
        goal = self._current_goal

        sx = start.pose.position.x
        sy = start.pose.position.y
        gx = goal.pose.position.x
        gy = goal.pose.position.y

        path = self._compute_path(sx, sy, gx, gy)

        if not path:
            self.get_logger().warn(
                f"No valid path from ({sx:.2f}, {sy:.2f}) to ({gx:.2f}, {gy:.2f})"
            )
            return

        path_msg = Path()
        path_msg.header.stamp = self.get_clock().now().to_msg()
        path_msg.header.frame_id = "map"

        for i, (px, py) in enumerate(path):
            pose = PoseStamped()
            pose.header.frame_id = "map"
            pose.pose.position = Point(x=px, y=py, z=0.0)
            if i < len(path) - 1:
                yaw = math.atan2(
                    path[i + 1][1] - py,
                    path[i + 1][0] - px,
                )
            else:
                yaw = 0.0
            pose.pose.orientation = _euler_to_quaternion(yaw)
            path_msg.poses.append(pose)

        self._plan_pub.publish(path_msg)
        self.get_logger().info(
            f"Plan computed: {len(path)} poses from "
            f"({sx:.2f}, {sy:.2f}) to ({gx:.2f}, {gy:.2f})"
        )

    def _compute_path(self, sx, sy, gx, gy):
        """Compute an A* path using 8-connected neighbors."""
        si, sj = self._world_to_grid(sx, sy)
        gi, gj = self._world_to_grid(gx, gy)

        if not self._in_bounds(si, sj) or not self._in_bounds(gi, gj):
            self.get_logger().warn("Start or goal out of map bounds")
            return None

        if self._is_obstacle(gi, gj):
            self.get_logger().warn("Goal is on an obstacle")
            return None

        if self._is_obstacle(si, sj):
            self.get_logger().warn("Start is on an obstacle, trying nearby free cells")
            si, sj = self._nearest_free(si, sj)
            if si is None:
                return None

        # A* search
        start_key = (si, sj)
        open_set = [(0.0, 0, si, sj)]
        came_from = {start_key: None}
        g_score = {start_key: 0.0}

        while open_set:
            _, _, ci, cj = heapq.heappop(open_set)
            if (ci, cj) == (gi, gj):
                return self._reconstruct_path(came_from, ci, cj)

            cg = g_score[(ci, cj)]
            for di, dj, cost_mul in _NEIGHBORS:
                ni, nj = ci + di, cj + dj
                if not self._in_bounds(ni, nj):
                    continue
                if self._is_obstacle(ni, nj):
                    continue

                move_cost = cost_mul
                # Add extra cost near obstacles
                if self._near_obstacle(ni, nj):
                    move_cost += self._obstacle_cost * 0.1

                tg = cg + move_cost
                nk = (ni, nj)
                if tg < g_score.get(nk, float("inf")):
                    g_score[nk] = tg
                    f = tg + self._heuristic(ni, nj, gi, gj)
                    heapq.heappush(open_set, (f, -tg, ni, nj))
                    came_from[nk] = (ci, cj)

        return None

    def _reconstruct_path(self, came_from, gi, gj):
        path = [(gi, gj)]
        current = (gi, gj)
        while came_from[current] is not None:
            current = came_from[current]
            path.append(current)
        path.reverse()
        return [self._grid_to_world(i, j) for i, j in path]

    def _world_to_grid(self, x, y):
        mi = self._map_info
        i = int((x - mi.origin.position.x) / mi.resolution)
        j = int((y - mi.origin.position.y) / mi.resolution)
        return i, j

    def _grid_to_world(self, i, j):
        mi = self._map_info
        x = mi.origin.position.x + (i + 0.5) * mi.resolution
        y = mi.origin.position.y + (j + 0.5) * mi.resolution
        return x, y

    def _in_bounds(self, i, j):
        return 0 <= i < self._width and 0 <= j < self._height

    def _is_obstacle(self, i, j):
        if not self._in_bounds(i, j):
            return True
        val = self._map_data[j * self._width + i]
        return val >= 65 or val == -1

    def _near_obstacle(self, i, j):
        r = self._inflation
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if self._is_obstacle(i + di, j + dj):
                    return True
        return False

    def _nearest_free(self, i, j):
        for r in range(1, max(self._width, self._height)):
            for di in range(-r, r + 1):
                for dj in range(-r, r + 1):
                    if (di == 0 and dj == 0) or abs(di) + abs(dj) > r * 2:
                        continue
                    ni, nj = i + di, j + dj
                    if self._in_bounds(ni, nj) and not self._is_obstacle(ni, nj):
                        return ni, nj
        return None, None

    def _heuristic(self, ci, cj, gi, gj):
        return math.sqrt((ci - gi) ** 2 + (cj - gj) ** 2)


def main():
    rclpy.init()
    node = PlannerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
