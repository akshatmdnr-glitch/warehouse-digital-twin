"""Authentication & authorization tests."""

import conftest
from fastapi.testclient import TestClient

from backend import api


def test_login_success(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert body["user"]["role"] == "administrator"
    assert "operator" in body["roles"]


def test_login_bad_credentials(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_validation(client):
    r = client.post("/api/auth/login", json={"username": "x", "password": ""})
    assert r.status_code == 422  # pydantic validation


def test_token_required(client):
    assert client.get("/api/robots").status_code == 401


def test_invalid_token(client):
    r = client.get("/api/robots", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_roles_and_permissions(client, auth_headers):
    # admin can create users
    r = client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={"username": "op1", "password": "pass1234", "role": "operator"},
    )
    assert r.status_code == 201
    op = conftest.login_as(client, "op1", "pass1234")
    op_h = {"Authorization": f"Bearer {op}"}
    # operator reads OK
    assert client.get("/api/robots", headers=op_h).status_code == 200
    # operator cannot create users (needs administrator)
    r = client.post(
        "/api/auth/users",
        headers=op_h,
        json={"username": "x", "password": "pass1234", "role": "operator"},
    )
    assert r.status_code == 403


def test_logout_revokes_token(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_users_list(client, auth_headers):
    r = client.get("/api/auth/users", headers=auth_headers)
    assert r.status_code == 200
    assert any(u["username"] == "admin" for u in r.json())


def test_rate_limit(client):
    from backend.config import get_config

    cfg = get_config()
    old = cfg.get("security.rate_limit_per_minute")
    cfg.get  # noqa
    from backend.config import get_config as gc2

    gc2()._config["security"]["rate_limit_per_minute"] = 3
    statuses = []
    for _ in range(6):
        r = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        statuses.append(r.status_code)
    assert 429 in statuses, statuses
    gc2()._config["security"]["rate_limit_per_minute"] = old or 120
