"""Repository layer: all SQL against the warehouse database.

This keeps SQL out of the API handlers. Every function takes/returns plain
dicts; timestamps are epoch floats.
"""

import json
import sqlite3
import time

from .database import get_db

# ── robots ────────────────────────────────────────────────────


def upsert_robot(r):
    now = time.time()
    first = get_db().query_one(
        "SELECT first_seen FROM robots WHERE robot_id=?", (r.get("robot_id"),)
    )
    first_seen = first["first_seen"] if first else now
    get_db().execute(
        """INSERT INTO robots(robot_id, name, namespace, robot_type, status,
             x, y, yaw, battery, charging, current_task, payload_capacity,
             max_speed, workload, priority, last_seen, first_seen, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(robot_id) DO UPDATE SET
             name=excluded.name, namespace=excluded.namespace,
             robot_type=excluded.robot_type, status=excluded.status,
             x=excluded.x, y=excluded.y, yaw=excluded.yaw,
             battery=excluded.battery, charging=excluded.charging,
             current_task=excluded.current_task,
             payload_capacity=excluded.payload_capacity,
             max_speed=excluded.max_speed, workload=excluded.workload,
             priority=excluded.priority, last_seen=excluded.last_seen,
             updated_at=excluded.updated_at""",
        (
            r.get("robot_id"),
            r.get("name"),
            r.get("namespace", ""),
            r.get("robot_type", "unknown"),
            r.get("status", "ONLINE"),
            r.get("x"),
            r.get("y"),
            r.get("yaw"),
            r.get("battery"),
            1 if r.get("charging") else 0,
            r.get("current_task", ""),
            r.get("payload_capacity", 0),
            r.get("max_speed", 0),
            r.get("workload", 0),
            r.get("priority", 0),
            r.get("last_seen", now),
            first_seen,
            now,
        ),
    )


def update_robot(robot_id, **fields):
    allowed = {
        "name",
        "namespace",
        "robot_type",
        "status",
        "x",
        "y",
        "yaw",
        "battery",
        "charging",
        "current_task",
        "payload_capacity",
        "max_speed",
        "workload",
        "priority",
        "last_seen",
    }
    sets = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(robot_id)
    return (
        get_db()
        .execute(f'UPDATE robots SET {", ".join(sets)} WHERE robot_id=?', params)
        .rowcount
        > 0
    )


def list_robots(**filters):
    sql = "SELECT * FROM robots WHERE 1=1"
    params = []
    if filters.get("status"):
        sql += " AND status=?"
        params.append(filters["status"])
    if filters.get("charging"):
        sql += " AND charging=1"
    sql += " ORDER BY robot_id"
    return [_row(r) for r in get_db().query(sql, params)]


def get_robot(robot_id):
    r = get_db().query_one("SELECT * FROM robots WHERE robot_id=?", (robot_id,))
    return _row(r) if r else None


def delete_robot(robot_id):
    return (
        get_db().execute("DELETE FROM robots WHERE robot_id=?", (robot_id,)).rowcount
        > 0
    )


# ── positions & battery history ───────────────────────────────


def add_position(robot_id, x, y, yaw, ts):
    get_db().execute(
        "INSERT INTO robot_positions(robot_id, x, y, yaw, ts) VALUES (?,?,?,?,?)",
        (robot_id, x, y, yaw or 0, ts),
    )


def add_battery_sample(robot_id, battery, charging, ts):
    get_db().execute(
        "INSERT INTO battery_history(robot_id, battery, charging, ts) VALUES (?,?,?,?)",
        (robot_id, battery, 1 if charging else 0, ts),
    )


def position_history(robot_id, since=None, limit=1000):
    sql = "SELECT x, y, yaw, ts FROM robot_positions WHERE robot_id=?"
    params = [robot_id]
    if since:
        sql += " AND ts>=?"
        params.append(since)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in get_db().query(sql, params)]


def battery_history(robot_id=None, since=None, limit=1000):
    sql = "SELECT robot_id, battery, charging, ts FROM battery_history WHERE 1=1"
    params = []
    if robot_id:
        sql += " AND robot_id=?"
        params.append(robot_id)
    if since:
        sql += " AND ts>=?"
        params.append(since)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in get_db().query(sql, params)]


# ── tasks ─────────────────────────────────────────────────────


