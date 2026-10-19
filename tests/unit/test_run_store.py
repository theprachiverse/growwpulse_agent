"""Unit tests for RunStore persistence (Task 0.4)."""

from datetime import UTC, date, datetime
from pathlib import Path

from pulse.models import (
    ActionIdea,
    CleanReview,
    Pulse,
    Quote,
    RunManifest,
    Theme,
)
from pulse.run_store import RunStore


def test_run_store_clean_reviews(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    reviews = [
        CleanReview(
            rid="rid_1",
            source="play_store",
            rating=5,
            clean_text="Great experience",
            date=date(2026, 10, 10),
            iso_week="2026-W41",
            helpful_votes=1,
        ),
        CleanReview(
            rid="rid_2",
            source="app_store",
            rating=2,
            clean_title="Bug in charts",
            clean_text="Charts freeze on iOS 18",
            date=date(2026, 10, 11),
            iso_week="2026-W41",
            helpful_votes=0,
        ),
    ]

    saved_path = store.save_clean_reviews("2026-W41", reviews)
    assert saved_path.exists()

    loaded = store.load_clean_reviews("2026-W41")
    assert len(loaded) == 2
    assert loaded[0].rid == "rid_1"
    assert loaded[1].clean_title == "Bug in charts"


def test_run_store_snapshots(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    w41_reviews = [
        CleanReview(
            rid="rid_ios_1",
            source="app_store",
            rating=4,
            clean_text="Good",
            date=date(2026, 10, 10),
            iso_week="2026-W41",
            helpful_votes=0,
        )
    ]
    w42_reviews = [
        CleanReview(
            rid="rid_ios_2",
            source="app_store",
            rating=1,
            clean_text="Crash",
            date=date(2026, 10, 17),
            iso_week="2026-W42",
            helpful_votes=1,
        )
    ]

    store.save_snapshot("app_store", "2026-W41", w41_reviews)
    store.save_snapshot("app_store", "2026-W42", w42_reviews)

    multi_loaded = store.load_snapshots_for_weeks("app_store", ["2026-W41", "2026-W42"])
    assert len(multi_loaded) == 2
    assert {r.rid for r in multi_loaded} == {"rid_ios_1", "rid_ios_2"}


def test_run_store_themes_and_pulse(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    theme = Theme(
        theme_id="t1",
        name="Withdrawals",
        definition="Payout delay issues",
        review_count=50,
        share=0.25,
        avg_rating=2.1,
        low_rating_share=0.6,
        by_source={"play_store": 40, "app_store": 10},
    )
    store.save_themes("2026-W42", [theme])
    loaded_themes = store.load_themes("2026-W42")
    assert loaded_themes is not None
    assert len(loaded_themes) == 1
    assert loaded_themes[0].name == "Withdrawals"

    quote = Quote(
        rid="rid_1",
        source="play_store",
        theme_id="t1",
        text="Withdrawal took 3 days",
        rating=1,
        month="Oct 2026",
    )
    action = ActionIdea(
        title="Speed up IMPS",
        rationale="Integrate direct IMPS gateway",
        theme_id="t1",
        owner_hint="Engineering",
    )
    pulse = Pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        total_reviews=200,
        reviews_by_source={"play_store": 160, "app_store": 40},
        avg_rating_by_source={"play_store": 3.6, "app_store": 4.0},
        avg_rating=3.68,
        top_themes=[theme, theme, theme],
        quotes=[quote, quote, quote],
        actions=[action, action, action],
        markdown="# Pulse Week 42\nSample note.",
        word_count=5,
    )

    json_p, md_p = store.save_pulse("2026-W42", pulse)
    assert json_p.exists()
    assert md_p.exists()

    loaded_pulse = store.load_pulse("2026-W42")
    assert loaded_pulse is not None
    assert loaded_pulse.total_reviews == 200


def test_run_store_manifest(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    manifest = RunManifest(
        run_id="run_001",
        week="2026-W42",
        started_at=datetime.now(UTC),
        status="success",
        doc_id="doc_xyz",
        draft_id="draft_abc",
    )
    store.save_manifest(manifest)
    loaded = store.load_manifest("2026-W42")
    assert loaded is not None
    assert loaded.doc_id == "doc_xyz"
    assert loaded.status == "success"
