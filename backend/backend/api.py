"""Warehouse Backend — FastAPI application.

Exposes the production REST API, WebSocket stream, OpenAPI docs and health
endpoints. This module contains no ROS imports: ROS is kept isolated in the
ingest bridge (backend_ingest_node) which feeds /api/ingest.
"""

import json
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from . import auth as auth_mod
from . import repository as repo
from . import ws_manager
from .auth import get_current_user, require_role, seed_default_user
from .config import get_config, reload_config
from .ingest import process_batch
from .logging_config import get_logger, setup_logging
from .monitoring import component_status, record_request, ring, sample, uptime
from .rate_limit import apply_rate_limit
from .schemas import (
    CreateUserRequest,
    IngestBatch,
    LoginRequest,
    MapIn,
    RobotIn,
    RobotPatch,
    TaskIn,
    TaskPatch,
)
from .version import __version__

log = get_logger("warehouse.api")


@asynccontextmanager
async def _lifespan(app):
    setup_logging(
        level=cfg.get("logging.level", "INFO"),
        log_file=cfg.get("logging.file"),
        max_bytes=cfg.get("logging.max_bytes", 5 * 1024 * 1024),
        backup_count=cfg.get("logging.backup_count", 3),
    )
    from . import services

    seed_default_user()
    services.start_all()
    log.info(
        "warehouse backend started",
        extra={"version": __version__, "db": cfg.get("database.path")},
    )
    yield
    services.stop_all()
    log.info("warehouse backend stopped")