def upsert_task(t, created_at=None):
    now = created_at or time.time()
    existing = get_db().query_one(
        "SELECT created_at FROM tasks WHERE task_id=?", (t.get("task_id"),)
    )
    created = existing["created_at"] if existing else now
    get_db().execute(
        """INSERT INTO tasks(task_id, status, priority, robot_id, pickup_x,
             pickup_y, dropoff_x, dropoff_y, required_payload, created_at,
             updated_at, completed_at, cancelled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             status=excluded.status, priority=excluded.priority,
             robot_id=excluded.robot_id, pickup_x=excluded.pickup_x,
             pickup_y=excluded.pickup_y, dropoff_x=excluded.dropoff_x,
             dropoff_y=excluded.dropoff_y,
             required_payload=excluded.required_payload,
             updated_at=excluded.updated_at""",
        (
            t.get("task_id"),
            t.get("status", "PENDING"),
            t.get("priority", 1),
            t.get("robot_id", ""),
            t.get("pickup_x"),
            t.get("pickup_y"),
            t.get("dropoff_x"),
            t.get("dropoff_y"),
            t.get("required_payload", 0),
            created,
            now,
            t.get("completed_at"),
            t.get("cancelled_at"),
        ),
    )


def list_tasks(**filters):
    sql = "SELECT * FROM tasks WHERE 1=1"
    params = []
    for key in ("status", "priority", "robot_id"):
        if filters.get(key) is not None:
            sql += f" AND {key}=?"
            params.append(filters[key])
    sql += " ORDER BY created_at DESC"
    return [_row(r) for r in get_db().query(sql, params)]


def get_task(task_id):
    r = get_db().query_one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
    return _row(r) if r else None


def update_task(task_id, **fields):
    allowed = {
        "status",
        "priority",
        "robot_id",
        "pickup_x",
        "pickup_y",
        "dropoff_x",
        "dropoff_y",
        "required_payload",
        "completed_at",
        "cancelled_at",
    }
    sets = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(task_id)
    return (
        get_db()
        .execute(f'UPDATE tasks SET {", ".join(sets)} WHERE task_id=?', params)
        .rowcount
        > 0
    )


def delete_task(task_id):
    return (
        get_db().execute("DELETE FROM tasks WHERE task_id=?", (task_id,)).rowcount > 0
    )


def add_task_history(
    task_id, robot_id, status, started_at, completed_at=None, duration=None, cancelled=0
):
    get_db().execute(
        """INSERT INTO task_history(task_id, robot_id, status, started_at,
             completed_at, duration, cancelled) VALUES (?,?,?,?,?,?,?)""",
        (
            task_id,
            robot_id,
            status,
            started_at,
            completed_at,
            duration,
            1 if cancelled else 0,
        ),
    )


# ── fleet / queue / reservations ──────────────────────────────


def add_fleet_status(snapshot):
    f = snapshot.get("fleet", snapshot)
    get_db().execute(
        """INSERT INTO fleet_status(ts, robot_count, online, offline, idle,
             busy, charging, snapshot) VALUES (?,?,?,?,?,?,?,?)""",
        (
            time.time(),
            f.get("total_robots", snapshot.get("total", 0)),
            f.get("online_robots", snapshot.get("online", 0)),
            f.get("offline_robots", snapshot.get("offline", 0)),
            f.get("idle_robots", snapshot.get("idle", 0)),
            f.get("busy_robots", snapshot.get("busy", 0)),
            f.get("charging_robots", snapshot.get("charging", 0)),
            _dump(snapshot),
        ),
    )


