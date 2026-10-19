"""Preprocessing and PII scrubbing module (Phase 2)."""

from pulse.preprocess.cleaner import (
    count_words,
    normalize_text,
    should_keep_review,
    strip_emojis,
)
from pulse.preprocess.embeddings import EmbeddingCache, embed_reviews
from pulse.preprocess.enrich import detect_language, enrich_review_metadata
from pulse.preprocess.pii import scrub_pii, scrub_review_fields
from pulse.preprocess.pipeline import process_and_persist_reviews, process_single_review
from pulse.preprocess.pseudonymize import generate_rid

__all__ = [
    "EmbeddingCache",
    "count_words",
    "detect_language",
    "embed_reviews",
    "enrich_review_metadata",
    "generate_rid",
    "normalize_text",
    "process_and_persist_reviews",
    "process_single_review",
    "scrub_pii",
    "scrub_review_fields",
    "should_keep_review",
    "strip_emojis",
]
