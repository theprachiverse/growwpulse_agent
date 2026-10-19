"""Date normalization, window filtering and deduplication (task 1.5)."""

import hashlib
from datetime import UTC, date, datetime, time

from pulse.models import RawReview


def to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def window_start_dt(start: date) -> datetime:
    return datetime.combine(start, time.min, tzinfo=UTC)


def window_end_dt(end: date) -> datetime:
    """Inclusive upper bound: last instant of the end date."""
    return datetime.combine(end, time.max, tzinfo=UTC)


def filter_window(reviews: list[RawReview], start: date, end: date) -> list[RawReview]:
    """Keep reviews with start <= date <= end (inclusive, UTC), dates normalized to UTC."""
    lo, hi = window_start_dt(start), window_end_dt(end)
    out: list[RawReview] = []
    for r in reviews:
        d = to_utc(r.date)
        if lo <= d <= hi:
            out.append(r.model_copy(update={"date": d}))
    return out


def _text_hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.lower().split()).encode("utf-8")).hexdigest()


def dedup(reviews: list[RawReview]) -> list[RawReview]:
    """Drop duplicates on (source, review_id), then on (source, sha1(text)) for reposts.

    If the same review_id appears more than once (a user edited their review, or pages
    overlapped), the version with the newest date wins; ties keep the first seen.
    Reposts with identical text keep the first occurrence. Empty texts are not text-deduplicated.
    """
    newest: dict[tuple[str, str], RawReview] = {}
    order: list[tuple[str, str]] = []
    for r in reviews:
        id_key = (str(r.source), r.review_id)
        current = newest.get(id_key)
        if current is None:
            order.append(id_key)
            newest[id_key] = r
        elif to_utc(r.date) > to_utc(current.date):
            newest[id_key] = r

    seen_text: set[tuple[str, str]] = set()
    out: list[RawReview] = []
    for order_key in order:
        r = newest[order_key]
        if r.text.strip():
            text_key = (r.source, _text_hash(f"{r.title or ''}|{r.text}"))
            if text_key in seen_text:
                continue
            seen_text.add(text_key)
        out.append(r)
    return out


def normalize(reviews: list[RawReview], start: date, end: date) -> list[RawReview]:
    return dedup(filter_window(reviews, start, end))
