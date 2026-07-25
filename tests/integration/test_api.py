from uuid import uuid4

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


def test_existing_request_id_cannot_mix_or_overwrite_runs():
    request_id = f"collision-{uuid4().hex[:12]}"
    body = {
        "pid_a": "PID-SYN-A",
        "pid_b": "PID-SYN-B",
        "mismatch_mode": "warn",
        "request_id": request_id,
    }
    first = client.post("/api/run-pair", json=body)
    assert first.status_code == 200, first.text
    second = client.post("/api/run-pair", json=body)
    assert second.status_code == 400
    assert "already exists" in second.text


def test_chunked_body_limit_is_enforced_without_content_length():
    chunks = iter([b'{"pid_a":"', b"A" * 1_000_100, b'"}'])
    response = client.post(
        "/api/run-pair",
        content=chunks,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
