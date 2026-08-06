"""Recovery & persistence tests: data survives a "restart" (re-open the DB)."""

import conftest
from backend.database import reset_db
from fastapi.testclient import TestClient

from backend import api
from backend import repository as repo


def test_state_restored_after_restart(tmp_path):
    db_path = str(tmp_path / "recovery.db")
    reset_db(db_path)
    repo.upsert_robot(
        {
            "robot_id": "robot1",
            "status": "ONLINE",
            "x": 1.0,
            "y": 2.0,
            "battery": 60.0,
            "current_task": "T1",
            "last_seen": 1700000000,
        }
    )
    repo.upsert_task(
        {
            "task_id": "T1",
            "status": "RUNNING",
            "robot_id": "robot1",
            "pickup_x": 0,
            "pickup_y": 0,
            "dropoff_x": 3,
            "dropoff_y": 3,
        }
    )
    repo.add_event("task_assigned", "info", "robot1", "Task T1 assigned")
    repo.add_fleet_status(
        {
            "total_robots": 1,
            "online_robots": 1,
            "offline_robots": 0,
            "idle_robots": 0,
            "busy_robots": 1,
            "charging_robots": 0,
        }
    )

    # "restart" — reopen the same file
    reset_db(db_path)

    robots = repo.list_robots()
    assert len(robots) == 1 and robots[0]["robot_id"] == "robot1"
    task = repo.get_task("T1")
    assert task and task["status"] == "RUNNING"
    events = repo.list_events()
    assert any(e["type"] == "task_assigned" for e in events)
    fleet = repo.latest_fleet_status()
    assert fleet and fleet["online"] == 1
    reset_db(":memory:")


def test_api_after_recovery(client, auth_headers):
    """Robots/tasks/events written via API survive a client restart."""
    client.post(
        "/api/robots",
        headers=auth_headers,
        json={"robot_id": "robot1", "status": "ONLINE"},
    )
    client.post(
        "/api/tasks",
        headers=auth_headers,
        json={
            "task_id": "T1",
            "pickup_x": 0,
            "pickup_y": 0,
            "dropoff_x": 1,
            "dropoff_y": 1,
        },
    )
    path = client.db_path
    # simulate backend restart: new app + same DB file
    reset_db(str(path))
    from backend.config import get_config

    get_config().reload()
    with TestClient(api.app) as c2:
        token = conftest.login_as(c2, "admin", "admin")
        h = {"Authorization": f"Bearer {token}"}
        robots = c2.get("/api/robots", headers=h).json()
        assert any(r["robot_id"] == "robot1" for r in robots)
        tasks = c2.get("/api/tasks", headers=h).json()
        assert any(t["task_id"] == "T1" for t in tasks)
    reset_db(":memory:")
