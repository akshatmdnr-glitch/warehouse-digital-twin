"""Unit tests for the A* planner on a synthetic occupancy grid."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'warehouse_bringup'))

import planner_node  # noqa: E402


class _Info:
    def __init__(self):
        self.origin = type('O', (), {'position': type('P', (), {'x': -0.0, 'y': -0.0})()})()
        self.resolution = 0.05
        self.width = 100
        self.height = 100


@pytest.fixture()
def planner():
    import logging
    p = planner_node.PlannerNode.__new__(planner_node.PlannerNode)
    p._logger = logging.getLogger('test.planner')
    p._cell_size = 0.05
    p._obstacle_cost = 100.0
    p._inflation = 2
    p._map_info = _Info()
    p._width = 100
    p._height = 100
    # open grid
    p._map_data = [0] * (100 * 100)
    # add a vertical wall at x=50 cells from row 20..80
    for j in range(20, 80):
        p._map_data[j * 100 + 50] = 100
    return p


def test_planner_finds_path(planner):
    path = planner._compute_path(0.5, 0.5, 4.5, 4.5)
    assert path is not None
    assert len(path) >= 2
    assert abs(path[0][0] - 0.5) < 0.1
    assert abs(path[-1][0] - 4.5) < 0.1


def test_planner_goes_around_wall(planner):
    # start left of wall, goal right of wall -> path must bypass it
    path = planner._compute_path(1.0, 1.0, 4.0, 1.0)
    assert path is not None
    for x, y in path:
        # never pass through the wall column (world x == 2.5 = cell 50)
        assert not (abs(x - 2.5) < 0.05 and 1.0 <= y <= 4.0)


def test_planner_goal_on_obstacle(planner):
    # Goal on the wall is snapped to the nearest clear cell and planned to.
    path = planner._compute_path(1.0, 1.0, 2.5, 2.5)  # goal on the wall
    assert path is not None
    # The snapped goal must be drivable (clear of the wall + inflation).
    gx, gy = path[-1]
    gi, gj = planner._world_to_grid(gx, gy)
    assert not planner._is_obstacle(gi, gj)
    assert not planner._near_obstacle(gi, gj)


def test_planner_out_of_bounds(planner):
    # Start just outside the map edge is snapped to the nearest drivable cell
    # and planned; far-outside starts (beyond the snap radius) return None.
    path = planner._compute_path(-0.05, 1.0, 1.0, 1.0)
    assert path is not None
    sx, sy = path[0]
    si, sj = planner._world_to_grid(sx, sy)
    assert planner._in_bounds(si, sj)
    assert not planner._is_obstacle(si, sj)
    assert planner._compute_path(-10.0, -10.0, 1.0, 1.0) is None


def test_planner_start_near_obstacle_is_snapped(planner):
    # A start cell that is clear but inside the inflation radius is snapped so
    # the robot can actually leave it (otherwise A* could never escape).
    path = planner._compute_path(2.49, 1.0, 4.0, 1.0)  # just left of wall cell
    assert path is not None
    sx, sy = path[0]
    si, sj = planner._world_to_grid(sx, sy)
    assert not planner._near_obstacle(si, sj)
