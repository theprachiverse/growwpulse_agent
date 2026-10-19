"""CSV/JSON file fallback source for backfill and offline development (task 1.4)."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pulse.ingest.base import ReviewSource
from pulse.models import RawReview, Source

# Canonical RawReview field -> column name in the file.
DEFAULT_COLUMN_MAP: dict[str, str] = {
    "review_id": "review_id",
    "rating": "rating",
    "title": "title",
    "text": "text",
    "date": "date",
    "app_version": "app_version",
    "helpful_votes": "helpful_votes",
}


def _parse_date(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class FileSource(ReviewSource):
    """Loads reviews of one store from a CSV or JSON export.

    Only mapped columns are read; any other column (e.g. author names) is ignored.
    """

    def __init__(
        self,
        path: Path | str,
        source: Source,
        column_map: dict[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.name = source
        self.column_map = {**DEFAULT_COLUMN_MAP, **(column_map or {})}

    def _rows(self) -> list[dict[str, Any]]:
        if self.path.suffix.lower() == ".csv":
            with self.path.open(encoding="utf-8", newline="") as f:
                return list(csv.DictReader(f))
        with self.path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("reviews", [])
        return list(data)

    def fetch(self, window_start: datetime) -> list[RawReview]:
        cm = self.column_map
        out: list[RawReview] = []
        for row in self._rows():
            raw_date = row.get(cm["date"])
            raw_rating = row.get(cm["rating"])
            raw_id = row.get(cm["review_id"])
            if raw_date in (None, "") or raw_rating in (None, "") or raw_id in (None, ""):
                continue
            date = _parse_date(raw_date)
            if date < window_start:
                continue
            votes = row.get(cm["helpful_votes"]) or 0
            out.append(
                RawReview(
                    source=self.name,
                    review_id=str(raw_id),
                    rating=int(float(raw_rating)),
                    title=(row.get(cm["title"]) or None),
                    text=row.get(cm["text"]) or "",
                    date=date,
                    app_version=(row.get(cm["app_version"]) or None),
                    helpful_votes=max(0, int(float(votes))),
                )
            )
        return out
