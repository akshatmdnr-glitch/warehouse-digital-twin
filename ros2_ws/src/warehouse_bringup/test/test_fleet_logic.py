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
    fm._w_queue = 1.0
    fm._w_battery = 1.0
    fm._w_current = 10.0
    fm._w_eta = 1.0
    fm._low_battery = 30.0
    fm._critical_battery = 15.0
    fm._robots = {}
    fm._reservations = {}
    fm._cell_owners = {}
    fm._reservations_released = 0
    fm._robot_queues = {}
    fm._waiting_tasks = []
    fm._task_seq = 0
    fm._retry_tasks = []
    fm._tasks_completed = 0
    # Stub publishers so dispatch/reservation paths don't touch ROS.
    class _Pub:
        def publish(self, msg):
            pass
    fm._assignment_pub = _Pub()
    fm._decision_pub = _Pub()
    fm._reservation_pub = _Pub()
    fm._recovery_pub = _Pub()
    fm._fleet_pub = _Pub()
    fm._monitor_pub = _Pub()
    fm._cancel_pub = _Pub()
    return fm


def _robot(rid, x, y, status='ONLINE', task='', battery=100.0, cap=5.0,
           workload=0, priority=1.0, charging=False):
    return {'robot_id': rid, 'status': status, 'current_task': task,
            'x': x, 'y': y, 'payload_capacity': cap, 'max_speed': 0.22,
            'robot_type': 'burger', 'workload': workload, 'priority': priority,
            'battery': battery, 'charging': charging, 'namespace': rid}


def _task(tid, pickup=(0.0, 0.0), dropoff=(3.0, 0.0), priority=1, payload=0.0):
    return {'task_id': tid, 'pickup': pickup, 'dropoff': dropoff,
            'priority': priority, 'required_payload': payload}


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
    best, _ = fm._select_robot(_task('T'))
    assert best['robot_id'] == 'r2'


def test_select_robot_excludes_offline_and_charging(fleet):
    fm = fleet
    fm._robots['off'] = _robot('off', 1.0, 0.0, status='OFFLINE')
    fm._robots['chg'] = _robot('chg', 1.0, 0.0, charging=True)
    fm._robots['busy'] = _robot('busy', 1.0, 0.0, task='T1')
    fm._robots['low'] = _robot('low', 1.0, 0.0, battery=10.0)
    fm._robots['ok'] = _robot('ok', 1.0, 0.0)
    best, _ = fm._select_robot(_task('T'))
    assert best['robot_id'] == 'ok'


def test_select_robot_capability_requirement(fleet):
    fm = fleet
    fm._robots['small'] = _robot('small', 1.0, 0.0, cap=2.0)
    fm._robots['big'] = _robot('big', 5.0, 0.0, cap=10.0)
    best, _ = fm._select_robot(_task('T', payload=5.0))
    assert best['robot_id'] == 'big'


def test_select_robot_prefers_idle_over_busy(fleet):
    """Never assign to a busy robot while an idle robot exists (rule 1)."""
    fm = fleet
    # busy robot is much closer, but an idle robot exists -> idle must win
    fm._robots['busy'] = _robot('busy', 0.5, 0.0, task='T_exec')
    fm._robots['idle'] = _robot('idle', 20.0, 0.0)
    best, _ = fm._select_robot(_task('T'))
    assert best['robot_id'] == 'idle'


def test_select_robot_finish_first_when_all_busy(fleet):
    """When every robot is busy, prefer the one that will finish first."""
    fm = fleet
    # both busy; 'early' has a short remaining route, 'late' a long one
    fm._robots['early'] = _robot('early', 1.0, 0.0, task='T1')
    fm._robots['late'] = _robot('late', 1.0, 0.0, task='T2')
    fm._reservations['early'] = {
        'segments': [[(0, 0), (1, 0)], [(2, 0)]], 'segment_index': 1,
    }
    fm._reservations['late'] = {
        'segments': [[(0, 0), (1, 0)], [(2, 0)], [(3, 0)], [(4, 0)]],
        'segment_index': 0,
    }
    best, _ = fm._select_robot(_task('T'))
    assert best['robot_id'] == 'early'


