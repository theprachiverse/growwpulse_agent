"""/api/status: the live header state (job, last finished run, LLM budget)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pulse.api import app
from pulse.models import RunManifest
from pulse.run_store import RunStore


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PULSE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PULSE_API_TOKEN", raising=False)
    monkeypatch.delenv("PULSE_READ_ONLY", raising=False)
    return tmp_path


def save(store: RunStore, week: str, finished: datetime, status: str = "success") -> None:
    store.save_manifest(
        RunManifest(
            run_id=f"run_{week}",
            week=week,
            started_at=finished,
            finished_at=finished,
            status=status,  # type: ignore[arg-type]
        )
    )


def test_no_runs_yet(data_dir: Path) -> None:
    body = TestClient(app).get("/api/status").json()
    assert body["last_run"] is None
    assert body["job"]["state"] in {"idle", "done", "failed", "running"}
    assert body["budget"]["runs_used"] == 0
    assert body["budget"]["runs_max"] == 3


def test_last_run_is_the_most_recently_finished_not_the_latest_week(data_dir: Path) -> None:
    store = RunStore(base_dir=data_dir)
    save(store, "2026-W42", datetime(2026, 10, 19, 17, 33, tzinfo=UTC))
    save(store, "2026-W41", datetime(2026, 10, 19, 23, 0, tzinfo=UTC), status="error")

    last = TestClient(app).get("/api/status").json()["last_run"]
    assert last == {
        "week": "2026-W41",
        "status": "error",
        "finished_at": "2026-10-19T23:00:00+00:00",
    }


def test_budget_hidden_on_read_only_deployments(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULSE_READ_ONLY", "1")
    assert TestClient(app).get("/api/status").json()["budget"] is None
