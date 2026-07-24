from fastapi.testclient import TestClient

from delta_chat.api import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_pids():
    r = client.get("/api/pids")
    assert r.status_code == 200
    assert "pids" in r.json()


def test_bad_request_id_rejected():
    r = client.post(
        "/api/run-pair",
        json={"pid_a": "PID-SYN-A", "pid_b": "PID-SYN-B", "request_id": "../escape"},
    )
    assert r.status_code == 422 or r.status_code == 400


def test_run_pair_no_absolute_paths():
    r = client.post(
        "/api/run-pair",
        json={"pid_a": "PID-SYN-A", "pid_b": "PID-SYN-B", "mismatch_mode": "warn"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_dir" not in body
    assert body["paths"]["delta_json"].startswith("/api/runs/")
    rid = body["request_id"]
    g = client.get(f"/api/runs/{rid}")
    assert g.status_code == 200
    assert "run_dir" not in g.json()
