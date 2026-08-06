"""System monitoring: CPU, memory, disk, DB, ROS/API/dashboard status.

A background sampler records a rolling ring of samples; /api/monitoring
returns both the live snapshot and the ring for charts.
"""

import collections
import os
import threading
import time
import urllib.request

import psutil

from .config import get_config
from .logging_config import get_logger

log = get_logger("warehouse.monitoring")

_lock = threading.Lock()
_ring: collections.deque = collections.deque(maxlen=120)
_request_counts = {"total": 0, "by_path": {}}
_requests_lock = threading.Lock()
_started = time.time()

# Cached dashboard probe (avoids a network round-trip on every health check).
_dashboard_probe = {"ts": 0.0, "result": None}
_dashboard_lock = threading.Lock()


def _probe_dashboard(url, interval=10.0):
    """GET the Control Center /api/state with caching and latency capture."""
    if not url:
        return {"ok": False, "status": "not configured", "latency_ms": None}
    now = time.time()
    with _dashboard_lock:
        if _dashboard_probe["result"] and now - _dashboard_probe["ts"] < interval:
            return _dashboard_probe["result"]
    result = {"ok": False, "status": "unreachable", "latency_ms": None}
    try:
        start = time.perf_counter()
        req = urllib.request.Request(f"{url}/api/state", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        result = {
            "ok": True,
            "status": "ok",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception:
        result = {"ok": False, "status": "unreachable", "latency_ms": None}
    with _dashboard_lock:
        _dashboard_probe["ts"] = now
        _dashboard_probe["result"] = result
    return result


def record_request(path):
    with _requests_lock:
        _request_counts["total"] += 1
        _request_counts["by_path"][path] = _request_counts["by_path"].get(path, 0) + 1


def sample():
    proc = psutil.Process(os.getpid())
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    rec = {
        "ts": round(time.time(), 3),
        "cpu_percent": proc.cpu_percent(interval=None),
        "memory_percent": proc.memory_percent(),
        "rss_mb": round(proc.memory_info().rss / (1024 * 1024), 2),
        "system_memory_percent": mem.percent,
        "disk_percent": disk.percent,
        "threads": proc.num_threads(),
        "connections": len(proc.connections()),
        "requests_total": _request_counts["total"],
        "ws_clients": _ws_clients(),
    }
    with _lock:
        _ring.append(rec)
    return rec


def _ws_clients():
    try:
        from . import ws_manager

        return ws_manager.client_count()
    except Exception:
        return 0


def ring():
    with _lock:
        return list(_ring)


def uptime():
    return round(time.time() - _started, 1)


def component_status():
    """Health of each subsystem (db, ros bridge, fleet, dashboard, analytics)."""
    from . import repository as repo

    cfg = get_config()
    db_ok = True
    db_size = 0
    try:
        db_path = cfg.get("database.path", "./data/warehouse.db")
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    except Exception:
        db_ok = False

    fleet = repo.latest_fleet_status()
    analytics = repo.latest_analytics()
    now = time.time()

    ros_bridge_age = None
    ros_bridge = "unknown"
    last_ingest = _last_ingest()
    if last_ingest is not None:
        ros_bridge_age = round(now - last_ingest, 1)
        ros_bridge = "ok" if ros_bridge_age < 15 else "stale"

    dashboard_url = cfg.get("dashboard.backend_url", "")
    dashboard = _probe_dashboard(
        dashboard_url, cfg.get("dashboard.check_interval", 10.0)
    )
    dashboard.update({"url": dashboard_url})
    return {
        "db": {
            "ok": db_ok,
            "path": cfg.get("database.path"),
            "size_bytes": db_size,
            "tables": repo.table_sizes(),
        },
        "ros_bridge": {
            "ok": ros_bridge == "ok",
            "status": ros_bridge,
            "last_ingest_age_s": ros_bridge_age,
        },
        "fleet": {"ok": bool(fleet), "latest": fleet},
        "dashboard": dashboard,
        "analytics": {
            "ok": bool(analytics),
            "last_snapshot_age_s": (
                round(now - analytics["ts"], 1) if analytics else None
            ),
        },
        "api": {"ok": True, "uptime_s": uptime(), "requests": _request_counts},
        "uptime_s": uptime(),
    }


_last_ingest_ts = [None]


def mark_ingest():
    _last_ingest_ts[0] = time.time()


def _last_ingest():
    return _last_ingest_ts[0]