def test_balance_across_two_robots(fleet):
    """Six tasks with distinct, non-overlapping routes across two idle robots
    split ~3/3 — the scheduler must never pile everything on one robot."""
    fm = fleet
    fm._robots['r1'] = _robot('r1', 0.0, 0.0)
    fm._robots['r2'] = _robot('r2', 0.0, 0.0)
    # Each task has its own pickup AND dropoff so routes do not collide; the
    # split is decided purely by the scheduler, not by the reservation system.
    routes = [
        (2.0, 2.0, 9.0, 9.0),    # T0
        (-2.0, 2.0, -9.0, 9.0),  # T1
        (2.0, -2.0, 9.0, -9.0),  # T2
        (-2.0, -2.0, -9.0, -9.0),  # T3
        (3.0, 0.0, 9.0, 3.0),    # T4
        (-3.0, 0.0, -9.0, -3.0),  # T5
    ]
    for i, (px, py, dx, dy) in enumerate(routes):
        fm._schedule_task(_task(f'T{i}', pickup=(px, py), dropoff=(dx, dy)))
    def total(rid):
        return len(fm._robot_queues.get(rid, [])) + (1 if rid in fm._reservations else 0)
    q1, q2 = total('r1'), total('r2')
    # Balanced distribution — never all on one robot.
    assert abs(q1 - q2) <= 1
    assert q1 >= 2 and q2 >= 2


def test_rebalance_moves_queued_task_to_idle_robot(fleet):
    """When a robot finishes and another has queued (not-started) work, the
    task moves to the now-idle robot instead of waiting behind the busy one."""
    fm = fleet
    fm._robots['r1'] = _robot('r1', 0.0, 0.0)
    fm._robots['r2'] = _robot('r2', 0.0, 0.0)
    # First task dispatches to r1; remaining identical-route tasks queue on r2
    # (r1 is busy, r2 is the only idle robot).
    for i in range(3):
        fm._schedule_task(_task(f'T{i}', pickup=(2.0, 2.0), dropoff=(5.0, 5.0)))
    # r1 holds T0 (reserved); T1,T2 are queued (on r2).
    assert 'r1' in fm._reservations
    queued_before = sum(len(q) for q in fm._robot_queues.values())
    assert queued_before == 2
    # r1 finishes T0 -> rebalance hands a queued task to the freed robot.
    res = fm._reservations['r1']
    res['activated'] = True
    fm._robots['r1']['current_task'] = ''
    fm._check_reservation_release('r1', '')
    r1_work = len(fm._robot_queues.get('r1', [])) + (1 if 'r1' in fm._reservations else 0)
    assert r1_work >= 1


def test_never_drop_task_when_both_busy(fleet):
    """A task arriving while all robots are busy is queued, not dropped."""
    fm = fleet
    fm._robots['r1'] = _robot('r1', 0.0, 0.0, task='A')
    fm._robots['r2'] = _robot('r2', 0.0, 0.0, task='B')
    result = fm._schedule_task(_task('T'))
    assert result != "no_robot"
    all_queued = [t for q in fm._robot_queues.values() for t in q]
    assert any(t['task_id'] == 'T' for t in all_queued)


def test_waiting_task_assigned_when_robot_appears(fleet):
    """Tasks with no eligible robot wait, then get assigned on rebalance."""
    fm = fleet
    # no robots at all -> task waits
    result = fm._schedule_task(_task('T'))
    assert result == "no_robot"
    assert any(t['task_id'] == 'T' for t in fm._waiting_tasks)
    # a robot appears -> rebalance assigns it (queued or dispatched)
    fm._robots['r1'] = _robot('r1', 0.0, 0.0)
    fm._rebalance()
    assert not any(t['task_id'] == 'T' for t in fm._waiting_tasks)
    queued = [t for q in fm._robot_queues.values() for t in q]
    reserved = [r["task"]["task_id"] for r in fm._reservations.values()]
    assert any(t['task_id'] == 'T' for t in queued) or 'T' in reserved


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


def test_shared_corridor_dispatch_follows_window(fleet):
    """A second robot on a shared corridor is NOT blocked by the whole route:
    it dispatches once the sliding window ahead of the first robot is clear,
    so both robots stay busy instead of one idling."""
    fm = fleet
    fm._robots['r1'] = _robot('r1', 0.0, 0.0)
    fm._robots['r2'] = _robot('r2', 0.0, 0.0)
    # First task dispatches to r1 and reserves the initial window.
    fm._schedule_task(_task('T0', pickup=(1.0, 0.0), dropoff=(8.0, 0.0)))
    assert 'r1' in fm._reservations
    # r1 only holds its lookahead window (segment 0), not the whole route.
    res = fm._reservations['r1']
    assert len(res['reserved_segment_indices']) <= fm._lookahead + 1
    # Second robot starts a short task whose window is free -> it dispatches
    # instead of waiting for r1's entire route to clear.
    fm._schedule_task(_task('T1', pickup=(9.0, 0.0), dropoff=(9.5, 0.0)))
    assert 'r2' in fm._reservations


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
