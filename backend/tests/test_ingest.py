"""Ingest bridge tests: batches persist robots/tasks/events/analytics/history."""


def test_ingest_batch(client, auth_headers):
    r = client.post(
        "/api/ingest",
        headers=auth_headers,
        json={
            "ts": 1700000000.0,
            "robots": [
                {
                    "robot_id": "robot1",
                    "status": "ONLINE",
                    "x": 1.0,
                    "y": 2.0,
                    "yaw": 0.5,
                    "battery": 80.0,
                    "charging": False,
                    "current_task": "T1",
                    "namespace": "robot1",
                },
                {
                    "robot_id": "robot2",
                    "status": "ONLINE",
                    "x": -1.0,
                    "y": -1.0,
                    "yaw": 1.57,
                    "battery": 90.0,
                    "charging": False,
                    "namespace": "robot2",
                },
            ],
            "tasks": [
                {
                    "task_id": "T1",
                    "status": "RUNNING",
                    "robot_id": "robot1",
                    "pickup": [0, 0],
                    "dropoff": [3, 3],
                    "priority": 1,
                },
            ],
            "fleet": {
                "total_robots": 2,
                "online_robots": 2,
                "idle_robots": 0,
                "busy_robots": 2,
                "offline_robots": 0,
                "charging_robots": 0,
            },
            "events": [
                {
                    "type": "task_assigned",
                    "severity": "info",
                    "robot": "robot1",
                    "message": "Task T1 assigned to robot1",
                },
            ],
            "alerts": [
                {
                    "type": "battery_low",
                    "severity": "warning",
                    "robot": "robot2",
                    "title": "Battery low",
                    "message": "Robot robot2 battery 10%",
                    "active": True,
                },
            ],
            "analytics": {
                "fleet": {
                    "avg_task_duration": 4.5,
                    "avg_queue_wait": 0.8,
                    "total_completed_tasks": 1,
                }
            },
            "reservations": [
                {
                    "robot_id": "robot1",
                    "task_id": "T1",
                    "status": "ACTIVE",
                    "segments": 4,
                    "head_on": False,
                },
            ],
            "queue": [{"robot_id": "robot2", "task_id": "T2", "event": "queued"}],
        },
    )
    assert r.status_code == 200
    stored = r.json()["stored"]
    assert stored["robots"] == 2
    assert stored["tasks"] == 1
    assert stored["positions"] == 2
    assert stored["batteries"] == 2
    assert stored["events"] >= 1  # explicit + derived (reservation_granted)
    assert stored["fleet"] == 1

    # verify persisted
    robots = client.get("/api/robots", headers=auth_headers).json()
    assert len(robots) == 2
    r1 = client.get("/api/robots/robot1", headers=auth_headers).json()
    assert len(r1["position_history"]) == 1
    assert len(r1["battery_history"]) == 1

    tasks = client.get("/api/tasks", headers=auth_headers).json()
    assert any(t["task_id"] == "T1" and t["status"] == "RUNNING" for t in tasks)

    events = client.get("/api/events", headers=auth_headers).json()
    assert any(e["type"] == "task_assigned" for e in events)

    alerts = client.get("/api/alerts", headers=auth_headers).json()
    assert len(alerts["active"]) == 1

    analytics = client.get("/api/analytics", headers=auth_headers).json()
    assert analytics["fleet"]["avg_task_duration"] == 4.5

    reservations = client.get("/api/reservations", headers=auth_headers).json()
    assert len(reservations["reservations"]) == 1

    fleet = client.get("/api/fleet", headers=auth_headers).json()
    assert fleet["latest_status"]["online"] == 2


def test_ingest_events_endpoint(client, auth_headers):
    r = client.post(
        "/api/ingest/events",
        headers=auth_headers,
        json=[
            {
                "type": "robot_online",
                "severity": "info",
                "robot": "robot1",
                "message": "Robot robot1 online",
                "ts": 1700000001.0,
            },
        ],
    )
    assert r.status_code == 200
    assert r.json()["stored"] == 1


def test_ingest_requires_auth(client):
    r = client.post("/api/ingest", json={"robots": []})
    assert r.status_code == 401
