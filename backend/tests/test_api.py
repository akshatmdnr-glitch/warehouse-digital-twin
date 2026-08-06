"""REST API tests: robots, tasks, fleet, analytics, reservations,
events, alerts, maps, health, version."""


def _seed(client, headers):
    client.post(
        "/api/robots",
        headers=headers,
        json={
            "robot_id": "robot1",
            "namespace": "robot1",
            "robot_type": "burger",
            "status": "ONLINE",
            "x": 1.0,
            "y": 2.0,
            "yaw": 0.5,
            "battery": 80.0,
            "current_task": "",
        },
    )
    client.post(
        "/api/tasks",
        headers=headers,
        json={
            "task_id": "T1",
            "pickup_x": 0,
            "pickup_y": 0,
            "dropoff_x": 3,
            "dropoff_y": 3,
            "priority": 1,
        },
    )


def test_robots_crud(client, auth_headers):
    _seed(client, auth_headers)
    r = client.get("/api/robots", headers=auth_headers)
    assert r.status_code == 200
    assert any(x["robot_id"] == "robot1" for x in r.json())

    r = client.get("/api/robots/robot1", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["battery"] == 80.0

    r = client.patch(
        "/api/robots/robot1",
        headers=auth_headers,
        json={"status": "OFFLINE", "battery": 20.0},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "OFFLINE"

    r = client.delete("/api/robots/robot1", headers=auth_headers)
    assert r.status_code == 200
    assert client.get("/api/robots/robot1", headers=auth_headers).status_code == 404


def test_robots_validation(client, auth_headers):
    r = client.post(
        "/api/robots", headers=auth_headers, json={"robot_id": "", "battery": 200}
    )
    assert r.status_code == 422


def test_tasks_crud(client, auth_headers):
    _seed(client, auth_headers)
    r = client.get("/api/tasks", headers=auth_headers)
    assert any(t["task_id"] == "T1" for t in r.json())

    r = client.patch(
        "/api/tasks/T1",
        headers=auth_headers,
        json={"priority": 2, "robot_id": "robot1"},
    )
    assert r.status_code == 200
    assert r.json()["priority"] == 2

    r = client.patch(
        "/api/tasks/T1", headers=auth_headers, json={"status": "COMPLETED"}
    )
    assert r.status_code == 200
    assert r.json()["completed_at"] is not None

    r = client.delete("/api/tasks/T1", headers=auth_headers)
    assert r.status_code == 200
    assert client.get("/api/tasks/T1", headers=auth_headers).status_code == 404


def test_fleet(client, auth_headers):
    _seed(client, auth_headers)
    r = client.get("/api/fleet", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["robots"]) >= 1


def test_analytics_and_reports(client, auth_headers):
    from backend import repository as repo

    repo.add_analytics_metric("avg_task_duration", "", 4.5)
    repo.add_analytics_metric("avg_queue_wait", "", 0.8)
    r = client.get(
        "/api/analytics/history?metric=avg_task_duration", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() and r.json()[0]["metric"] == "avg_task_duration"
    r = client.get("/api/analytics/reports?window=daily", headers=auth_headers)
    assert r.status_code == 200
    r = client.get("/api/analytics/reports?window=weekly", headers=auth_headers)
    assert r.status_code == 400


def test_events_filter_search_export(client, auth_headers):
    from backend import repository as repo

    repo.add_event("task_assigned", "info", "robot1", "Task T1 assigned to robot1")
    repo.add_event("robot_offline", "high", "robot1", "Robot robot1 offline")
    repo.add_event("charging_started", "info", "robot2", "Robot robot2 charging")
    r = client.get("/api/events?type=task_assigned", headers=auth_headers)
    assert len(r.json()) == 1 and r.json()[0]["type"] == "task_assigned"
    r = client.get("/api/events?severity=high", headers=auth_headers)
    assert all(e["severity"] == "high" for e in r.json())
    r = client.get("/api/events?q=charging", headers=auth_headers)
    assert all("charging" in e["message"] for e in r.json())
    r = client.get("/api/events/export?fmt=csv", headers=auth_headers)
    assert r.headers["content-type"].startswith("text/csv")
    assert "timestamp,severity" in r.text


def test_alerts_ack(client, auth_headers):
    from backend import repository as repo

    repo.add_alert(
        "battery_low", "warning", "robot1", "Battery low", "Robot robot1 battery 10%"
    )
    r = client.get("/api/alerts", headers=auth_headers)
    assert len(r.json()["active"]) == 1
    alert_id = r.json()["active"][0]["id"]
    r = client.post(f"/api/alerts/{alert_id}/ack", headers=auth_headers)
    assert r.status_code == 200
    r = client.get("/api/alerts", headers=auth_headers)
    assert len(r.json()["active"]) == 0


def test_maps(client, auth_headers):
    r = client.post(
        "/api/maps",
        headers=auth_headers,
        json={
            "name": "warehouse",
            "width": 10,
            "height": 10,
            "resolution": 0.05,
            "origin_x": -0.03,
            "origin_y": 0.0,
            "data": [0, 100, -1] * 33,
        },
    )
    assert r.status_code == 201
    r = client.get("/api/maps", headers=auth_headers)
    assert len(r.json()) == 1
    r = client.get(f"/api/maps/{r.json()[0]['id']}", headers=auth_headers)
    assert r.json()["width"] == 10


def test_health_endpoints(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/live").status_code == 200
    r = client.get("/api/ready")
    assert r.status_code == 200
    assert r.json()["status"] in ("ready", "not_ready")
    r = client.get("/api/health/components")
    assert r.status_code == 200
    assert "db" in r.json() and "ros_bridge" in r.json()


def test_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert r.json()["service"] == "warehouse-backend"
    assert r.json()["version"]


def test_config_and_services(client, auth_headers):
    r = client.get("/api/config", headers=auth_headers)
    assert r.status_code == 200
    assert "database" in r.json()
    r = client.post("/api/config/reload", headers=auth_headers)
    assert r.status_code == 200
    r = client.get("/api/services", headers=auth_headers)
    assert r.status_code == 200


def test_monitoring(client, auth_headers):
    r = client.get("/api/monitoring")
    assert r.status_code == 200
    assert "cpu_percent" in r.json()["live"]
    r = client.get("/api/monitoring/components", headers=auth_headers)
    assert r.status_code == 200
