"""SQLite persistence layer with migrations.

Schema is versioned (schema_version table). On startup every pending migration
runs in order. All tables use a TEXT/REAL ts epoch format so the REST layer can
filter/sort without datetime parsing.

Automatic state restore: on restart the service reads the same database, so
robots, tasks, fleet state, events, alerts, analytics and history all survive.
"""

import json
import os
import sqlite3
import threading
import time

from .config import get_config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_login REAL,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS robots (
    robot_id TEXT PRIMARY KEY,
    name TEXT,
    namespace TEXT DEFAULT '',
    robot_type TEXT DEFAULT 'unknown',
    status TEXT DEFAULT 'ONLINE',
    x REAL,
    y REAL,
    yaw REAL,
    battery REAL,
    charging INTEGER DEFAULT 0,
    current_task TEXT DEFAULT '',
    payload_capacity REAL DEFAULT 0,
    max_speed REAL DEFAULT 0,
    workload INTEGER DEFAULT 0,
    priority REAL DEFAULT 0,
    last_seen REAL,
    first_seen REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS robot_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    yaw REAL NOT NULL DEFAULT 0,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_robot ON robot_positions(robot_id, ts);

CREATE TABLE IF NOT EXISTS battery_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    battery REAL NOT NULL,
    charging INTEGER NOT NULL DEFAULT 0,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_battery_robot ON battery_history(robot_id, ts);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'PENDING',
    priority INTEGER NOT NULL DEFAULT 1,
    robot_id TEXT DEFAULT '',
    pickup_x REAL DEFAULT 0,
    pickup_y REAL DEFAULT 0,
    dropoff_x REAL DEFAULT 0,
    dropoff_y REAL DEFAULT 0,
    required_payload REAL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL,
    completed_at REAL,
    cancelled_at REAL
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    robot_id TEXT DEFAULT '',
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL,
    duration REAL,
    cancelled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_history_task ON task_history(task_id);

CREATE TABLE IF NOT EXISTS fleet_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    robot_count INTEGER NOT NULL DEFAULT 0,
    online INTEGER NOT NULL DEFAULT 0,
    offline INTEGER NOT NULL DEFAULT 0,
    idle INTEGER NOT NULL DEFAULT 0,
    busy INTEGER NOT NULL DEFAULT 0,
    charging INTEGER NOT NULL DEFAULT 0,
    snapshot TEXT
);

CREATE TABLE IF NOT EXISTS queue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    robot_id TEXT DEFAULT '',
    task_id TEXT NOT NULL,
    event TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_history_ts ON queue_history(ts);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    segments INTEGER NOT NULL DEFAULT 0,
    head_on INTEGER NOT NULL DEFAULT 0,
    granted_at REAL NOT NULL,
    released_at REAL
);
CREATE INDEX IF NOT EXISTS idx_reservations_robot ON reservations(robot_id);

CREATE TABLE IF NOT EXISTS charging_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    start_battery REAL,
    end_battery REAL,
    duration REAL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    robot_id TEXT DEFAULT '',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    robot_id TEXT DEFAULT '',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    ts REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    cleared_ts REAL
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analytics_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,
    robot_id TEXT DEFAULT '',
    value REAL NOT NULL,
    ts REAL NOT NULL,
    window TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_metrics_metric ON analytics_metrics(metric, ts);

CREATE TABLE IF NOT EXISTS maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    resolution REAL NOT NULL,
    origin_x REAL DEFAULT 0,
    origin_y REAL DEFAULT 0,
    data BLOB,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    revoked INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    """Thin SQLite wrapper with a per-process connection."""

    def __init__(self, path=None):
        self._path = path or get_config().get("database.path", "./data/warehouse.db")
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    # ── connection helpers ──
    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self):
        with self._lock:
            self._conn.close()

    # ── migrations ──
    def migrate(self):
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        version = self._current_version()
        for fn in _migrations:
            num = int(fn.__name__.split("_")[1].lstrip("m"))  # 'm0001' -> 1
            if num > version:
                with self._lock:
                    fn(self._conn)
                    self._conn.execute(
                        "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                        (num, time.time()),
                    )
                    self._conn.commit()

    def _current_version(self):
        row = self.query_one("SELECT MAX(version) AS v FROM schema_version")
        return row["v"] if row and row["v"] is not None else 0

    # ── backups ──
    def backup(self, dest_path):
        with self._lock:
            dest = sqlite3.connect(dest_path)
            self._conn.backup(dest)
            dest.close()
        return dest_path


def _m0001_seed_indexes(conn):
    conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active, ts)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_ts "
        "ON analytics_snapshots(ts)"
    )


_migrations = [_m0001_seed_indexes]


# Singleton.
_db = None


def get_db():
    global _db
    if _db is None:
        _db = Database()
    return _db


def reset_db(path=None):
    """Replace the singleton (used by tests)."""
    global _db
    if _db is not None:
        _db.close()
    _db = Database(path)
    return _db
