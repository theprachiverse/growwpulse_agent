"""Small exponential-backoff helper shared by ingest sources."""

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    should_retry: Callable[[BaseException], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call `fn`, retrying with exponential backoff (1s, 2s, 4s, ...)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except retry_on as exc:
            if attempt >= max_attempts or (should_retry is not None and not should_retry(exc)):
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
