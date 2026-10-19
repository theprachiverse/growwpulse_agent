"""Task 4.4: Pulse Markdown and JSON Rendering.

Renders executive pulse document using Jinja2 template with accurate store aggregates,
partial coverage annotations, verbatim quotes, and grounded action ideas.
Constructs the validated Pydantic Pulse data contract.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from pulse.compose.shorten import count_pulse_body_words
from pulse.models import ActionIdea, CleanReview, Pulse, Quote, Source, Theme

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def get_template_env() -> Environment:
    """Initialize Jinja2 environment."""
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def compute_store_statistics(
    reviews: list[CleanReview],
    window_weeks: int,
) -> tuple[dict[Source, int], dict[Source, float], str | None]:
    """Calculate review counts, average ratings per store, and detect partial coverage."""
    play_revs = [r for r in reviews if r.source == "play_store"]
    app_revs = [r for r in reviews if r.source == "app_store"]

    counts: dict[Source, int] = {
        "play_store": len(play_revs),
        "app_store": len(app_revs),
    }

    play_avg = round(sum(r.rating for r in play_revs) / len(play_revs), 2) if play_revs else 3.0
    app_avg = round(sum(r.rating for r in app_revs) / len(app_revs), 2) if app_revs else 3.0

    avgs: dict[Source, float] = {
        "play_store": play_avg,
        "app_store": app_avg,
    }

    # Detect if App Store coverage is partial across the window
    partial_coverage: str | None = None
    if app_revs:
        app_weeks = sorted({r.iso_week for r in app_revs})
        # If app store reviews span fewer weeks than configured window_weeks
        if len(app_weeks) < window_weeks:
            first_w = app_weeks[0].split("-")[-1]
            last_w = app_weeks[-1].split("-")[-1]
            partial_coverage = f"{first_w}–{last_w}"

    return counts, avgs, partial_coverage


def render_pulse(
    week: str,
    window_start: date,
    window_end: date,
    window_weeks: int,
    reviews: list[CleanReview],
    top_themes: list[Theme],
    quotes: list[Quote],
    actions: list[ActionIdea],
    low_volume_threshold: int = 0,
) -> tuple[str, Pulse]:
    """Render pulse markdown and build the complete validated Pulse model."""
    if len(top_themes) != 3:
        raise ValueError(f"Pulse requires exactly 3 top themes, got {len(top_themes)}")
    if len(quotes) != 3:
        raise ValueError(f"Pulse requires exactly 3 quotes, got {len(quotes)}")
    if len(actions) != 3:
        raise ValueError(f"Pulse requires exactly 3 action ideas, got {len(actions)}")

    counts, avgs, partial_coverage = compute_store_statistics(reviews, window_weeks)
    total_revs = len(reviews)
    overall_avg = round(sum(r.rating for r in reviews) / total_revs, 2) if total_revs > 0 else 3.0

    theme_name_by_id = {t.theme_id: t.name for t in top_themes}

    env = get_template_env()
    template = env.get_template("pulse.md.j2")

    rendered_md = (
        template.render(
            week=week,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            window_weeks=window_weeks,
            play_count=counts["play_store"],
            play_avg=avgs["play_store"],
            app_count=counts["app_store"],
            app_avg=avgs["app_store"],
            partial_coverage=partial_coverage,
            low_volume=total_revs < low_volume_threshold,
            total_reviews=total_revs,
            top_themes=top_themes,
            quotes=quotes,
            actions=actions,
            theme_name_by_id=theme_name_by_id,
        ).strip()
        + "\n"
    )

    word_count = count_pulse_body_words(rendered_md)

    pulse = Pulse(
        week=week,
        window_start=window_start,
        window_end=window_end,
        total_reviews=total_revs,
        reviews_by_source=counts,
        avg_rating_by_source=avgs,
        avg_rating=overall_avg,
        top_themes=top_themes,
        quotes=quotes,
        actions=actions,
        markdown=rendered_md,
        word_count=word_count,
    )

    return rendered_md, pulse
