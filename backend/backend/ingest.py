"""Ingest handler — receives state/event batches from the ROS bridge.

The ROS ingest node observes the warehouse graph and POSTs batches to
/api/ingest. This module is pure business logic (no ROS imports): it persists
the batch and derives durable events, alerts, task history and charging
sessions by comparing each batch against the database (previous state).
"""

import time

from . import repository as repo
from . import ws_manager
from .config import get_config
from .logging_config import get_logger
from .monitoring import mark_ingest

log = get_logger("warehouse.ingest")


def process_batch(batch):
    """Persist a batch, derive events, broadcast real-time notifications."""
    counts = {
        "robots": 0,
        "positions": 0,
        "batteries": 0,
        "tasks": 0,
        "events": 0,
        "alerts": 0,
        "fleet": 0,
        "reservations": 0,
        "metrics": 0,
    }
    now = batch.get("ts") or time.time()

    # ── read previous state (before upserting) ──
    prev_robots = {}
    for r in batch.get("robots", []):
        rid = r.get("robot_id")
        if rid:
            prev_robots[rid] = repo.get_robot(rid)
    prev_tasks = {}
    for t in batch.get("tasks", []):
        tid = t.get("task_id")
        if tid:
            prev_tasks[tid] = repo.get_task(tid)
    prev_reservations = {
        (r["robot_id"], r["task_id"]) for r in repo.list_reservations(status="ACTIVE")
    }

    # ── robots + positions + batteries ──
    for r in batch.get("robots", []):
        repo.upsert_robot(_robot_dict(r))
        counts["robots"] += 1
        if r.get("x") is not None:
            repo.add_position(
                r["robot_id"], r.get("x"), r.get("y", 0), r.get("yaw", 0), now
            )
            counts["positions"] += 1
        if r.get("battery") is not None:
            repo.add_battery_sample(
                r["robot_id"], r.get("battery"), bool(r.get("charging")), now
            )
            counts["batteries"] += 1

    for p in batch.get("positions", []):
        if p.get("robot_id"):
            repo.add_position(
                p["robot_id"],
                p.get("x", 0),
                p.get("y", 0),
                p.get("yaw", 0),
                p.get("ts", now),
            )
            counts["positions"] += 1

    for b in batch.get("batteries", []):
        if b.get("robot_id"):
            repo.add_battery_sample(
                b["robot_id"],
                b.get("battery", 0),
                bool(b.get("charging")),
                b.get("ts", now),
            )
            counts["batteries"] += 1

    # ── tasks ──
    for t in batch.get("tasks", []):
        repo.upsert_task(_task_dict(t))
        counts["tasks"] += 1

    # ── fleet + queue + reservations ──
    if batch.get("fleet"):
        repo.add_fleet_status(batch["fleet"])
        counts["fleet"] = 1

    for q in batch.get("queue", []):
        if q.get("task_id"):
            repo.add_queue_history(
                q.get("robot_id", ""),
                q.get("task_id", ""),
                q.get("event", "queued"),
                q.get("ts", now),
            )
            counts["reservations"] += 1

    for res in batch.get("reservations", []):
        if res.get("status") == "ACTIVE":
            repo.add_reservation(
                res.get("robot_id", ""),
                res.get("task_id", ""),
                res.get("segments", 0),
                bool(res.get("head_on")),
            )
        elif res.get("status") == "RELEASED":
            repo.release_reservation(res.get("robot_id", ""), res.get("task_id", ""))
        counts["reservations"] += 1

    # ── derive events/alerts/history from transitions ──
    derived = _derive_transitions(
        batch, prev_robots, prev_tasks, prev_reservations, now
    )

    # ── explicit events + alerts ──
    for e in batch.get("events", []):
        repo.add_event(
            e.get("type", "system"),
            e.get("severity", "info"),
            e.get("robot", "") or e.get("robot_id", ""),
            e.get("message", ""),
            e.get("ts", now),
        )
        counts["events"] += 1
    for e in derived:
        repo.add_event(e["type"], e["severity"], e["robot"], e["message"], now)
        counts["events"] += 1

    for a in batch.get("alerts", []):
        if a.get("active"):
            repo.add_alert(
                a.get("type", "generic"),
                a.get("severity", "warning"),
                a.get("robot", ""),
                a.get("title", "Alert"),
                a.get("message", ""),
                a.get("ts", now),
            )
            counts["alerts"] += 1

    # ── analytics ──
    if batch.get("analytics"):
        repo.add_analytics_snapshot(batch["analytics"])
        fleet = batch["analytics"].get("fleet", {})
        for key in (
            "avg_task_duration",
            "avg_queue_wait",
            "avg_reservation_wait",
            "total_completed_tasks",
        ):
            if fleet.get(key) is not None:
                repo.add_analytics_metric(key, "", fleet[key], ts=now)
                counts["metrics"] += 1

    for m in batch.get("metrics", []):
        repo.add_analytics_metric(
            m.get("metric"),
            m.get("robot_id", ""),
            m.get("value", 0),
            m.get("ts", now),
            m.get("window", ""),
        )
        counts["metrics"] += 1

    mark_ingest()

    # ── real-time broadcasts ──
    if batch.get("robots"):
        ws_manager.broadcast({"type": "robot_update", "data": batch["robots"][:50]})
    if batch.get("fleet"):
        ws_manager.broadcast({"type": "fleet", "data": batch["fleet"]})
    if derived:
        ws_manager.broadcast({"type": "events", "data": derived})
    if batch.get("alerts"):
        ws_manager.broadcast({"type": "alerts", "data": batch["alerts"][-20:]})
    if batch.get("analytics"):
        ws_manager.broadcast({"type": "analytics", "data": batch["analytics"]})

    return counts


