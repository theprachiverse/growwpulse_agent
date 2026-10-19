"""Review text cleaning and length filtering (Task 2.1).

Rules:
- Unicode NFC normalization.
- Zero-width character removal.
- Whitespace collapse.
- Emoji stripping/removal.
- Drop empty or emoji-only reviews.
- Drop reviews with word count <= min_words (default 8, keeping only reviews with > 8 words).
"""

from __future__ import annotations

import re
import unicodedata

# Zero-width spaces, directional marks, BOM
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff\u202a-\u202e]")
WHITESPACE_PATTERN = re.compile(r"\s+")

# Matches standard Unicode emoji ranges and symbols
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27ff]|[\u2300-\u23ff]|[\u2b50\u2b55\u200d\u20e3]",
    flags=re.UNICODE,
)


def strip_emojis(text: str | None) -> str:
    """Remove emojis and miscellaneous pictographs from text."""
    if not text:
        return ""
    return EMOJI_PATTERN.sub("", text)


def normalize_text(text: str | None, remove_emojis: bool = True) -> str:
    """Normalize text using Unicode NFC, strip emojis/zero-width chars, and collapse whitespace."""
    if not text:
        return ""
    if remove_emojis:
        text = strip_emojis(text)
    # Unicode NFC normalization
    normalized = unicodedata.normalize("NFC", text)
    # Strip zero-width characters
    stripped = ZERO_WIDTH_PATTERN.sub("", normalized)
    # Collapse consecutive whitespace to single space and strip edges
    return WHITESPACE_PATTERN.sub(" ", stripped).strip()


def count_words(text: str) -> int:
    """Count words containing at least one alphanumeric character.

    Prevents emoji-only or punctuation-only tokens from being counted as words.
    """
    if not text:
        return 0
    tokens = text.split()
    return sum(1 for token in tokens if any(char.isalnum() for char in token))


def has_meaningful_text(text: str) -> bool:
    """Check whether text contains at least one alphanumeric character."""
    if not text:
        return False
    return any(char.isalnum() for char in text)


def should_keep_review(
    text: str,
    min_words: int = 8,
    remove_emojis: bool = True,
) -> bool:
    """Determine if a review meets content, emoji, and length thresholds.

    Reviews must:
    1. Not be empty.
    2. Contain meaningful text (not emoji-only or symbols-only).
    3. Have strictly more than min_words words (count_words > min_words).
    """
    cleaned = normalize_text(text, remove_emojis=remove_emojis)
    if not has_meaningful_text(cleaned):
        return False
    return count_words(cleaned) > min_words
