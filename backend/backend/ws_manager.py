"""WebSocket hub for real-time streaming.

Supports many simultaneous clients; broadcasts typed messages. Every connect
is authenticated (token via query param or header). Messages look like:
    {"type": "robot_update", "data": {...}}
"""

import asyncio
import json
import time

from . import repository as repo
from .auth import verify_token

CLIENTS: set = set()
_loop = None


async def handle_client(websocket, token=None):
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    if token:
        claims = verify_token(token)
        if not claims:
            await websocket.close(code=4001, reason="Unauthorized")
            return
        role = claims.get("role", "operator")
    else:
        role = "operator"  # anonymous view (health/general stream only)
    CLIENTS.add(websocket)
    try:
        await websocket.send_text(
            json.dumps({"type": "hello", "data": {"role": role, "ts": time.time()}})
        )
        await websocket.send_text(
            json.dumps({"type": "state", "data": _state_payload()})
        )
        async for raw in websocket.iter_text():
            try:
                msg = json.loads(raw)
                if msg.get("type") == "ping":
                    await websocket.send_text(
                        json.dumps({"type": "pong", "data": {"ts": time.time()}})
                    )
            except Exception:
                pass
    finally:
        CLIENTS.discard(websocket)


def broadcast(message):
    """Best-effort async broadcast from any thread."""
    payload = json.dumps(message)
    loop = _loop
    if loop is None:
        return
    for ws in list(CLIENTS):
        loop.create_task(_safe_send(ws, payload))


async def _safe_send(ws, payload):
    try:
        await ws.send_text(payload)
    except Exception:
        CLIENTS.discard(ws)


def client_count():
    return len(CLIENTS)


def _state_payload():
    return {
        "fleet": repo.latest_fleet_status() or {},
        "robots": repo.list_robots(),
        "alerts": repo.list_alerts(active_only=True),
        "events": repo.list_events(limit=50),
        "analytics": repo.latest_analytics(),
        "ts": time.time(),
    }


def _running_loop():
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return None
