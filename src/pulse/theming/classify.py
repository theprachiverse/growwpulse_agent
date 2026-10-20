"""Task 3.3: Batched Classification with Centroid Fallback.

Classifies cleaned reviews into discovered themes or 'other' using batched LLM calls
with strict 1-to-1 completeness validation, rate-limiting, and dense vector centroid fallback.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pulse.llm.budget import BudgetExceededError
from pulse.models import CleanReview
from pulse.observability.logging import get_logger
from pulse.theming.cache import ClassificationCache, fingerprint, review_key
from pulse.theming.cluster import truncate_preview
from pulse.theming.discover import load_prompt_template
from pulse.theming.models import (
    BatchClassificationResult,
    ClassificationItem,
    ReviewAssignment,
    ThemeSpec,
)

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.theming.classify")


def compute_theme_centroids(
    themes: list[ThemeSpec],
    llm_client: LLMClient,
) -> dict[str, list[float]]:
    """Compute dense centroid vectors for each theme using name and definition."""
    texts = [
        f"{t.name}: {t.definition}. Keywords: {', '.join(t.inclusion_criteria)}" for t in themes
    ]
    vectors = llm_client.embed(texts)
    return {t.theme_id: vec for t, vec in zip(themes, vectors, strict=True)}


def classify_by_centroid(
    embedding: list[float],
    theme_centroids: dict[str, list[float]],
    min_similarity: float = 0.25,
) -> tuple[str, float]:
    """Fallback assignment to nearest theme centroid via cosine similarity."""
    if not theme_centroids or not embedding:
        return "other", 0.3

    rev_vec = np.array(embedding, dtype=np.float32)
    rev_norm = np.linalg.norm(rev_vec)
    if rev_norm == 0:
        return "other", 0.0
    rev_unit = rev_vec / rev_norm

    best_theme = "other"
    best_sim = -1.0

    for theme_id, centroid in theme_centroids.items():
        c_vec = np.array(centroid, dtype=np.float32)
        c_norm = np.linalg.norm(c_vec)
        if c_norm == 0:
            continue
        c_unit = c_vec / c_norm
        sim = float(np.dot(rev_unit, c_unit))
        if sim > best_sim:
            best_sim = sim
            best_theme = theme_id

    if best_sim >= min_similarity:
        return best_theme, round(best_sim, 3)
    # SVD embeddings are signed, so best_sim can be negative; keep confidence within [0, 1].
    return "other", round(min(1.0, max(0.0, 1.0 - best_sim)), 3)


def format_themes_definition(themes: list[ThemeSpec]) -> str:
    """Format theme specifications into bullet points for the classification prompt."""
    lines: list[str] = []
    for t in themes:
        inc = f" (Includes: {', '.join(t.inclusion_criteria)})" if t.inclusion_criteria else ""
        exc = f" (Excludes: {', '.join(t.exclusion_criteria)})" if t.exclusion_criteria else ""
        lines.append(f"- `{t.theme_id}`: **{t.name}** — {t.definition}{inc}{exc}")
    return "\n".join(lines)


def _prompt_text(review: CleanReview) -> str:
    """The review text exactly as the classification prompt shows it to the model."""
    return truncate_preview(review.clean_text, max_words=50, max_chars=300)


def classify_batch_with_llm(
    batch: list[CleanReview],
    themes: list[ThemeSpec],
    llm_client: LLMClient,
    system_prompt: str,
) -> dict[str, ClassificationItem]:
    """Execute LLM classification on a single batch of reviews."""
    themes_def = format_themes_definition(themes)

    reviews_text = "\n".join(
        f'- ID: {r.rid} | Text: "{truncate_preview(r.clean_text, max_words=50, max_chars=300)}"'
        for r in batch
    )

    user_prompt = (
        f"Classify each of the following {len(batch)} reviews into exactly one theme_id or 'other':\n\n"
        f"{reviews_text}\n\n"
        f"Available theme IDs: {', '.join(t.theme_id for t in themes)}, or 'other'.\n"
        "Output a valid JSON object matching the BatchClassificationResult schema."
    )

    full_system = system_prompt.replace("{themes_definition}", themes_def)
    result = llm_client.complete_json(
        prompt=user_prompt,
        schema=BatchClassificationResult,
        temperature=0.0,
        system_prompt=full_system,
    )

    valid_theme_ids = {t.theme_id for t in themes} | {"other"}
    item_map: dict[str, ClassificationItem] = {}
    for item in result.items:
        t_id = item.theme_id if item.theme_id in valid_theme_ids else "other"
        item_map[item.rid] = ClassificationItem(
            rid=item.rid,
            theme_id=t_id,
            confidence=max(0.0, min(1.0, float(item.confidence))),
        )

    return item_map


def classify_reviews(
    reviews: list[CleanReview],
    review_embeddings: list[list[float]],
    themes: list[ThemeSpec],
    llm_client: LLMClient,
    theme_centroids: dict[str, list[float]] | None = None,
    batch_size: int = 50,
    request_delay_s: float = 0.2,
    cache_dir: Path | str | None = None,
) -> list[ReviewAssignment]:
    """Classify all cleaned reviews into themes with 1-to-1 guarantee and centroid fallback.

    Args:
        reviews: List of clean reviews to classify.
        review_embeddings: Aligned embeddings for all reviews.
        themes: Discovered theme specifications.
        llm_client: LLM client for batch completion.
        theme_centroids: Optional precomputed centroid vectors for themes.
        batch_size: Batch size for LLM prompt (target 40..50).
        request_delay_s: Politeness delay between batches to respect rate limits.
        cache_dir: If set, LLM answers are cached here and reused by later runs (see cache.py);
            only reviews without a cached answer are sent to the LLM.

    Returns:
        list[ReviewAssignment]: Fully classified review assignments in input order.
    """
    if not reviews:
        return []

    # Ensure theme centroids are available for fallback
    centroids = theme_centroids or compute_theme_centroids(themes, llm_client)

    system_prompt = load_prompt_template(
        "classify.md",
        fallback_text="You are an expert review classifier for the Groww investment platform.",
    )

    cache = (
        ClassificationCache(
            cache_dir, fingerprint(themes, system_prompt, getattr(llm_client, "chat_model", ""))
        )
        if cache_dir is not None
        else None
    )

    # Answers from earlier runs; only the rest goes to the LLM, packed into full batches.
    keys = {r.rid: review_key(r.rid, _prompt_text(r)) for r in reviews}
    item_map: dict[str, ClassificationItem] = {}
    if cache is not None:
        for r in reviews:
            hit = cache.get(keys[r.rid])
            if hit is not None:
                item_map[r.rid] = hit
    todo = [r for r in reviews if r.rid not in item_map]

    total = len(reviews)
    n_batches = (len(todo) + batch_size - 1) // batch_size
    logger.info(
        "starting_batch_classification",
        total_reviews=total,
        cached=total - len(todo),
        batches=n_batches,
        batch_size=batch_size,
    )

    for b_idx in range(n_batches):
        batch = todo[b_idx * batch_size : (b_idx + 1) * batch_size]
        try:
            fresh = classify_batch_with_llm(batch, themes, llm_client, system_prompt)
        except BudgetExceededError:
            if cache is not None:
                cache.save()  # keep the progress made before the budget ran out
            raise
        except Exception as exc:
            fresh = {}
            logger.warning(
                "batch_classification_llm_failed_using_fallback",
                batch_index=b_idx + 1,
                error=str(exc),
            )
        item_map.update(fresh)
        if cache is not None:
            for rid, answer in fresh.items():
                if rid in keys:
                    cache.put(keys[rid], answer)
            cache.save()

        if b_idx < n_batches - 1 and request_delay_s > 0:
            time.sleep(request_delay_s)

    # Assemble assignments with completeness and confidence checks
    rid_to_embedding = {r.rid: emb for r, emb in zip(reviews, review_embeddings, strict=False)}
    assignments: list[ReviewAssignment] = []
    for r in reviews:
        emb = rid_to_embedding.get(r.rid, [])
        item = item_map.get(r.rid)

        if item is None or item.confidence < 0.5:
            # Centroid fallback if missing or low confidence
            theme_id, conf = classify_by_centroid(emb, centroids)
        else:
            theme_id = item.theme_id
            conf = item.confidence

        assignments.append(
            ReviewAssignment(
                rid=r.rid,
                theme_id=theme_id,
                confidence=conf,
                source=r.source,
                rating=r.rating,
                helpful_votes=r.helpful_votes,
            )
        )

    logger.info("batch_classification_completed", total_assigned=len(assignments))
    return assignments