app = FastAPI(
    title="Warehouse Digital Twin — Backend API",
    version=__version__,
    lifespan=_lifespan,
    description=(
        "Production backend for the Warehouse Digital Twin. Persistent "
        "SQLite storage, REST APIs, WebSocket streaming, authentication, "
        "events, alerts, analytics, health and monitoring. Data is fed by "
        "the ROS ingest bridge and is available to the dashboard and "
        "third-party integrations."
    ),
    openapi_tags=[
        {"name": "auth", "description": "Login, logout, tokens, users"},
        {"name": "robots", "description": "Robot registry & state"},
        {"name": "tasks", "description": "Task management"},
        {"name": "fleet", "description": "Fleet summaries & history"},
        {"name": "analytics", "description": "Analytics & reports"},
        {"name": "reservations", "description": "Traffic reservations"},
        {"name": "events", "description": "Event log"},
        {"name": "alerts", "description": "Alerts & acknowledgements"},
        {"name": "maps", "description": "Map storage"},
        {"name": "health", "description": "Health / readiness / liveness"},
        {"name": "system", "description": "Version, config, monitoring, logs"},
    ],
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ── middleware ────────────────────────────────────────────────

cfg = get_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.get("security.cors_origins", ["*"]),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _request_middleware(request: Request, call_next):
    start = time.time()
    request_id = uuid.uuid4().hex[:12]
    record_request(request.url.path)
    try:
        response = await call_next(request)
    except HTTPException:
        raise
    except Exception:
        log.error(
            "unhandled error",
            extra={"path": request.url.path, "request_id": request_id},
        )
        raise
    elapsed = (time.time() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = f"{elapsed:.1f}"
    if cfg.get("security.secure_headers", True):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ── helpers ───────────────────────────────────────────────────


def _auth_enabled():
    return cfg.get("auth.enabled", True)


# ── auth API ──────────────────────────────────────────────────


@app.post("/api/auth/login", tags=["auth"], dependencies=[Depends(apply_rate_limit)])
def login(body: LoginRequest):
    if not _auth_enabled():
        raise HTTPException(status_code=503, detail="Auth disabled")
    user = repo.get_user(body.username)
    if not user or not auth_mod.verify_password(body.password, user["password_hash"]):
        log.warning("login failed", extra={"username": body.username})
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user["active"]:
        raise HTTPException(status_code=403, detail="User disabled")
    repo.touch_login(body.username)
    token, expires = auth_mod.issue_token(user["username"], user["role"])
    log.info("login success", extra={"username": body.username})
    return {
        "token": token,
        "expires_at": expires,
        "user": {"username": user["username"], "role": user["role"]},
        "roles": list(auth_mod.ROLE_LEVEL),
    }


@app.post("/api/auth/logout", tags=["auth"])
def logout(request: Request, claims=Depends(get_current_user)):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if token:
        repo.revoke_token(token)
    return {"ok": True, "message": "Logged out"}


@app.get("/api/auth/me", tags=["auth"])
def me(claims=Depends(get_current_user)):
    user = repo.get_user(claims.get("sub"))
    return {
        "username": claims.get("sub"),
        "role": claims.get("role"),
        "readonly": claims.get("readonly", False),
        "user": user,
    }


@app.get("/api/auth/roles", tags=["auth"])
def roles():
    return dict(auth_mod.ROLE_LEVEL)


@app.post("/api/auth/users", tags=["auth"], status_code=201)
def create_user(body: CreateUserRequest, claims=Depends(require_role("administrator"))):
    if repo.get_user(body.username):
        raise HTTPException(status_code=409, detail="User exists")
    repo.create_user(body.username, body.role, auth_mod.hash_password(body.password))
    log.info("user created", extra={"username": body.username, "role": body.role})
    return {"ok": True, "username": body.username, "role": body.role}


@app.get("/api/auth/users", tags=["auth"])
def list_users(claims=Depends(require_role("administrator"))):
    return repo.list_users()


# ── robots API ────────────────────────────────────────────────


@app.get("/api/robots", tags=["robots"])
def robots_list(
    status: str | None = None,
    charging: bool | None = None,
    claims: dict = Depends(get_current_user),
):
    return repo.list_robots(status=status, charging=charging)


@app.get("/api/robots/{robot_id}", tags=["robots"])
def robot_get(robot_id: str, claims: dict = Depends(get_current_user)):
    r = repo.get_robot(robot_id)
    if not r:
        raise HTTPException(status_code=404, detail="Robot not found")
    r["position_history"] = repo.position_history(robot_id, limit=100)
    r["battery_history"] = repo.battery_history(robot_id, limit=100)
    return r


@app.post("/api/robots", tags=["robots"], status_code=201)
def robot_create(body: RobotIn, claims=Depends(require_role("operator"))):
    repo.upsert_robot({**body.model_dump(), "last_seen": time.time()})
    repo.add_event(
        "robot_registered", "info", body.robot_id, f"Robot {body.robot_id} registered"
    )
    return repo.get_robot(body.robot_id)


@app.patch("/api/robots/{robot_id}", tags=["robots"])
def robot_patch(
    robot_id: str, body: RobotPatch, claims=Depends(require_role("operator"))
):
    if not repo.get_robot(robot_id):
        raise HTTPException(status_code=404, detail="Robot not found")
    repo.update_robot(robot_id, **body.model_dump(exclude_none=True))
    return repo.get_robot(robot_id)


@app.delete("/api/robots/{robot_id}", tags=["robots"])
def robot_delete(robot_id: str, claims=Depends(require_role("administrator"))):
    if not repo.delete_robot(robot_id):
        raise HTTPException(status_code=404, detail="Robot not found")
    repo.add_event("robot_deleted", "info", robot_id, f"Robot {robot_id} removed")
    return {"ok": True}


# ── tasks API ─────────────────────────────────────────────────


@app.get("/api/tasks", tags=["tasks"])
def tasks_list(
    status: str | None = None,
    priority: int | None = None,
    robot_id: str | None = None,
    claims: dict = Depends(get_current_user),
):
    return repo.list_tasks(status=status, priority=priority, robot_id=robot_id)


@app.get("/api/tasks/{task_id}", tags=["tasks"])
def task_get(task_id: str, claims: dict = Depends(get_current_user)):
    t = repo.get_task(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@app.post("/api/tasks", tags=["tasks"], status_code=201)
def task_create(body: TaskIn, claims=Depends(require_role("operator"))):
    if repo.get_task(body.task_id):
        raise HTTPException(status_code=409, detail="Task exists")
    repo.upsert_task({**body.model_dump(), "status": "PENDING"})
    repo.add_event("task_created", "info", "", f"Task {body.task_id} created")
    repo.add_task_history(body.task_id, body.robot_id or "", "PENDING", time.time())
    return repo.get_task(body.task_id)


@app.patch("/api/tasks/{task_id}", tags=["tasks"])
def task_patch(task_id: str, body: TaskPatch, claims=Depends(require_role("operator"))):
    if not repo.get_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    fields = body.model_dump(exclude_none=True)
    prev = repo.get_task(task_id)
    if fields.get("status") in ("COMPLETED",):
        fields["completed_at"] = time.time()
        repo.add_task_history(
            task_id,
            (prev or {}).get("robot_id") or "",
            "COMPLETED",
            time.time(),
            time.time(),
        )
        repo.add_event(
            "task_completed",
            "info",
            (prev or {}).get("robot_id") or "",
            f"Task {task_id} completed",
        )
    if fields.get("status") in ("CANCELLED",):
        fields["cancelled_at"] = time.time()
        repo.add_event(
            "task_cancelled",
            "warning",
            (prev or {}).get("robot_id") or "",
            f"Task {task_id} cancelled",
        )
    if fields.get("status") == "ASSIGNED" and (prev or {}).get("status") != "ASSIGNED":
        repo.add_task_history(
            task_id, fields.get("robot_id") or "", "ASSIGNED", time.time()
        )
        repo.add_event(
            "task_assigned",
            "info",
            fields.get("robot_id") or "",
            f'Task {task_id} assigned to {fields.get("robot_id") or ""}',
        )
    repo.update_task(task_id, **fields)
    return repo.get_task(task_id)


@app.delete("/api/tasks/{task_id}", tags=["tasks"])
def task_delete(task_id: str, claims=Depends(require_role("operator"))):
    if not repo.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    repo.add_event("task_deleted", "warning", "", f"Task {task_id} deleted")
    return {"ok": True}


# ── fleet API ─────────────────────────────────────────────────


@app.get("/api/fleet", tags=["fleet"])
def fleet_summary(claims: dict = Depends(get_current_user)):
    latest = repo.latest_fleet_status()
    return {
        "robots": repo.list_robots(),
        "latest_status": latest,
        "history": repo.fleet_history(limit=50),
        "reservation_queue": repo.queue_history(limit=50),
        "charging_sessions": repo.list_charging_sessions(limit=50),
    }


# ── analytics API ─────────────────────────────────────────────


@app.get("/api/analytics", tags=["analytics"])
def analytics_latest(claims: dict = Depends(get_current_user)):
    latest = repo.latest_analytics()
    if not latest:
        return {}
    return latest["payload"]


@app.get("/api/analytics/history", tags=["analytics"])
def analytics_history(
    metric: str | None = None,
    limit: int = 100,
    claims: dict = Depends(get_current_user),
):
    if metric:
        return repo.metrics(metric=metric, limit=limit)
    return repo.analytics_history(limit=limit)


@app.get("/api/analytics/reports", tags=["analytics"])
def analytics_reports(
    window: str = "daily", days: int = 30, claims: dict = Depends(get_current_user)
):
    if window not in ("hourly", "daily"):
        raise HTTPException(status_code=400, detail="window must be hourly|daily")
    return repo.report(window=window, days=days)


# ── reservations API ──────────────────────────────────────────


@app.get("/api/reservations", tags=["reservations"])
def reservations_list(
    status: str | None = None, claims: dict = Depends(get_current_user)
):
    return {
        "reservations": repo.list_reservations(status=status),
        "queue": repo.queue_history(limit=100),
    }


# ── events API ────────────────────────────────────────────────


@app.get("/api/events", tags=["events"])
def events_list(
    type: str | None = None,
    severity: str | None = None,
    robot_id: str | None = None,
    q: str | None = None,
    since: float | None = None,
    limit: int = 200,
    claims: dict = Depends(get_current_user),
):
    return repo.list_events(
        type=type, severity=severity, robot_id=robot_id, q=q, since=since, limit=limit
    )


@app.get("/api/events/export", tags=["events"])
def events_export(
    fmt: str = "json",
    type: str | None = None,
    severity: str | None = None,
    q: str | None = None,
    since: float | None = None,
    limit: int = 5000,
    claims: dict = Depends(get_current_user),
):
    rows = repo.list_events(type=type, severity=severity, q=q, since=since, limit=limit)
    if fmt == "csv":
        lines = ["timestamp,severity,type,robot,message"]
        for e in rows:
            msg = e["message"].replace(",", " ").replace('"', "'")
            lines.append(
                f"{e['ts']},{e['severity']},{e['type']},"
                f"{e['robot_id'] or ''},\"{msg}\""
            )
        return Response(
            "\n".join(lines),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="warehouse_events.csv"'
            },
        )
    return rows


# ── alerts API ────────────────────────────────────────────────


@app.get("/api/alerts", tags=["alerts"])
def alerts_list(active_only: bool = False, claims: dict = Depends(get_current_user)):
    return {
        "active": repo.list_alerts(active_only=True),
        "history": repo.list_alerts(active_only=False, limit=200),
    }


@app.post("/api/alerts/{alert_id}/ack", tags=["alerts"])
def alerts_ack(alert_id: int, claims=Depends(require_role("operator"))):
    if not repo.acknowledge_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"ok": True}


# ── maps API ──────────────────────────────────────────────────


@app.get("/api/maps", tags=["maps"])
def maps_list(claims: dict = Depends(get_current_user)):
    return repo.list_maps()


@app.post("/api/maps", tags=["maps"], status_code=201)
def maps_create(body: MapIn, claims=Depends(require_role("supervisor"))):
    data = None
    if body.data is not None:
        import struct

        data = struct.pack(f"<{len(body.data)}b", *body.data)
    repo.save_map(
        body.name,
        body.width,
        body.height,
        body.resolution,
        (body.origin_x, body.origin_y),
        data,
    )
    return {"ok": True, "name": body.name}


@app.get("/api/maps/{map_id}", tags=["maps"])
def maps_get(map_id: int, claims: dict = Depends(get_current_user)):
    m = repo.get_map(map_id)
    if not m:
        raise HTTPException(status_code=404, detail="Map not found")
    if m.get("data"):
        import struct

        m["data"] = list(struct.unpack(f'<{len(m["data"])}b', m["data"]))
    return m


# ── health / readiness / liveness ─────────────────────────────


@app.get("/api/health", tags=["health"])
def health():
    """Liveness: the process is up."""
    return {
        "status": "ok",
        "service": "warehouse-backend",
        "version": __version__,
        "ts": time.time(),
        "uptime_s": uptime(),
    }


@app.get("/api/live", tags=["health"])
def live():
    return health()


@app.get("/api/ready", tags=["health"])
def ready():
    """Readiness: DB reachable and ROS bridge has fed data recently."""
    from .monitoring import component_status

    comp = component_status()
    db_ok = comp["db"]["ok"]
    ready_flag = db_ok
    return {
        "status": "ready" if ready_flag else "not_ready",
        "db": db_ok,
        "ros_bridge": comp["ros_bridge"],
        "components": comp,
    }


@app.get("/api/health/components", tags=["health"])
def health_components():
    return component_status()


# ── version ───────────────────────────────────────────────────


@app.get("/api/version", tags=["system"])
def version():
    return {
        "service": "warehouse-backend",
        "version": __version__,
        "api": "v1",
        "schema_version": 1,
    }


# ── config ────────────────────────────────────────────────────


@app.get("/api/config", tags=["system"])
def config_get(claims=Depends(require_role("operator"))):
    return cfg.as_dict()


@app.post("/api/config/reload", tags=["system"])
def config_reload(claims=Depends(require_role("supervisor"))):
    cfg.reload()
    return {"ok": True, "reloaded": True}


# ── monitoring ────────────────────────────────────────────────


@app.get("/api/monitoring", tags=["system"])
def monitoring_live():
    return {"live": sample(), "series": ring()[-60:]}


@app.get("/api/monitoring/series", tags=["system"])
def monitoring_series():
    return ring()


@app.get("/api/monitoring/components", tags=["system"])
def monitoring_components(claims=Depends(require_role("operator"))):
    return component_status()


# ── services ──────────────────────────────────────────────────


@app.get("/api/services", tags=["system"])
def services_status(claims=Depends(require_role("operator"))):
    from . import services

    return services.service_status()


@app.post("/api/services/{name}/pause", tags=["system"])
def services_pause(name: str, claims=Depends(require_role("supervisor"))):
    from . import services

    services.pause(name)
    return {"ok": True, "service": name, "paused": True}


@app.post("/api/services/{name}/resume", tags=["system"])
def services_resume(name: str, claims=Depends(require_role("supervisor"))):
    from . import services

    services.resume(name)
    return {"ok": True, "service": name, "paused": False}


# ── logs ──────────────────────────────────────────────────────


@app.get("/api/logs", tags=["system"])
def logs_tail(lines: int = 100, level: str | None = None):
    path = cfg.get("logging.file")
    if not path or not os.path.isfile(path):
        return {"logs": [], "error": "no log file configured"}
    entries = []
    with open(path, "r") as fh:
        tail = fh.readlines()[-int(lines) :]
    for ln in tail:
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except Exception:
            e = {"message": ln, "level": "INFO"}
        if level and e.get("level") != level.upper():
            continue
        entries.append(e)
    return {"logs": entries}


# ── ingest (ROS bridge) ───────────────────────────────────────


@app.post("/api/ingest", tags=["system"])
def ingest(
    batch: IngestBatch, request: Request, claims: dict = Depends(get_current_user)
):
    """Receive a state/event batch from the ROS ingest bridge."""
    counts = process_batch(batch.model_dump(exclude_none=True))
    return {"ok": True, "stored": counts}


@app.post("/api/ingest/events", tags=["system"])
def ingest_events(
    batch: list[dict], request: Request, claims: dict = Depends(get_current_user)
):
    repo.add_events_batch(batch)
    ws_manager.broadcast({"type": "events", "data": batch[-20:]})
    return {"ok": True, "stored": len(batch)}


# ── WebSocket ─────────────────────────────────────────────────


@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    token = ws.query_params.get("token")
    auth_header = ws.headers.get("Authorization", "")
    if not token and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    await ws_manager.handle_client(ws, token=token or None)