def latest_fleet_status():
    r = get_db().query_one("SELECT * FROM fleet_status ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    d = _row(r)
    try:
        d["snapshot"] = json.loads(d["snapshot"]) if d["snapshot"] else None
    except Exception:
        d["snapshot"] = None
    return d


def fleet_history(limit=100):
    rows = get_db().query(
        "SELECT ts, robot_count, online, offline, idle, "
        "busy, charging FROM fleet_status ORDER BY id DESC "
        "LIMIT ?",
        (limit,),
    )
    return [_row(r) for r in rows]


def add_queue_history(robot_id, task_id, event, ts=None):
    get_db().execute(
        "INSERT INTO queue_history(ts, robot_id, task_id, event) VALUES (?,?,?,?)",
        (ts or time.time(), robot_id or "", task_id, event),
    )


def queue_history(limit=500):
    rows = get_db().query(
        "SELECT * FROM queue_history ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [_row(r) for r in rows]


def add_reservation(robot_id, task_id, segments, head_on):
    get_db().execute(
        """INSERT INTO reservations(robot_id, task_id, status, segments, head_on,
             granted_at) VALUES (?,?,?,?,?,?)""",
        (robot_id, task_id, "ACTIVE", segments, 1 if head_on else 0, time.time()),
    )


def release_reservation(robot_id, task_id):
    return (
        get_db()
        .execute(
            """UPDATE reservations SET status='RELEASED', released_at=?
           WHERE robot_id=? AND task_id=? AND status='ACTIVE'""",
            (time.time(), robot_id, task_id),
        )
        .rowcount
        > 0
    )


def list_reservations(**filters):
    sql = "SELECT * FROM reservations WHERE 1=1"
    params = []
    if filters.get("status"):
        sql += " AND status=?"
        params.append(filters["status"])
    sql += " ORDER BY granted_at DESC"
    return [_row(r) for r in get_db().query(sql, params)]


def add_charging_session(robot_id, started_at, start_battery):
    get_db().execute(
        """INSERT INTO charging_sessions(robot_id, started_at, start_battery)
           VALUES (?,?,?)""",
        (robot_id, started_at, start_battery),
    )


def end_charging_session(robot_id, end_battery):
    row = get_db().query_one(
        """SELECT id, started_at FROM charging_sessions
           WHERE robot_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1""",
        (robot_id,),
    )
    if not row:
        return
    get_db().execute(
        """UPDATE charging_sessions SET ended_at=?, end_battery=?, duration=?
           WHERE id=?""",
        (time.time(), end_battery, time.time() - row["started_at"], row["id"]),
    )


def list_charging_sessions(limit=100):
    rows = get_db().query(
        "SELECT * FROM charging_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    )
    return [_row(r) for r in rows]


# ── events & alerts ───────────────────────────────────────────


def add_event(etype, severity, robot_id, message, ts=None):
    get_db().execute(
        "INSERT INTO events(ts, type, severity, robot_id, message) VALUES (?,?,?,?,?)",
        (ts or time.time(), etype, severity or "info", robot_id or "", message),
    )


def add_events_batch(events):
    for e in events:
        add_event(
            e.get("type", "system"),
            e.get("severity", "info"),
            e.get("robot", "") or e.get("robot_id", ""),
            e.get("message", ""),
            e.get("ts"),
        )


def list_events(**filters):
    sql = "SELECT * FROM events WHERE 1=1"
    params = []
    if filters.get("type"):
        sql += " AND type=?"
        params.append(filters["type"])
    if filters.get("severity"):
        sql += " AND severity=?"
        params.append(filters["severity"])
    if filters.get("robot_id"):
        sql += " AND robot_id=?"
        params.append(filters["robot_id"])
    if filters.get("q"):
        sql += " AND (message LIKE ? OR type LIKE ? OR robot_id LIKE ?)"
        like = f"%{filters['q']}%"
        params += [like, like, like]
    if filters.get("since"):
        sql += " AND ts>=?"
        params.append(float(filters["since"]))
    limit = int(filters.get("limit", 200))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in get_db().query(sql, params)]


def add_alert(atype, severity, robot_id, title, message, ts=None):
    get_db().execute(
        """INSERT INTO alerts(type, severity, robot_id, title, message, ts)
           VALUES (?,?,?,?,?,?)""",
        (
            atype,
            severity or "warning",
            robot_id or "",
            title,
            message,
            ts or time.time(),
        ),
    )


def list_alerts(active_only=False, limit=200):
    sql = "SELECT * FROM alerts WHERE 1=1"
    params = []
    if active_only:
        sql += " AND active=1"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in get_db().query(sql, params)]


def acknowledge_alert(alert_id):
    return (
        get_db()
        .execute(
            "UPDATE alerts SET active=0, cleared_ts=? WHERE id=?",
            (time.time(), alert_id),
        )
        .rowcount
        > 0
    )


# ── analytics ─────────────────────────────────────────────────


def add_analytics_snapshot(payload):
    get_db().execute(
        "INSERT INTO analytics_snapshots(ts, payload) VALUES (?,?)",
        (time.time(), _dump(payload)),
    )


def latest_analytics():
    r = get_db().query_one("SELECT * FROM analytics_snapshots ORDER BY id DESC LIMIT 1")
    if not r:
        return None
    d = _row(r)
    try:
        d["payload"] = json.loads(d["payload"])
    except Exception:
        d["payload"] = {}
    return d


def analytics_history(limit=100):
    rows = get_db().query(
        "SELECT ts, payload FROM analytics_snapshots ORDER BY id DESC LIMIT ?", (limit,)
    )
    out = []
    for r in rows:
        d = _row(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            d["payload"] = {}
        out.append(d)
    return out


def add_analytics_metric(metric, robot_id, value, ts=None, window=""):
    get_db().execute(
        """INSERT INTO analytics_metrics(metric, robot_id, value, ts, window)
           VALUES (?,?,?,?,?)""",
        (metric, robot_id or "", float(value), ts or time.time(), window or ""),
    )


def metrics(metric=None, since=None, window=None, limit=1000):
    sql = "SELECT * FROM analytics_metrics WHERE 1=1"
    params = []
    if metric:
        sql += " AND metric=?"
        params.append(metric)
    if window:
        sql += " AND window=?"
        params.append(window)
    if since:
        sql += " AND ts>=?"
        params.append(float(since))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in get_db().query(sql, params)]


def report(window="daily", days=30):
    """Aggregate metrics into a report (hourly or daily buckets)."""
    fmt = "%Y-%m-%d %H:00" if window == "hourly" else "%Y-%m-%d"
    rows = get_db().query(
        f"""SELECT strftime('{fmt}', ts, 'unixepoch', 'localtime') AS bucket,
             metric, COUNT(*) AS samples, ROUND(AVG(value), 3) AS avg_value,
             ROUND(SUM(value), 2) AS sum_value
           FROM analytics_metrics
           WHERE ts >= ?
           GROUP BY bucket, metric ORDER BY bucket DESC, metric""",
        (time.time() - days * 86400,),
    )
    return [_row(r) for r in rows]


# ── maps ──────────────────────────────────────────────────────


def save_map(name, width, height, resolution, origin, data):
    now = time.time()
    get_db().execute(
        """INSERT INTO maps(name, width, height, resolution, origin_x,
             origin_y, data, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            name,
            width,
            height,
            resolution,
            origin[0],
            origin[1],
            sqlite3.Binary(data) if data else None,
            now,
            now,
        ),
    )


def list_maps():
    rows = get_db().query(
        "SELECT id, name, width, height, resolution, origin_x, origin_y, "
        "created_at, updated_at FROM maps ORDER BY id DESC"
    )
    return [_row(r) for r in rows]


def get_map(map_id):
    r = get_db().query_one("SELECT * FROM maps WHERE id=?", (map_id,))
    return _row(r) if r else None


# ── users & tokens ────────────────────────────────────────────


def create_user(username, role, password_hash):
    get_db().execute(
        "INSERT INTO users(username, role, password_hash, created_at) "
        "VALUES (?,?,?,?) ON CONFLICT(username) DO NOTHING",
        (username, role, password_hash, time.time()),
    )


def get_user(username):
    r = get_db().query_one("SELECT * FROM users WHERE username=?", (username,))
    return _row(r) if r else None


def list_users():
    return [
        _row(r)
        for r in get_db().query(
            "SELECT username, role, created_at, last_login, active FROM users"
        )
    ]


def touch_login(username):
    get_db().execute(
        "UPDATE users SET last_login=? WHERE username=?", (time.time(), username)
    )


def store_token(token, username, role, expires_at):
    get_db().execute(
        "INSERT INTO api_tokens(token, username, role, created_at, expires_at) "
        "VALUES (?,?,?,?,?)",
        (token, username, role, time.time(), expires_at),
    )


def revoke_token(token):
    return (
        get_db()
        .execute("UPDATE api_tokens SET revoked=1 WHERE token=?", (token,))
        .rowcount
        > 0
    )


def token_valid(token):
    r = get_db().query_one(
        "SELECT * FROM api_tokens WHERE token=? AND revoked=0", (token,)
    )
    if not r:
        return None
    if r["expires_at"] and time.time() > r["expires_at"]:
        return None
    return _row(r)


# ── maintenance ───────────────────────────────────────────────


def prune(table, column, before_ts):
    """Delete rows older than before_ts from a table by a ts column."""
    cur = get_db().execute(f"DELETE FROM {table} WHERE {column} < ?", (before_ts,))
    return cur.rowcount


def archive(table, before_ts):
    """Move rows older than before_ts into table_archive."""
    archive_table = f"{table}_archive"
    get_db().execute(
        f"CREATE TABLE IF NOT EXISTS {archive_table} AS SELECT * FROM {table} "
        f"WHERE 1=0"
    )
    get_db().execute(
        f"INSERT INTO {archive_table} SELECT * FROM {table} WHERE ts < ?", (before_ts,)
    )
    return prune(table, "ts", before_ts)


def table_sizes():
    tables = [
        "robots",
        "robot_positions",
        "battery_history",
        "tasks",
        "task_history",
        "fleet_status",
        "queue_history",
        "reservations",
        "charging_sessions",
        "events",
        "alerts",
        "analytics_snapshots",
        "analytics_metrics",
        "maps",
    ]
    out = {}
    for t in tables:
        try:
            r = get_db().query_one(f"SELECT COUNT(*) AS n FROM {t}")
            out[t] = r["n"]
        except Exception:
            out[t] = 0
    return out


# ── helpers ───────────────────────────────────────────────────


def _row(row):
    return dict(row) if row else None


def _dump(obj):
    return json.dumps(obj)
