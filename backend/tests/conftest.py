"""Shared pytest fixtures: isolated DB, FastAPI test client, auth tokens."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["BACKEND_AUTH_SECRET"] = "test-secret"
os.environ["BACKEND_DB_PATH"] = ":memory:"
os.environ["BACKEND_LOG_FILE"] = ""
os.environ["BACKEND_SERVICES_DISABLED"] = "1"

import pytest  # noqa: E402
from backend.database import reset_db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import api  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "warehouse_test.db"
    reset_db(str(db_path))
    api.app.router = api.app.router  # keep reference
    # refresh config env overrides
    from backend.config import get_config

    get_config().reload()
    with TestClient(api.app) as c:
        c.db_path = db_path
        yield c
    reset_db(":memory:")


@pytest.fixture()
def admin_token(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture()
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def login_as(client, username, password):
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    return r.json()["token"] if r.status_code == 200 else None
