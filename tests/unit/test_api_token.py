from fastapi.testclient import TestClient

from pulse.api import app


def test_token_gate(monkeypatch):
    monkeypatch.setenv("PULSE_API_TOKEN", "s3cret")
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200  # healthcheck stays open
    assert c.get("/api/weeks").status_code == 401
    assert c.get("/api/weeks", headers={"X-Pulse-Token": "wrong"}).status_code == 401
    assert c.get("/api/weeks", headers={"X-Pulse-Token": "s3cret"}).status_code == 200


def test_no_token_configured_is_open(monkeypatch):
    monkeypatch.delenv("PULSE_API_TOKEN", raising=False)
    assert TestClient(app).get("/api/weeks").status_code == 200
