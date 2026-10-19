"""Apple App Store (India) review source via the public RSS JSON feed (task 1.3)."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from pulse.config import AppStoreConfig
from pulse.ingest.base import ReviewSource
from pulse.ingest.retry import with_backoff
from pulse.models import RawReview, Source

FEED_URL = (
    "https://itunes.apple.com/{country}/rss/customerreviews/"
    "page={page}/id={app_id}/sortBy=mostRecent/json"
)


def _label(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if isinstance(value, dict):
        label = value.get("label")
        return None if label is None else str(label)
    return None


def extract_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return feed entries as a list (a single entry arrives as a dict)."""
    entries = payload.get("feed", {}).get("entry")
    if entries is None:
        return []
    if isinstance(entries, dict):
        return [entries]
    return list(entries)


def map_app_review(entry: dict[str, Any]) -> RawReview | None:
    """Map one feed entry to `RawReview`; returns None for non-review entries.

    `author.*` and links are never read, so they are discarded here.
    """
    rating = _label(entry, "im:rating")
    if rating is None:  # app metadata entry
        return None
    updated = _label(entry, "updated")
    review_id = _label(entry, "id")
    if updated is None or review_id is None:
        return None
    date = datetime.fromisoformat(updated)
    date = date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)
    try:
        votes = max(0, int(_label(entry, "im:voteSum") or 0))
    except ValueError:
        votes = 0
    return RawReview(
        source="app_store",
        review_id=review_id,
        rating=int(rating),
        title=_label(entry, "title"),
        text=_label(entry, "content") or "",
        date=date,
        app_version=_label(entry, "im:version"),
        helpful_votes=votes,
    )


class _RetryableHTTPError(Exception):
    pass


class AppStoreSource(ReviewSource):
    name: Source = "app_store"

    def __init__(
        self,
        config: AppStoreConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._client = client
        self._sleep = sleep

    def _get_page(self, client: httpx.Client, page: int) -> dict[str, Any] | None:
        url = FEED_URL.format(country=self.config.country, page=page, app_id=self.config.app_id)

        def call() -> httpx.Response:
            try:
                resp = client.get(url)
            except httpx.TransportError as exc:
                raise _RetryableHTTPError(str(exc)) from exc
            if resp.status_code >= 500:
                raise _RetryableHTTPError(f"HTTP {resp.status_code}")
            return resp

        resp = with_backoff(
            call, max_attempts=5, retry_on=(_RetryableHTTPError,), sleep=self._sleep
        )
        if resp.status_code >= 400:
            if page > 1:  # past the last available page
                return None
            resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def fetch(self, window_start: datetime) -> list[RawReview]:
        cfg = self.config
        client = self._client or httpx.Client(timeout=30.0, follow_redirects=True)
        owns_client = self._client is None
        results: list[RawReview] = []
        try:
            for page in range(1, cfg.max_pages + 1):
                payload = self._get_page(client, page)
                if payload is None:
                    break
                entries = extract_entries(payload)
                if not entries:
                    break
                reached_window_end = False
                for entry in entries:
                    review = map_app_review(entry)
                    if review is None:
                        continue
                    if review.date < window_start:
                        reached_window_end = True
                        continue
                    results.append(review)
                if reached_window_end or len(results) >= cfg.max_reviews:
                    break
        finally:
            if owns_client:
                client.close()
        return results[: cfg.max_reviews]
