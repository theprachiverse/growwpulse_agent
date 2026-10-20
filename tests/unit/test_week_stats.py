"""Per-week counts, window labels and staleness shown in the week dropdown."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pulse.models import CleanReview, Pulse
from pulse.run_store import RunStore
from pulse.week_stats import (
    days_elapsed,
    is_pulse_stale,
    option_label,
    posted_in_week,
    pulse_window_weeks,
)


def review(rid: str, week: str, source: str = "play_store") -> CleanReview:
    return CleanReview(
        rid=rid,
        source=source,
        rating=4,
        clean_title=None,
        clean_text="a perfectly fine review with enough words to count",
        date=date(2026, 10, 14),
        iso_week=week,
    )


def make_pulse(total: int, start: date, end: date) -> Pulse:
    return Pulse.model_construct(
        week="2026-W42", window_start=start, window_end=end, total_reviews=total
    )


def test_run_file_holding_a_whole_window_does_not_inflate_a_weeks_count(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    window = [review(f"a{i}", "2026-W41") for i in range(5)] + [
        review(f"b{i}", "2026-W42") for i in range(3)
    ]
    store.save_clean_reviews("2026-W42", window)  # 8 rows, only 3 posted in W42

    assert len(posted_in_week(store, "2026-W42")) == 3
    assert len(posted_in_week(store, "2026-W41")) == 0  # no snapshot, no run of its own


def test_snapshots_and_run_file_are_merged_without_duplicates(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    store.save_snapshot("play_store", "2026-W42", [review("x1", "2026-W42")])
    store.save_snapshot("app_store", "2026-W42", [review("x2", "2026-W42", "app_store")])
    store.save_clean_reviews("2026-W42", [review("x1", "2026-W42"), review("x3", "2026-W42")])

    assert {r.rid for r in posted_in_week(store, "2026-W42")} == {"x1", "x2", "x3"}


def test_week_in_progress_vs_finished() -> None:
    assert days_elapsed("2026-W43", today=date(2026, 10, 20)) == 2  # Mon 19 -> Tue 20
    assert days_elapsed("2026-W43", today=date(2026, 10, 25)) == 7
    assert days_elapsed("2026-W42", today=date(2026, 10, 20)) is None


def test_window_weeks_and_staleness() -> None:
    ten = make_pulse(1527, date(2026, 8, 10), date(2026, 10, 18))
    assert pulse_window_weeks(ten) == 10
    assert not is_pulse_stale(ten, 1527)
    assert is_pulse_stale(ten, 1326)  # clean data re-collected with a different window
    assert not is_pulse_stale(ten, 0)  # nothing to compare against
    assert not is_pulse_stale(None, 1326)


def test_option_label_states_which_number_is_which() -> None:
    pulse = make_pulse(1527, date(2026, 8, 10), date(2026, 10, 18))
    done = option_label("2026-W42", "October W3", 133, pulse, False, None)
    assert done == "October W3 · 2026-W42 · 133 reviews · pulse: 10-wk window, 1,527"
    assert "⚠ outdated" in option_label("2026-W42", "October W3", 133, pulse, True, None)

    partial = option_label("2026-W43", "October W4", 40, None, False, 2)
    assert "40 reviews so far" in partial
    assert "in progress (day 2/7)" in partial
