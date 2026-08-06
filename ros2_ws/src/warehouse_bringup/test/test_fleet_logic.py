"""Unit tests for the Fleet Manager's pure logic (no ROS spin required)."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'warehouse_bringup'))

from fleet_manager_node import FleetManagerNode  # noqa: E402


@pytest.fixture()
def fleet():
    import logging
    fm = FleetManagerNode.__new__(FleetManagerNode)
    fm._logger = logging.getLogger('test.fleet')
    fm._cell_size = 1.0
    fm._reservation_buffer = 0
    fm._segment_size = 2
    fm._lookahead = 1
    fm._w_distance = 1.0
    fm._w_workload = 1.0
    fm._w_priority = 1.0
    fm._w_capability = 1.0
    fm._low_battery = 30.0
    fm._critical_battery = 15.0
    fm._robots = {}
    fm._reservations = {}
    fm._cell_owners = {}
    fm._reservations_released = 0
    fm._pending_dispatches = []
    fm._retry_tasks = []
    return fm


def _robot(rid, x, y, status='ONLINE', task='', battery=100.0, cap=5.0,
           workload=0, priority=1.0, charging=False):
    return {'robot_id': rid, 'status': status, 'current_task': task,
            'x': x, 'y': y, 'payload_capacity': cap, 'max_speed': 0.22,
            'robot_type': 'burger', 'workload': workload, 'priority': priority,
            'battery': battery, 'charging': charging, 'namespace': rid}


def test_route_cells_ordered(fleet):
    cells = fleet._route_cells_ordered((0.0, 0.0), (3.0, 0.0))
    assert (0, 0) in cells
    assert (3, 0) in cells
    # monotonic in x
    xs = [c[0] for c in cells]
    assert xs == sorted(xs)


def test_partition_segments(fleet):
    cells = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)]
    segs = fleet._partition_segments(cells)
    assert segs == [[(0, 0), (1, 0)], [(2, 0), (3, 0)], [(4, 0)]]


def test_select_robot_prefers_nearest(fleet):
    fm = fleet
    fm._robots['r1'] = _robot('r1', 10.0, 0.0, workload=0)
    fm._robots['r2'] = _robot('r2', 1.0, 0.0, workload=0)
    best, _ = fm._select_robot(0.0, (0.0, 0.0))
    assert best['robot_id'] == 'r2'


def test_select_robot_excludes_offline_and_charging(fleet):
    fm = fleet
    fm._robots['off'] = _robot('off', 1.0, 0.0, status='OFFLINE')
    fm._robots['chg'] = _robot('chg', 1.0, 0.0, charging=True)
    fm._robots['busy'] = _robot('busy', 1.0, 0.0, task='T1')
    fm._robots['low'] = _robot('low', 1.0, 0.0, battery=10.0)
    fm._robots['ok'] = _robot('ok', 1.0, 0.0)
    best, _ = fm._select_robot(0.0, (0.0, 0.0))
    assert best['robot_id'] == 'ok'


def test_select_robot_capability_requirement(fleet):
    fm = fleet
    fm._robots['small'] = _robot('small', 1.0, 0.0, cap=2.0)
    fm._robots['big'] = _robot('big', 5.0, 0.0, cap=10.0)
    best, _ = fm._select_robot(5.0, (0.0, 0.0))
    assert best['robot_id'] == 'big'


def test_head_on_conflict_detection(fleet):
    fm = fleet
    route_a = set(fleet._route_cells_ordered((0.0, 0.0), (3.0, 0.0)))
    fm._reservations['r1'] = {
        'route': fleet._route_cells_ordered((0.0, 0.0), (3.0, 0.0)),
        'route_dir': (3.0, 0.0)}
    # opposite direction overlaps -> head-on
    assert fm._has_head_on_conflict(route_a, (-3.0, 0.0))
    # same direction -> not head-on
    assert not fm._has_head_on_conflict(route_a, (3.0, 0.0))


def test_reserve_and_release_segments(fleet):
    fm = fleet
    route = fleet._route_cells_ordered((0.0, 0.0), (4.0, 0.0))
    segs = fleet._partition_segments(route)
    task = {'task_id': 'T1', 'pickup': (0.0, 0.0), 'dropoff': (4.0, 0.0),
            'priority': 1, 'required_payload': 0.0}
    ok = fleet._reserve('r1', task, route, segs, (4.0, 0.0))
    assert ok
    assert len(fm._cell_owners) > 0
    res = fm._reservations['r1']
    assert len(res['reserved_segment_indices']) > 0
    # releasing clears owners
    fm._release_reservations('r1')
    assert 'r1' not in fm._reservations
    assert len(fm._cell_owners) == 0
