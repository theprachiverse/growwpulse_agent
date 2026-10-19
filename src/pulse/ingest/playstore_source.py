"""Google Play Store review source (task 1.2)."""

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pulse.config import PlayStoreConfig
from pulse.ingest.base import ReviewSource
from pulse.ingest.retry import with_backoff
from pulse.models import RawReview, Source


def map_play_review(item: dict[str, Any]) -> RawReview | None:
    """Map a google-play-scraper item to `RawReview`.

    Only whitelisted fields are read, so `userName`, `userImage`, `replyContent`
    and `repliedAt` are discarded here and can never reach a `RawReview`.
    """
    at = item.get("at")
    score = item.get("score")
    review_id = item.get("reviewId")
    if at is None or score is None or review_id is None:
        return None
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
    return RawReview(
        source="play_store",
        review_id=str(review_id),
        rating=int(score),
        title=None,
        text=item.get("content") or "",
        date=at,
        app_version=item.get("reviewCreatedVersion") or item.get("appVersion"),
        helpful_votes=max(0, int(item.get("thumbsUpCount") or 0)),
    )


def _default_reviews_fn() -> Callable[..., tuple[list[dict[str, Any]], Any]]:
    from google_play_scraper import Sort, reviews

    def call(**kwargs: Any) -> tuple[list[dict[str, Any]], Any]:
        return reviews(sort=Sort.NEWEST, **kwargs)  # type: ignore[no-any-return]

    return call


class PlayStoreSource(ReviewSource):
    name: Source = "play_store"

    def __init__(
        self,
        config: PlayStoreConfig,
        *,
        reviews_fn: Callable[..., tuple[list[dict[str, Any]], Any]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        page_delay: tuple[float, float] = (0.5, 1.0),
    ) -> None:
        self.config = config
        self._reviews_fn = reviews_fn
        self._sleep = sleep
        self._page_delay = page_delay

    def fetch(self, window_start: datetime) -> list[RawReview]:
        fn = self._reviews_fn or _default_reviews_fn()
        cfg = self.config
        results: list[RawReview] = []
        token: Any = None

        while len(results) < cfg.max_reviews:
            count = min(cfg.page_size, cfg.max_reviews - len(results))

            def call(count: int = count, token: Any = token) -> tuple[list[dict[str, Any]], Any]:
                return fn(
                    app_id=cfg.package_id,
                    lang=cfg.lang,
                    country=cfg.country,
                    count=count,
                    continuation_token=token,
                )

            batch, token = with_backoff(call, max_attempts=5, sleep=self._sleep)
            if not batch:
                break

            reached_window_end = False
            for item in batch:
                review = map_play_review(item)
                if review is None:
                    continue
                if review.date < window_start:
                    reached_window_end = True
                    break
                results.append(review)
            if reached_window_end or token is None:
                break
            self._sleep(random.uniform(*self._page_delay))

        return results[: cfg.max_reviews]