def _derive_transitions(batch, prev_robots, prev_tasks, prev_reservations, now):
    """Compare the batch against the DB and emit durable events.

    Task completion is recorded exactly once: the task_status transition
    (ACTIVE -> COMPLETED) wins; the fleet "current_task became empty" signal
    only fires if the task is not already marked COMPLETED.
    """
    events = []

    # ---- task status transitions (per-robot task_status) ----
    completed_now = set()
    assigned_now = set()
    for t in batch.get("tasks", []):
        tid = t.get("task_id")
        status = t.get("status")
        prev = prev_tasks.get(tid)
        prev_status = prev["status"] if prev else None
        if status in ("COMPLETED", "ACTIVE") and prev_status != status:
            if status == "COMPLETED":
                completed_now.add(tid)
                repo.update_task(tid, status="COMPLETED", completed_at=now)
                robot = t.get("robot_id") or (prev or {}).get("robot_id") or ""
                repo.add_task_history(tid, robot, "COMPLETED", now, now)
                events.append(
                    {
                        "type": "task_completed",
                        "severity": "info",
                        "robot": robot,
                        "message": f"Task {tid} completed",
                    }
                )
                events.append(
                    {
                        "type": "navigation_completed",
                        "severity": "info",
                        "robot": robot,
                        "message": f"Navigation completed for task {tid}",
                    }
                )
            elif status == "ACTIVE":
                robot = t.get("robot_id") or (prev or {}).get("robot_id") or ""
                repo.add_task_history(tid, robot, "ACTIVE", now)
        elif status == "ASSIGNED" and prev_status not in ("ASSIGNED", "RUNNING"):
            assigned_now.add(tid)
            events.append(
                {
                    "type": "task_assigned",
                    "severity": "info",
                    "robot": t.get("robot_id", ""),
                    "message": f'Task {tid} assigned to {t.get("robot_id", "")}',
                }
            )

    # ---- robot transitions (fleet_status) ----
    for r in batch.get("robots", []):
        rid = r.get("robot_id")
        prev = prev_robots.get(rid)
        if not prev:
            continue
        status = r.get("status")
        charging = bool(r.get("charging"))
        task = r.get("current_task") or ""
        p_status = prev.get("status")
        p_charging = bool(prev.get("charging"))
        p_task = prev.get("current_task") or ""

        if p_status != status and status in ("ONLINE", "OFFLINE"):
            if status == "OFFLINE":
                events.append(
                    {
                        "type": "robot_offline",
                        "severity": "high",
                        "robot": rid,
                        "message": f"Robot {rid} is OFFLINE",
                    }
                )
                repo.add_alert(
                    "robot_offline",
                    "high",
                    rid,
                    "Robot offline",
                    f"Robot {rid} lost connectivity",
                    now,
                )
            else:
                events.append(
                    {
                        "type": "robot_online",
                        "severity": "info",
                        "robot": rid,
                        "message": f"Robot {rid} is back ONLINE",
                    }
                )

        if p_charging != charging:
            if charging:
                events.append(
                    {
                        "type": "charging_started",
                        "severity": "info",
                        "robot": rid,
                        "message": f"Robot {rid} started charging",
                    }
                )
                repo.add_charging_session(rid, now, r.get("battery"))
            else:
                events.append(
                    {
                        "type": "charging_finished",
                        "severity": "info",
                        "robot": rid,
                        "message": f"Robot {rid} finished charging",
                    }
                )
                repo.end_charging_session(rid, r.get("battery"))

        # battery low (crossing below the fleet threshold while not charging)
        low = float(get_config().get("fleet.battery.low_battery_threshold", 30.0))
        battery = r.get("battery")
        prev_battery = prev.get("battery")
        if (
            not charging
            and battery is not None
            and prev_battery is not None
            and prev_battery > low
            and battery <= low
        ):
            events.append(
                {
                    "type": "battery_low",
                    "severity": "warning",
                    "robot": rid,
                    "message": f"Robot {rid} battery low ({battery:.0f}%)",
                }
            )
            repo.add_alert(
                "battery_low",
                "warning",
                rid,
                "Battery low",
                f"Robot {rid} battery {battery:.0f}%",
                now,
            )

        # task completion signalled by the fleet (fallback path)
        if (
            p_task
            and not task
            and status == "ONLINE"
            and not charging
            and p_task not in completed_now
        ):
            cur = repo.get_task(p_task)
            if cur and cur["status"] != "COMPLETED":
                repo.update_task(p_task, status="COMPLETED", completed_at=now)
                repo.add_task_history(p_task, rid, "COMPLETED", now, now)
                events.append(
                    {
                        "type": "task_completed",
                        "severity": "info",
                        "robot": rid,
                        "message": f"Task {p_task} completed",
                    }
                )

    # ---- reservation granted (new ACTIVE reservation) ----
    for res in batch.get("reservations", []):
        if res.get("status") == "ACTIVE":
            key = (res.get("robot_id", ""), res.get("task_id", ""))
            if key not in prev_reservations:
                events.append(
                    {
                        "type": "reservation_granted",
                        "severity": "info",
                        "robot": key[0],
                        "message": f"Reservation granted to {key[0]} for {key[1]}",
                    }
                )

    return events


