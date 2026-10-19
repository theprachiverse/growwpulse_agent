"""End-to-end preprocessing pipeline (Phase 2).

Transforms RawReview[] -> CleanReview[] with:
1. Normalization & length filtering (> 8 words).
2. PII scrubbing (title + text) & hit logging.
3. Pseudonymization (rid generation).
4. Language & ISO week enrichment.
5. Per-source snapshot persistence & historical snapshot merging.
6. Embedding computation with caching.
7. Persistence to data/runs/<week>/reviews_clean.jsonl.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from pulse.config import AppConfig
from pulse.ingest.fetcher import missing_snapshot_weeks
from pulse.models import CleanReview, RawReview, Source
from pulse.observability.logging import get_logger
from pulse.preprocess.cleaner import normalize_text, should_keep_review
from pulse.preprocess.embeddings import embed_reviews
from pulse.preprocess.enrich import detect_language, enrich_review_metadata, is_english
from pulse.preprocess.pii import scrub_review_fields
from pulse.preprocess.pseudonymize import generate_rid
from pulse.run_store import RunStore

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.preprocess")


def process_single_review(
    raw: RawReview,
    min_words: int = 8,
    enable_presidio: bool = False,
    english_only: bool = True,
    strip_emojis: bool = True,
) -> CleanReview | None:
    """Preprocess a single raw review.

    Returns CleanReview if the review passes length/content/language filters, or None if dropped.
    """
    normalized_text = normalize_text(raw.text, remove_emojis=strip_emojis)
    normalized_title = normalize_text(raw.title, remove_emojis=strip_emojis) if raw.title else None

    # Length filter: must have strictly more than min_words words
    if not should_keep_review(normalized_text, min_words=min_words, remove_emojis=strip_emojis):
        return None

    # PII scrubbing
    clean_title, clean_text, pii_hits = scrub_review_fields(
        normalized_title,
        normalized_text,
        enable_presidio=enable_presidio,
    )

    # Language detection and filtering
    lang = detect_language(clean_text)
    if english_only and not is_english(clean_text, lang):
        return None

    # Pseudonymize ID and drop store review_id
    rid = generate_rid(raw.source, raw.review_id)

    # Enrichment
    review_date, iso_week = enrich_review_metadata(raw.date)

    return CleanReview(
        rid=rid,
        source=raw.source,
        rating=raw.rating,
        clean_title=clean_title,
        clean_text=clean_text,
        date=review_date,
        iso_week=iso_week,
        app_version=raw.app_version,
        helpful_votes=raw.helpful_votes,
        lang=lang,
        pii_hits=pii_hits,
    )


def process_and_persist_reviews(
    raw_reviews: list[RawReview],
    target_week: str,
    n_weeks: int,
    config: AppConfig,
    run_store: RunStore,
    llm_client: LLMClient | None = None,
    coverage_start: dict[Source, date] | None = None,
    compute_embeddings: bool = True,
) -> list[CleanReview]:
    """Execute complete Phase 2 preprocessing and persistence pipeline."""
    min_words = config.ingest.min_words
    english_only = config.ingest.english_only
    strip_emojis = config.ingest.strip_emojis

    logger.info(
        "preprocessing_start",
        raw_count=len(raw_reviews),
        min_words=min_words,
        english_only=english_only,
        strip_emojis=strip_emojis,
    )

    # Step 1: Clean, scrub, pseudonymize, and enrich
    cleaned_reviews: list[CleanReview] = []
    for raw in raw_reviews:
        clean = process_single_review(
            raw,
            min_words=min_words,
            enable_presidio=config.privacy.enable_presidio,
            english_only=english_only,
            strip_emojis=strip_emojis,
        )
        if clean is not None:
            cleaned_reviews.append(clean)

    logger.info(
        "preprocessing_filtered",
        retained=len(cleaned_reviews),
        dropped=len(raw_reviews) - len(cleaned_reviews),
    )

    # Step 2: Accumulate weekly snapshots
    # Group newly cleaned reviews by (source, iso_week)
    grouped: dict[tuple[Source, str], list[CleanReview]] = defaultdict(list)
    for r in cleaned_reviews:
        grouped[(r.source, r.iso_week)].append(r)

    # Save snapshots for each week/source, merging with any existing snapshot
    for (src, wk), new_revs in grouped.items():
        existing = run_store.load_snapshot(src, wk)
        seen_rids = {r.rid for r in existing}
        merged = list(existing)
        for r in new_revs:
            if r.rid not in seen_rids:
                seen_rids.add(r.rid)
                merged.append(r)
        run_store.save_snapshot(src, wk, merged)

    # Step 3: Merge stored snapshots for weeks the live fetch didn't reach (App Store)
    all_clean_reviews = list(cleaned_reviews)
    seen_rids = {r.rid for r in all_clean_reviews}

    if config.sources.app_store.accumulate_weekly:
        cov_start = coverage_start.get("app_store") if coverage_start else None
        missing_weeks = missing_snapshot_weeks(cov_start, target_week, n_weeks)
        if missing_weeks:
            logger.info("loading_app_store_snapshots", missing_weeks=missing_weeks)
            stored_app_store = run_store.load_snapshots_for_weeks("app_store", missing_weeks)
            for r in stored_app_store:
                if r.rid not in seen_rids:
                    seen_rids.add(r.rid)
                    all_clean_reviews.append(r)

    # Step 4: Persist clean reviews for the weekly run
    run_store.save_clean_reviews(target_week, all_clean_reviews)
    logger.info("clean_reviews_saved", count=len(all_clean_reviews), week=target_week)

    # Step 5: Generate and cache embeddings
    if compute_embeddings and llm_client is not None and all_clean_reviews:
        cache_dir = run_store.base_dir / "cache"
        logger.info("generating_embeddings", count=len(all_clean_reviews))
        embed_reviews(all_clean_reviews, llm_client, cache_dir=cache_dir)

    return all_clean_reviews
