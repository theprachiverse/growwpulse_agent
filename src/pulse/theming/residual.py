"""Residual theme pass: recover a missed product domain from the 'other' bucket.

Runs after classification when 'other' is too large and a theme slot is still free. One LLM
call proposes at most one extra theme from a sample of the unclassified complaints; only the
'other' reviews are then re-classified against the extended theme list. The result is kept
only if the new theme captures enough reviews, otherwise the original result stands.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from pulse.llm.budget import BudgetExceededError
from pulse.models import CleanReview
from pulse.observability.logging import get_logger
from pulse.theming.classify import classify_reviews
from pulse.theming.cluster import truncate_preview
from pulse.theming.discover import load_prompt_template
from pulse.theming.models import ReviewAssignment, ThemeSpec

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.theming.residual")

OTHER = "other"
SAMPLE_SIZE = 40
TOP_HELPFUL = 15


class ResidualThemeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="True only if one coherent new product domain exists")
    theme: ThemeSpec | None = None


def other_share(assignments: list[ReviewAssignment]) -> float:
    if not assignments:
        return 0.0
    return sum(1 for a in assignments if a.theme_id == OTHER) / len(assignments)


def _sample_other(others: list[CleanReview]) -> list[CleanReview]:
    """Prefer complaints/neutral reviews (<= 3 stars): generic praise is legitimately 'other'."""
    pool = [r for r in others if r.rating <= 3] or others
    ranked = sorted(pool, key=lambda r: (-r.helpful_votes, r.rid))
    top = ranked[:TOP_HELPFUL]
    rest = ranked[TOP_HELPFUL:]
    rng = random.Random(42)
    extra = rng.sample(rest, min(SAMPLE_SIZE - len(top), len(rest)))
    return top + sorted(extra, key=lambda r: r.rid)


def discover_residual_theme(
    others: list[CleanReview],
    themes: list[ThemeSpec],
    llm_client: LLMClient,
) -> ThemeSpec | None:
    sample = _sample_other(others)
    existing = "\n".join(f"- {t.theme_id}: {t.name} - {t.definition}" for t in themes)
    reviews = "\n".join(
        f"- ({r.source}, {r.rating}*, helpful={r.helpful_votes}) "
        f'"{truncate_preview(r.clean_text, 45, 250)}"'
        for r in sample
    )
    new_id = f"t{len(themes) + 1}"
    system_prompt = load_prompt_template(
        "discover_residual_theme.md",
        fallback_text="You are an expert product analytics agent for Groww.",
    )
    user_prompt = (
        f"### Existing themes\n{existing}\n\n"
        f"### Sample of {len(sample)} unclassified reviews (out of {len(others)})\n{reviews}\n\n"
        f"Use theme_id `{new_id}` if you propose a theme."
    )
    result = llm_client.complete_json(
        prompt=user_prompt,
        schema=ResidualThemeResult,
        temperature=0.0,
        system_prompt=system_prompt,
    )
    if not result.found or result.theme is None:
        return None
    return result.theme.model_copy(update={"theme_id": new_id})


def residual_pass(
    reviews: list[CleanReview],
    embeddings: list[list[float]],
    themes: list[ThemeSpec],
    assignments: list[ReviewAssignment],
    llm_client: LLMClient,
    *,
    max_themes: int,
    threshold: float,
    min_capture: float,
    batch_size: int = 50,
    cache_dir: Path | str | None = None,
    classify_client: LLMClient | None = None,
    max_llm_reviews: int = 0,
) -> tuple[list[ThemeSpec], list[ReviewAssignment]]:
    """Returns (themes, assignments); unchanged unless the residual theme is accepted.

    `llm_client` names the new theme; re-sorting the 'other' bucket uses `classify_client`
    (its own quota) when given, capped at `max_llm_reviews` (the rest fall back to centroids).
    """
    share = other_share(assignments)
    if share <= threshold or len(themes) >= max_themes:
        return themes, assignments

    other_rids = {a.rid for a in assignments if a.theme_id == OTHER}
    idx = [i for i, r in enumerate(reviews) if r.rid in other_rids]
    others = [reviews[i] for i in idx]
    other_embs = [embeddings[i] for i in idx]

    try:
        new_theme = discover_residual_theme(others, themes, llm_client)
    except BudgetExceededError as exc:
        logger.warning("residual_discovery_skipped_budget_exceeded", error=str(exc))
        return themes, assignments
    except Exception as exc:
        logger.warning("residual_discovery_failed", error=str(exc))
        return themes, assignments
    if new_theme is None:
        logger.info("residual_no_new_theme", other_share=round(share, 4))
        return themes, assignments

    extended = [*themes, new_theme]
    redone = classify_reviews(
        reviews=others,
        review_embeddings=other_embs,
        themes=extended,
        llm_client=classify_client or llm_client,
        batch_size=batch_size,
        request_delay_s=0.0,
        cache_dir=cache_dir,
        max_llm_reviews=max_llm_reviews,
    )
    by_rid = {a.rid: a for a in redone}
    merged = [by_rid.get(a.rid, a) if a.theme_id == OTHER else a for a in assignments]

    captured = sum(1 for a in merged if a.theme_id == new_theme.theme_id)
    if captured / len(merged) < min_capture:
        logger.info(
            "residual_theme_rejected",
            theme=new_theme.name,
            captured=captured,
            needed=round(min_capture * len(merged)),
        )
        return themes, assignments

    logger.info(
        "residual_theme_accepted",
        theme=new_theme.name,
        captured=captured,
        other_before=round(share, 4),
        other_after=round(other_share(merged), 4),
    )
    return extended, merged
