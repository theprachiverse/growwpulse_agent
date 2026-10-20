"""Task 3.5: End-to-end Theming Pipeline.

Chains together:
1. Review loading from data/runs/<week>/reviews_clean.jsonl
2. Dense embedding loading / generation with cache
3. Over-clustering & stratified sampling (Task 3.1)
4. LLM theme discovery & hard cap normalization (Task 3.2)
5. Batched classification with 1-to-1 completeness & centroid fallback (Task 3.3)
6. Multi-dimensional ranking & WoW delta tracking (Task 3.4)
7. Artifact persistence to themes.json & assignments.jsonl (Task 3.5)
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pulse.config import AppConfig
from pulse.models import CleanReview, Theme
from pulse.observability.logging import get_logger
from pulse.preprocess.embeddings import embed_reviews
from pulse.run_store import RunStore
from pulse.theming.classify import classify_reviews
from pulse.theming.cluster import cluster_and_sample
from pulse.theming.discover import discover_themes
from pulse.theming.models import ReviewAssignment, ThemeSpec
from pulse.theming.rank import rank_themes
from pulse.theming.residual import residual_pass
from pulse.utils.weeks import date_to_iso_week, week_start_date

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.theming.pipeline")


def get_previous_week_string(week: str) -> str:
    """Calculate the ISO week string immediately preceding the given week."""
    monday = week_start_date(week)
    prev_monday = monday - timedelta(days=7)
    return date_to_iso_week(prev_monday)


def run_theming_pipeline(
    week: str,
    config: AppConfig,
    run_store: RunStore,
    llm_client: LLMClient,
    source_weighting: str | None = None,
    k_clusters: int = 7,
    batch_size: int = 50,
    dry_run: bool = False,
) -> tuple[list[Theme], list[ReviewAssignment]]:
    """Execute complete Phase 3 theming workflow on cleaned reviews.

    Args:
        week: Target ISO week string (e.g. '2026-W42').
        config: Application configuration.
        run_store: Persistence store for artifacts.
        llm_client: Client for embeddings and chat completions.
        source_weighting: Optional override for 'volume' | 'balanced' ranking mode.
        k_clusters: Number of clusters for KMeans over-clustering.
        batch_size: Review count per batch for LLM classification.

    Returns:
        tuple[list[Theme], list[ReviewAssignment]]:
            - List of ranked Theme models
            - List of ReviewAssignment records for all reviews
    """
    logger.info("theming_pipeline_start", week=week)

    # 1. Load cleaned reviews
    reviews: list[CleanReview] = run_store.load_clean_reviews(week)
    if not reviews:
        raise ValueError(
            f"No clean reviews found for week '{week}' in {run_store.run_dir(week)}. "
            "Please run 'pulse preprocess' first."
        )

    logger.info("loaded_clean_reviews", count=len(reviews), week=week)

    # 2. Dense embeddings (with on-disk cache)
    cache_dir = run_store.base_dir / "cache"
    embeddings = embed_reviews(reviews, llm_client, cache_dir=cache_dir)
    logger.info("embeddings_ready", count=len(embeddings))

    # 3. Previous week themes for naming consistency
    prev_week = get_previous_week_string(week)
    prev_themes = run_store.load_themes(prev_week)
    if prev_themes:
        logger.info("loaded_prior_themes_anchor", prev_week=prev_week, count=len(prev_themes))

    # 4. Over-clustering & Stratified Sampling (3.1)
    cluster_samples, _cluster_centroids = cluster_and_sample(
        reviews=reviews,
        embeddings=embeddings,
        k=k_clusters,
        random_state=42,
        samples_per_cluster=15,
    )

    # 5. Theme Discovery & Normalization (3.2)
    discovered_specs: list[ThemeSpec] = discover_themes(
        cluster_samples=cluster_samples,
        seed_taxonomy=config.theming.seed_taxonomy,
        previous_themes=prev_themes,
        llm_client=llm_client,
        max_themes=config.theming.max_themes,
    )

    # 6. Batched Classification with Centroid Fallback (3.3)
    assignments = classify_reviews(
        reviews=reviews,
        review_embeddings=embeddings,
        themes=discovered_specs,
        llm_client=llm_client,
        batch_size=batch_size,
        cache_dir=cache_dir,
    )

    # 6b. Residual pass: recover a missed product domain from a large 'other' bucket
    if config.theming.residual_pass:
        discovered_specs, assignments = residual_pass(
            reviews=reviews,
            embeddings=embeddings,
            themes=discovered_specs,
            assignments=assignments,
            llm_client=llm_client,
            max_themes=config.theming.max_themes,
            threshold=config.theming.residual_other_threshold,
            min_capture=config.theming.residual_min_capture,
            batch_size=batch_size,
            cache_dir=cache_dir,
        )

    # 7. Multi-dimensional Ranking & Trend Tracking (3.4)
    ranking_mode = source_weighting or config.ranking.source_weighting
    ranked_themes = rank_themes(
        themes=discovered_specs,
        assignments=assignments,
        source_weighting=ranking_mode,
        previous_themes=prev_themes,
    )

    # 8. Persistence (3.5)
    run_store.save_themes(week, ranked_themes)
    run_store.save_assignments(week, assignments)
    logger.info(
        "theming_pipeline_persisted",
        themes_count=len(ranked_themes),
        assignments_count=len(assignments),
        week=week,
    )

    return ranked_themes, assignments
