"""Background services: cleanup, archiving, pruning, health monitoring,
heartbeat verification, automatic backups and hourly analytics reports.

All services run as asyncio tasks started on FastAPI startup and can be
paused/resumed through /api/services.
"""

import asyncio
import json
import os
import threading
import time

from . import repository as repo
from . import ws_manager
from .config import get_config
from .logging_config import get_logger, log_structured

log = get_logger("warehouse.services")

_running: dict = {}
_paused: set = set()


def _days(d):
    return d * 86400


def start_all():
    _running["cleanup"] = asyncio.create_task(_run_service("cleanup", _cleanup))
    _running["archive"] = asyncio.create_task(_run_service("archive", _archive))
    _running["backup"] = asyncio.create_task(_run_service("backup", _backup))
    _running["health"] = asyncio.create_task(_run_service("health", _health_monitor))
    _running["heartbeat"] = asyncio.create_task(
        _run_service("heartbeat", _heartbeat_verification)
    )
    _running["reports"] = asyncio.create_task(_run_service("reports", _hourly_reports))
    log.info("background services started", extra={"services": list(_running)})


def stop_all():
    for task in _running.values():
        task.cancel()
    _running.clear()


def service_status():
    return {
        name: {
            "running": not task.done(),
            "paused": name in _paused,
            "interval": _service_interval(name),
        }
        for name, task in _running.items()
    }


def _service_interval(name):
    cfg = get_config()
    return {
        "cleanup": cfg.get("services.cleanup_interval_seconds", 3600),
        "archive": cfg.get("services.archive_interval_seconds", 86400),
        "backup": cfg.get("services.backup_interval_seconds", 86400),
        "health": cfg.get("services.health_monitor_interval_seconds", 5),
        "heartbeat": cfg.get("services.heartbeat_verification_interval_seconds", 3),
        "reports": cfg.get("analytics.report_hourly_minutes", 5) * 60,
    }[name]


async def _run_service(name, fn):
    while True:
        try:
            if name not in _paused:
                await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("service error", extra={"service": name, "error": str(e)})
        await asyncio.sleep(_service_interval(name))


def pause(name):
    _paused.add(name)


def resume(name):
    _paused.discard(name)


def _retention() -> dict:
    return dict(get_config().get("retention") or {})


async def _cleanup():
    """Prune time-series rows older than the configured retention."""
    ret = _retention()
    now = time.time()
    total = 0
    total += repo.prune(
        "robot_positions", "ts", now - _days(ret["robot_positions_days"])
    )
    total += repo.prune(
        "battery_history", "ts", now - _days(ret["battery_history_days"])
    )
    total += repo.prune("events", "ts", now - _days(ret["events_days"]))
    total += repo.prune("queue_history", "ts", now - _days(ret["queue_history_days"]))
    total += repo.prune(
        "analytics_snapshots", "ts", now - _days(ret["analytics_snapshots_days"])
    )
    if total:
        log.info("cleanup removed rows", extra={"rows": total})


async def _archive():
    """Move old event rows into *_archive tables."""
    now = time.time()
    before = now - _days(_retention().get("events_days", 90))
    moved = repo.archive("events", before)
    if moved:
        log.info("archived events", extra={"rows": moved})


async def _backup():
    """Take a SQLite online backup and prune old backups."""
    cfg = get_config()
    backup_dir = cfg.get("database.backup_dir", "./data/backups")
    keep = cfg.get("services.backup_keep", 7)
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"warehouse_{stamp}.db")
    repo.get_db().backup(dest)
    log.info("backup created", extra={"path": dest})
    backups = sorted(f for f in os.listdir(backup_dir) if f.endswith(".db"))
    for old in backups[:-keep]:
        os.remove(os.path.join(backup_dir, old))


async def _health_monitor():
    """Broadcast periodic health messages over the WebSocket hub."""
    ws_manager.broadcast(
        {
            "type": "health",
            "data": {
                "ts": time.time(),
                "db": "ok",
                "uptime_s": _uptime(),
            },
        }
    )


_uptime_start = time.time()


def _uptime():
    return round(time.time() - _uptime_start, 1)


async def _heartbeat_verification():
    """Verify fleet heartbeats and mark stale robots OFFLINE in the DB."""
    timeout = get_config().get("services.heartbeat_timeout_seconds", 3)
    now = time.time()
    changed = []
    for r in repo.list_robots(status="ONLINE"):
        last = r.get("last_seen")
        if last and now - last > timeout:
            repo.update_robot(r["robot_id"], status="OFFLINE")
            repo.add_event(
                "robot_offline",
                "high",
                r["robot_id"],
                f'Robot {r["robot_id"]} no heartbeat for >{timeout}s',
            )
            repo.add_alert(
                "robot_offline",
                "high",
                r["robot_id"],
                "Robot offline",
                f'Robot {r["robot_id"]} lost connectivity',
            )
            changed.append(r["robot_id"])
    if changed:
        log.warning(
            "heartbeat verification flagged robots offline", extra={"robots": changed}
        )
        ws_manager.broadcast({"type": "alerts", "data": {"robots": changed}})


async def _hourly_reports():
    """Roll hourly analytics metrics from the latest snapshot into the metrics table."""
    snap = repo.latest_analytics()
    if not snap:
        return
    payload = snap.get("payload", {})
    fleet = payload.get("fleet", {})
    for key, value in fleet.items():
        if isinstance(value, (int, float)):
            repo.add_analytics_metric(key, "", value, ts=snap["ts"], window="hourly")