def _robot_dict(r):
    return {
        "robot_id": r.get("robot_id") or r.get("id"),
        "name": r.get("name"),
        "namespace": r.get("namespace", ""),
        "robot_type": r.get("robot_type", "unknown"),
        "status": r.get("status", "ONLINE"),
        "x": r.get("x"),
        "y": r.get("y"),
        "yaw": r.get("yaw"),
        "battery": r.get("battery"),
        "charging": bool(r.get("charging")),
        "current_task": r.get("current_task", ""),
        "payload_capacity": r.get("payload_capacity", 0),
        "max_speed": r.get("max_speed", 0),
        "workload": r.get("workload", 0),
        "priority": r.get("priority", 0),
        "last_seen": r.get("last_seen") or time.time(),
    }


def _task_dict(t):
    return {
        "task_id": t.get("task_id") or t.get("id"),
        "status": t.get("status", "PENDING"),
        "priority": t.get("priority", 1),
        "robot_id": t.get("robot_id") or t.get("robot") or "",
        "pickup_x": (t.get("pickup") or [0, 0])[0],
        "pickup_y": (t.get("pickup") or [0, 0])[1],
        "dropoff_x": (t.get("dropoff") or [0, 0])[0],
        "dropoff_y": (t.get("dropoff") or [0, 0])[1],
        "required_payload": t.get("required_payload", 0),
    }
