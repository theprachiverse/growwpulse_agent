"""Review enrichment module (Task 2.4).

Adds:
- Language identification (via langdetect, fallback to 'en').
- ISO week assignment (via pulse.utils.weeks.date_to_iso_week).
- UTC date conversion.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime

from pulse.utils.weeks import date_to_iso_week


def detect_language(text: str) -> str:
    """Detect the ISO 639-1 language tag of a text snippet.

    Falls back cleanly to 'en' on short/ambiguous text or detection errors.
    """
    if not text or len(text.strip()) < 3:
        return "en"
    try:
        import langdetect

        # Seed langdetect for deterministic results
        langdetect.DetectorFactory.seed = 42
        return str(langdetect.detect(text))
    except Exception:
        return "en"


def non_latin_letter_ratio(text: str) -> float:
    """Share of alphabetic characters that are not Latin script (e.g. Devanagari)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    non_latin = sum(1 for ch in letters if not unicodedata.name(ch, "").startswith("LATIN"))
    return non_latin / len(letters)


def is_english(text: str, lang: str, max_non_latin: float = 0.05) -> bool:
    """True if the text is detected as English and has (almost) no non-Latin script.

    langdetect often tags Hindi/English mixed text as English, so script is checked too.
    """
    return lang == "en" and non_latin_letter_ratio(text) <= max_non_latin


def enrich_review_metadata(dt: datetime) -> tuple[date, str]:
    """Convert datetime to UTC date and corresponding ISO week string."""
    utc_dt = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    review_date = utc_dt.date()
    iso_week = date_to_iso_week(utc_dt)
    return review_date, iso_week
