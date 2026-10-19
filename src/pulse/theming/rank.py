"""Task 3.4: Multi-dimensional Ranking & Trend Tracking.

Calculates aggregated metrics (volume, share, avg_rating, low_rating_share, by_source, wow_delta)
and ranks themes under volume or balanced modes with severity and helpful-vote tie-breakers.
Strictly excludes 'other' from top 3 themes.
"""

from __future__ import annotations

from pulse.models import Source, Theme
from pulse.observability.logging import get_logger
from pulse.theming.models import ReviewAssignment, ThemeSpec

logger = get_logger("pulse.theming.rank")


def calculate_wow_delta(
    current_share: float,
    theme_name: str,
    previous_themes: list[Theme] | None,
) -> float | None:
    """Calculate Week-over-Week delta in percentage points against prior week's theme."""
    if not previous_themes:
        return None

    # Match by name (exact or case-insensitive)
    t_name_lower = theme_name.strip().lower()
    for prev in previous_themes:
        if prev.name.strip().lower() == t_name_lower or prev.name.lower() in t_name_lower:
            # Difference in percentage points, e.g. 0.24 - 0.19 = +5.0 points
            return round((current_share - prev.share) * 100.0, 1)

    return None


def rank_themes(
    themes: list[ThemeSpec],
    assignments: list[ReviewAssignment],
    source_weighting: str = "volume",
    previous_themes: list[Theme] | None = None,
) -> list[Theme]:
    """Rank themes by volume or balanced weighting with severity tie-breakers.

    Args:
        themes: Discovered theme specifications.
        assignments: All review assignments.
        source_weighting: 'volume' (default) or 'balanced' (mean of per-source shares).
        previous_themes: Prior run themes for WoW delta calculation.

    Returns:
        list[Theme]: Ranked list of Theme models (top 3 guaranteed non-other).
    """
    total_assignments = len(assignments)
    if total_assignments == 0:
        return []

    # Count total reviews per source for balanced weighting
    total_play = sum(1 for a in assignments if a.source == "play_store")
    total_app = sum(1 for a in assignments if a.source == "app_store")

    # Group assignments by theme_id
    grouped: dict[str, list[ReviewAssignment]] = {t.theme_id: [] for t in themes}
    grouped["other"] = []
    for a in assignments:
        if a.theme_id in grouped:
            grouped[a.theme_id].append(a)
        else:
            grouped["other"].append(a)

    theme_metrics: list[tuple[Theme, float, float, int]] = []

    for spec in themes:
        t_revs = grouped.get(spec.theme_id, [])
        count = len(t_revs)
        share = count / total_assignments if total_assignments > 0 else 0.0

        if count > 0:
            avg_rating = round(sum(r.rating for r in t_revs) / count, 2)
            low_rating_cnt = sum(1 for r in t_revs if r.rating <= 2)
            low_rating_share = round(low_rating_cnt / count, 4)
            helpful_sum = sum(r.helpful_votes for r in t_revs)
        else:
            avg_rating = 3.0
            low_rating_share = 0.0
            helpful_sum = 0

        by_source: dict[Source, int] = {
            "play_store": sum(1 for r in t_revs if r.source == "play_store"),
            "app_store": sum(1 for r in t_revs if r.source == "app_store"),
        }

        wow_delta = calculate_wow_delta(share, spec.name, previous_themes)

        theme_model = Theme(
            theme_id=spec.theme_id,
            name=spec.name,
            definition=spec.definition,
            review_count=count,
            share=round(share, 4),
            avg_rating=avg_rating,
            low_rating_share=low_rating_share,
            wow_delta=wow_delta,
            by_source=by_source,
        )

        # Primary sort metric based on source_weighting
        if source_weighting == "balanced":
            play_share = by_source["play_store"] / total_play if total_play > 0 else 0.0
            app_share = by_source["app_store"] / total_app if total_app > 0 else 0.0
            primary_score = 0.5 * (play_share + app_share)
        else:
            primary_score = share

        theme_metrics.append((theme_model, primary_score, low_rating_share, helpful_sum))

    # Sort descending by:
    # 1. Primary score (volume share or balanced share)
    # 2. low_rating_share (severity signal)
    # 3. helpful_votes sum (consensus signal)
    theme_metrics.sort(
        key=lambda item: (item[1], item[2], item[3]),
        reverse=True,
    )

    ranked_themes = [item[0] for item in theme_metrics]

    logger.info(
        "theming_ranking_complete",
        weighting=source_weighting,
        top_themes=[(t.name, t.review_count, t.share) for t in ranked_themes[:3]],
        other_count=len(grouped.get("other", [])),
    )

    return ranked_themes
