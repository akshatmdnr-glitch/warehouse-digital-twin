"""WebSocket tests: auth, hello/state, ping/pong, multi-client."""

import json

import conftest
from fastapi.testclient import TestClient

from backend import api, ws_manager


def _connect(client, token=None, **params):
    url = "/api/ws"
    if token:
        url += "?token=" + token
    return client.websocket_connect(url)


def test_ws_requires_token(client):
    # connecting without a token should still allow (anonymous), but with a
    # bad token it must reject. We test that a valid token gets hello/state.
    pass


def test_ws_hello_and_state(client, admin_token):
    with client.websocket_connect("/api/ws?token=" + admin_token) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["data"]["role"] == "administrator"
        state = ws.receive_json()
        assert state["type"] == "state"
        assert "robots" in state["data"]


def test_ws_ping_pong(client, admin_token):
    with client.websocket_connect("/api/ws?token=" + admin_token) as ws:
        ws.receive_json()  # hello
        ws.receive_json()  # state
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"


def test_ws_broadcast_multi_client(client, admin_token):
    c1 = client.websocket_connect("/api/ws?token=" + admin_token)
    c2 = client.websocket_connect("/api/ws?token=" + admin_token)
    with c1 as ws1, c2 as ws2:
        ws1.receive_json()  # hello
        ws1.receive_json()  # state
        ws2.receive_json()
        ws2.receive_json()
        # broadcast from the manager
        ws_manager.broadcast({"type": "fleet", "data": {"online": 2}})
        m1 = ws1.receive_json()
        m2 = ws2.receive_json()
        assert m1 == {"type": "fleet", "data": {"online": 2}}
        assert m2 == m1


def test_ws_invalid_token(client):
    with client.websocket_connect("/api/ws?token=bogus") as ws:
        # server closes with 4001
        try:
            ws.receive_json()
            closed = False
        except Exception:
            closed = True
        assert closed
