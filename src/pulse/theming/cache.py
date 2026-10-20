"""On-disk cache of LLM review classifications.

Weekly windows overlap by ~90%, and a posted review never changes, so re-classifying the whole
window every run wastes LLM calls. Each cached answer is keyed by review ID + the exact text sent
to the model, inside a file named by a fingerprint of everything else that shapes the answer
(themes, system prompt, chat model). Any change to those yields a new fingerprint and therefore a
clean cache, so a stale assignment can never be served.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pulse.observability.logging import get_logger
from pulse.theming.models import ClassificationItem, ThemeSpec

logger = get_logger("pulse.theming.cache")

KEEP_FINGERPRINTS = 5


def fingerprint(themes: list[ThemeSpec], system_prompt: str, chat_model: str) -> str:
    """Stable 16-char digest of the inputs (besides the review) that decide a classification."""
    payload = json.dumps(
        {
            "themes": [t.model_dump() for t in themes],
            "prompt": system_prompt,
            "model": chat_model,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def review_key(rid: str, prompt_text: str) -> str:
    """Key for one review: its ID plus a hash of the text the model saw."""
    return f"{rid}:{hashlib.sha256(prompt_text.encode('utf-8')).hexdigest()[:12]}"


class ClassificationCache:
    """Cache for one fingerprint; `load` reads it, `save` writes it atomically."""

    def __init__(self, cache_dir: Path | str, fp: str) -> None:
        self.dir = Path(cache_dir)
        self.path = self.dir / f"classifications-{fp}.json"
        self._items: dict[str, tuple[str, float]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._items = {k: (str(v[0]), float(v[1])) for k, v in raw.items()}
        except Exception as exc:
            logger.warning("classification_cache_load_failed", error=str(exc))
            self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str) -> ClassificationItem | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        return ClassificationItem(rid=key.split(":", 1)[0], theme_id=hit[0], confidence=hit[1])

    def put(self, key: str, item: ClassificationItem) -> None:
        self._items[key] = (item.theme_id, item.confidence)
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._items), encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
            self._prune()
        except Exception as exc:
            logger.warning("classification_cache_save_failed", error=str(exc))

    def _prune(self) -> None:
        """Keep only the newest few fingerprints so old theme sets don't pile up."""
        files = sorted(
            self.dir.glob("classifications-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for old in files[KEEP_FINGERPRINTS:]:
            old.unlink(missing_ok=True)
