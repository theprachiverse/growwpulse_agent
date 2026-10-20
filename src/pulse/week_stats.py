"""Per-week review counts and dropdown labels for the dashboard.

Two different numbers exist for a week and must not be mixed:
- *posted* reviews: cleaned reviews whose date falls inside that ISO week;
- the *window*: the rolling N-week set a pulse was built from. A run stores the whole window
  in its `reviews_clean.jsonl`, so that file's length is NOT the week's own count.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pulse.models import CleanReview, Pulse
from pulse.run_store import RunStore
from pulse.utils.weeks import date_to_iso_week, week_end_date, week_start_date

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


def previous_week(week: str) -> str:
    """The ISO week before `week`."""
    return date_to_iso_week(week_start_date(week) - timedelta(days=1))


def theme_breakdown(
    assignments: Iterable[dict[str, Any]],
    rids: set[str] | None,
    prev_rids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-theme star mix and week-over-week share change, from review assignments.

    `rids` restricts the scope (None = every assignment). `rating_dist` is [n1..n5].
    `trend_pts` is this scope's share minus `prev_rids`' share, in percentage points; it is
    None when either side has no assigned reviews.
    """
    scoped = [a for a in assignments if rids is None or a["rid"] in rids]
    prev = [a for a in assignments if prev_rids and a["rid"] in prev_rids]
    dist: dict[str, list[int]] = defaultdict(lambda: [0] * 5)
    for a in scoped:
        dist[a["theme_id"]][a["rating"] - 1] += 1
    prev_counts: dict[str, int] = defaultdict(int)
    for a in prev:
        prev_counts[a["theme_id"]] += 1

    out: dict[str, dict[str, Any]] = {}
    for tid, d in dist.items():
        trend = None
        if scoped and prev:
            trend = round((sum(d) / len(scoped) - prev_counts[tid] / len(prev)) * 100, 1)
        out[tid] = {"rating_dist": d, "trend_pts": trend}
    return out


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
