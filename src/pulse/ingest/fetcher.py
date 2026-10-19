"""Concurrent multi-source fetch with partial-failure tolerance (tasks 1.6, 1.7)."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from pulse.config import AppConfig
from pulse.ingest.appstore_source import AppStoreSource
from pulse.ingest.base import ReviewSource
from pulse.ingest.normalizer import normalize, to_utc, window_start_dt
from pulse.ingest.playstore_source import PlayStoreSource
from pulse.models import RawReview, Source
from pulse.utils.weeks import date_to_iso_week, window_bounds


class AllSourcesFailedError(RuntimeError):
    """Raised when every enabled source failed."""

    def __init__(self, errors: dict[Source, str]) -> None:
        super().__init__(f"All sources failed: {errors}")
        self.errors = errors


@dataclass
class FetchResult:
    reviews: list[RawReview] = field(default_factory=list)
    coverage_start: dict[Source, date] = field(default_factory=dict)
    source_errors: dict[Source, str] = field(default_factory=dict)

    def counts_by_source(self) -> dict[str, int]:
        return dict(Counter(r.source for r in self.reviews))

    def counts_by_week(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.reviews:
            week = date_to_iso_week(to_utc(r.date))
            out.setdefault(week, {}).setdefault(r.source, 0)
            out[week][r.source] += 1
        return dict(sorted(out.items()))


def build_sources(config: AppConfig, selection: str = "all") -> list[ReviewSource]:
    sources: list[ReviewSource] = []
    if selection in ("all", "play_store") and config.sources.play_store.enabled:
        sources.append(PlayStoreSource(config.sources.play_store))
    if selection in ("all", "app_store") and config.sources.app_store.enabled:
        sources.append(AppStoreSource(config.sources.app_store))
    return sources


def fetch_all(
    sources: list[ReviewSource],
    week: str,
    n_weeks: int,
) -> FetchResult:
    """Fetch every source concurrently and normalize into one window-filtered list.

    A failing source is recorded in `source_errors`; we only raise when all fail.
    """
    start, end = window_bounds(week, n_weeks)
    window_start: datetime = window_start_dt(start)
    result = FetchResult()
    if not sources:
        raise ValueError("No sources enabled/selected")

    def run(src: ReviewSource) -> tuple[Source, list[RawReview] | Exception]:
        try:
            return src.name, src.fetch(window_start)
        except Exception as exc:  # per-source isolation
            return src.name, exc

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        outcomes = list(pool.map(run, sources))

    collected: list[RawReview] = []
    for name, outcome in outcomes:
        if isinstance(outcome, Exception):
            result.source_errors[name] = f"{type(outcome).__name__}: {outcome}"
            continue
        if outcome:
            result.coverage_start[name] = min(to_utc(r.date) for r in outcome).date()
        collected.extend(outcome)

    if len(result.source_errors) == len(sources):
        raise AllSourcesFailedError(result.source_errors)

    result.reviews = normalize(collected, start, end)
    return result


def missing_snapshot_weeks(coverage_start: date | None, week: str, n_weeks: int) -> list[str]:
    """ISO weeks in the window older than the live fetch reached (task 1.7).

    These are the weeks to load from stored snapshots when building the window.
    """
    start, _ = window_bounds(week, n_weeks)
    cutoff = coverage_start or window_bounds(week, n_weeks)[1]
    weeks: list[str] = []
    d = start
    while d < cutoff:
        w = date_to_iso_week(d)
        if w not in weeks and w != date_to_iso_week(cutoff):
            weeks.append(w)
        d = date.fromordinal(d.toordinal() + 7)
    return weeks
