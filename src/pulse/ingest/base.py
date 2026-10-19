"""Abstract review source interface (task 1.1)."""

from abc import ABC, abstractmethod
from datetime import datetime

from pulse.models import RawReview, Source


class ReviewSource(ABC):
    """A provider of public reviews, normalized into `RawReview`."""

    name: Source

    @abstractmethod
    def fetch(self, window_start: datetime) -> list[RawReview]:
        """Fetch reviews dated at or after `window_start` (timezone-aware UTC)."""
