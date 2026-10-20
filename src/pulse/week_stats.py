"""Per-week review counts and dropdown labels for the dashboard.

Two different numbers exist for a week and must not be mixed:
- *posted* reviews: cleaned reviews whose date falls inside that ISO week;
- the *window*: the rolling N-week set a pulse was built from. A run stores the whole window
  in its `reviews_clean.jsonl`, so that file's length is NOT the week's own count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pulse.models import CleanReview, Pulse
from pulse.run_store import RunStore
from pulse.utils.weeks import week_end_date, week_start_date

SOURCES: tuple[Literal["play_store", "app_store"], ...] = ("play_store", "app_store")


def posted_in_week(store: RunStore, week: str) -> list[CleanReview]:
    """Cleaned reviews posted during `week`, de-duplicated.

    Weekly snapshots are the per-week record. The week's own run file holds a whole window, so
    only its rows dated in `week` are used, which covers weeks that have no snapshot.
    """
    candidates = [
        *(r for src in SOURCES for r in store.load_snapshot(src, week)),
        *(r for r in store.load_clean_reviews(week) if r.iso_week == week),
    ]
    seen: set[str] = set()
    unique: list[CleanReview] = []
    for r in candidates:
        if r.rid not in seen:
            seen.add(r.rid)
            unique.append(r)
    return unique


def pulse_window_weeks(pulse: Pulse) -> int:
    """Length of the rolling window a pulse covers, in weeks."""
    return max(1, ((pulse.window_end - pulse.window_start).days + 1) // 7)


def days_elapsed(week: str, today: date | None = None) -> int | None:
    """1..7 while `week` is still in progress (UTC), or None once it has finished."""
    today = today or datetime.now(UTC).date()
    if week_end_date(week) < today:
        return None
    return min(7, max(1, (today - week_start_date(week)).days + 1))


def is_pulse_stale(pulse: Pulse | None, run_clean_count: int) -> bool:
    """True when the run's clean data no longer matches the pulse built from it.

    This happens when a later run re-collects the reviews with a different window length
    (or new data) without regenerating the pulse.
    """
    return pulse is not None and run_clean_count > 0 and run_clean_count != pulse.total_reviews


def option_label(
    week: str,
    human: str,
    posted: int,
    pulse: Pulse | None,
    stale: bool,
    in_progress_day: int | None,
) -> str:
    """Dropdown text, e.g. 'October W3 (...) · 2026-W42 · 133 reviews · pulse: 10-wk window, 1,527'."""
    parts = [f"{human} · {week}"]
    if in_progress_day is not None:
        parts.append(f"{posted:,} reviews so far · in progress (day {in_progress_day}/7)")
    else:
        parts.append(f"{posted:,} reviews")
    if pulse is not None:
        window = f"pulse: {pulse_window_weeks(pulse)}-wk window, {pulse.total_reviews:,}"
        parts.append(f"{window} ⚠ outdated" if stale else window)
    return " · ".join(parts)
