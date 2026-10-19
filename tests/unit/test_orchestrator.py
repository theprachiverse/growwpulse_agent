"""Unit tests for Phase 7: Orchestrator, Scheduling, Retention & Hardening."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from evals.fake_mcp import FakeMCP, make_settings

from pulse.guardrails.validators import ValidationReport
from pulse.llm.client import MockLLMClient
from pulse.orchestrator import (
    LockError,
    Orchestrator,
    PreflightError,
    acquire_run_lock,
    check_mcp_health,
    estimate_llm_cost,
    prune_retention,
)
from pulse.run_store import RunStore
from tests.unit.test_compose import make_test_review, make_test_themes
from tests.unit.test_publish import make_pulse


def test_estimate_llm_cost():
    usage = {"tokens_prompt": 1_000_000, "tokens_completion": 500_000}
    # 1.0M * 0.0000006 = 0.60; 0.5M * 0.0000008 = 0.40 -> 1.00
    cost = estimate_llm_cost(usage)
    assert cost == pytest.approx(1.0, rel=1e-2)


def test_acquire_run_lock_prevents_concurrent_runs(tmp_path: Path):
    run_dir = tmp_path / "runs" / "2026-W42"
    with acquire_run_lock(run_dir, "2026-W42") as lock1:
        assert lock1.exists()
        # Attempting second lock for same week must raise LockError (EC-OPS-05)
        with (
            pytest.raises(LockError, match="is locked by active process PID"),
            acquire_run_lock(run_dir, "2026-W42"),
        ):
            pass
    # After exiting, lock is released
    assert not lock1.exists()


def test_check_mcp_health():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert check_mcp_health("https://example.com/mcp") is True
        mock_get.assert_called_with("https://example.com/healthz", timeout=10.0)

    with patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=500)
        assert check_mcp_health("https://example.com/mcp") is False

    with patch("httpx.get", side_effect=Exception("Connection refused")):
        assert check_mcp_health("https://example.com/mcp") is False


def test_prune_retention_preserves_manifests_and_deletes_heavy_data(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    target_week = "2026-W42"

    # Recent run: 2026-W41 (1 week old -> keep all)
    recent_dir = store.run_dir("2026-W41")
    (recent_dir / "reviews_clean.jsonl").write_text("clean data", encoding="utf-8")
    (recent_dir / "assignments.jsonl").write_text("assignments", encoding="utf-8")
    (recent_dir / "run_manifest.json").write_text("{}", encoding="utf-8")

    # Old run: 2026-W20 (> 20 weeks old -> prune heavy files, keep manifests & pulse)
    old_dir = store.run_dir("2026-W20")
    (old_dir / "reviews_clean.jsonl").write_text("old clean", encoding="utf-8")
    (old_dir / "assignments.jsonl").write_text("old assignments", encoding="utf-8")
    (old_dir / "pulse.json").write_text("{}", encoding="utf-8")
    (old_dir / "pulse.md").write_text("# Pulse", encoding="utf-8")
    (old_dir / "run_manifest.json").write_text("{}", encoding="utf-8")

    # Old snapshot: 2026-W20
    old_snap = store.snapshot_dir("play_store") / "2026-W20.jsonl"
    old_snap.write_text("old snap", encoding="utf-8")

    # Recent snapshot: 2026-W41
    recent_snap = store.snapshot_dir("play_store") / "2026-W41.jsonl"
    recent_snap.write_text("recent snap", encoding="utf-8")

    pruned = prune_retention(store, target_week=target_week, retention_weeks=12)

    # Old heavy files pruned
    assert not (old_dir / "reviews_clean.jsonl").exists()
    assert not (old_dir / "assignments.jsonl").exists()
    assert not old_snap.exists()

    # Old manifests & pulse preserved
    assert (old_dir / "pulse.json").exists()
    assert (old_dir / "pulse.md").exists()
    assert (old_dir / "run_manifest.json").exists()

    # Recent run files untouched
    assert (recent_dir / "reviews_clean.jsonl").exists()
    assert recent_snap.exists()

    assert "2026-W20/reviews_clean.jsonl" in pruned["pruned_runs"]
    assert "play_store/2026-W20.jsonl" in pruned["pruned_snapshots"]


def test_orchestrator_preflight_failure_aborts_early(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    settings = make_settings(tmp_path)
    orch = Orchestrator(settings=settings, run_store=store, llm_client=MockLLMClient())

    with (
        patch("pulse.orchestrator.check_mcp_health", return_value=False),
        pytest.raises(PreflightError, match="Pre-flight health check failed"),
    ):
        orch.run(week="2026-W42", dry_run=False, check_health=True)

    # Manifest recorded with error status
    manifest = store.load_manifest("2026-W42")
    assert manifest is not None
    assert manifest.status == "error"


def test_orchestrator_from_step_missing_artifact_raises(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    settings = make_settings(tmp_path)
    orch = Orchestrator(settings=settings, run_store=store, llm_client=MockLLMClient())

    # Missing reviews_clean.jsonl when starting from theming (EC-OPS-06)
    with pytest.raises(FileNotFoundError, match="missing clean reviews artifact"):
        orch.run(week="2026-W42", from_step="theming", check_health=False)


def test_orchestrator_low_volume_aborts_when_configured(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    settings = make_settings(tmp_path)
    settings.app_config.ingest.min_reviews = 50
    settings.app_config.ingest.skip_on_low_volume = True

    orch = Orchestrator(settings=settings, run_store=store, llm_client=MockLLMClient())

    from pulse.models import RawReview

    # Mock fetch_all to return only 5 raw reviews (< 50)
    revs = [
        RawReview(
            review_id=f"r{i}",
            source="play_store",
            rating=1,
            text="Valid text with plenty of words to avoid length filter in test.",
            date=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        )
        for i in range(5)
    ]
    with patch("pulse.orchestrator.fetch_all") as mock_fetch:
        mock_fetch.return_value = MagicMock(
            reviews=revs,
            coverage_start={"play_store": date(2026, 10, 1)},
            source_errors={},
            counts_by_source=lambda: {"play_store": 5},
        )
        res = orch.run(week="2026-W42", dry_run=True, check_health=False)
        assert res.manifest.status == "error"
        assert res.pulse is None


def test_orchestrator_guardrails_failure_blocks_publishing(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    settings = make_settings(tmp_path)
    fake_mcp = FakeMCP()
    orch = Orchestrator(
        settings=settings,
        run_store=store,
        llm_client=MockLLMClient(),
        mcp_client=fake_mcp,
    )

    # Seed clean reviews and themes
    revs = [make_test_review(f"r{i}") for i in range(20)]
    store.save_clean_reviews("2026-W42", revs)
    themes = make_test_themes()
    store.save_themes("2026-W42", themes)
    store.save_assignments("2026-W42", [{"rid": f"r{i}", "theme_id": "t1"} for i in range(20)])

    # Mock run_compose_pipeline to return failed validation
    bad_report = ValidationReport(
        passed=False,
        checks={"theme_count": True, "verbatim": False},
        hard_failures=["Quote text is not an exact substring"],
        word_count=210,
        max_words_allowed=250,
    )
    mock_pulse = make_pulse()

    with patch("pulse.orchestrator.run_compose_pipeline", return_value=(mock_pulse, bad_report)):
        res = orch.run(week="2026-W42", from_step="compose", check_health=False)
        assert res.manifest.status == "failed_validation"
        # Zero MCP calls were made
        assert len(fake_mcp.calls) == 0


def test_orchestrator_end_to_end_resumption_and_dry_run(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    settings = make_settings(tmp_path)
    fake_mcp = FakeMCP()
    orch = Orchestrator(
        settings=settings,
        run_store=store,
        llm_client=MockLLMClient(),
        mcp_client=fake_mcp,
    )

    # Seed state for 2026-W42
    revs = [make_test_review(f"r{i}") for i in range(20)]
    store.save_clean_reviews("2026-W42", revs)
    themes = make_test_themes()
    store.save_themes("2026-W42", themes)
    store.save_assignments("2026-W42", [{"rid": f"r{i}", "theme_id": "t1"} for i in range(20)])

    mock_pulse = make_pulse()
    good_report = ValidationReport(
        passed=True,
        checks={"theme_count": True, "verbatim": True, "word_limit": True},
        word_count=210,
        max_words_allowed=250,
    )

    with patch("pulse.orchestrator.run_compose_pipeline", return_value=(mock_pulse, good_report)):
        # Run with from_step=compose in dry_run
        res = orch.run(
            week="2026-W42",
            dry_run=True,
            from_step="compose",
            check_health=False,
        )

        assert res.manifest.status == "success"
        assert res.step_timings.get("compose") is not None
        assert "ingest" not in res.step_timings
        assert len(fake_mcp.calls) == 0  # dry run does not invoke MCP
