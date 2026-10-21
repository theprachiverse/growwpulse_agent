"""End-to-End Pulse Composition & Guardrails Pipeline.

Chains together:
1. Loading cleaned reviews, ranked themes, and assignments from RunStore
2. Retrieving diverse quote candidates per top theme (Task 4.1)
3. Span-based verbatim quote selection with source-mix balancing (Task 4.2)
4. Concrete, evidence-grounded action idea generation (Task 4.3)
5. Jinja2 executive rendering to pulse.md and Pulse model (Task 4.4)
6. Word budget control with shortening passes (Task 4.5)
7. Comprehensive guardrails validation & eval reporting (Task 4.6)
8. Artifact persistence to pulse.json, pulse.md, and eval_report.json
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from pulse.compose.actions import generate_action_ideas
from pulse.compose.quotes import retrieve_quote_candidates, select_quotes_with_llm
from pulse.compose.render import render_pulse
from pulse.compose.shorten import CLAMP_STAGES, clamp_fields, run_shorten_pass
from pulse.config import AppConfig
from pulse.guardrails.validators import ValidationReport, validate_pulse
from pulse.models import CleanReview, Pulse, Theme
from pulse.observability.logging import get_logger
from pulse.preprocess.embeddings import embed_reviews
from pulse.run_store import RunStore
from pulse.utils.weeks import window_bounds

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.compose.pipeline")


def _hard_unrepairable(report: ValidationReport) -> list[str]:
    """Hard checks that shortening cannot fix (the word limit is repairable; soft checks never block)."""
    return [
        name
        for name, ok in report.checks.items()
        if not ok and name not in {"word_limit", "source_coverage", "tone"}
    ]


def run_compose_pipeline(
    week: str,
    config: AppConfig,
    run_store: RunStore,
    llm_client: LLMClient,
    dry_run: bool = False,
    max_repair_attempts: int = 2,
    persist: bool = True,
) -> tuple[Pulse, ValidationReport]:
    """Execute complete Phase 4 pulse composition and guardrails validation workflow.

    Args:
        week: Target ISO week string (e.g. '2026-W42').
        config: Application configuration.
        run_store: Run artifact store.
        llm_client: Client for LLM completions.
        dry_run: Accepted for callers that pass the run's flag; a dry run still saves its pulse
            (it only skips publishing). Use `persist` to skip writing to disk.
        max_repair_attempts: Max retry/shorten passes if validation checks fail.
        persist: If False, nothing is written to the run directory (used by `pulse compose --dry-run`).

    Returns:
        tuple[Pulse, ValidationReport]: Generated pulse and guardrail validation report.
    """
    logger.info("compose_pipeline_start", week=week)

    # 1. Load clean reviews
    clean_reviews: list[CleanReview] = run_store.load_clean_reviews(week)
    if not clean_reviews:
        raise ValueError(
            f"No clean reviews found for week '{week}'. Please run 'pulse preprocess' first."
        )

    # 2. Load ranked themes
    themes: list[Theme] | None = run_store.load_themes(week)
    if not themes:
        raise ValueError(
            f"No ranked themes found for week '{week}'. Please run 'pulse theme' first."
        )

    min_reviews = config.ingest.min_reviews
    if len(clean_reviews) < min_reviews and config.ingest.skip_on_low_volume:
        raise ValueError(
            f"Low volume: {len(clean_reviews)} clean reviews (< {min_reviews}); "
            "skipping because ingest.skip_on_low_volume is true."
        )

    if len(themes) < 3:
        raise ValueError(f"At least 3 themes required for composition, found {len(themes)}.")

    top_themes = themes[:3]
    logger.info("selected_top_themes", themes=[t.name for t in top_themes])

    # 3. Load assignments
    assignments = run_store.load_assignments(week)
    rid_to_theme = {a["rid"]: a["theme_id"] for a in assignments}

    # Group reviews by theme_id
    reviews_by_theme: dict[str, list[CleanReview]] = {t.theme_id: [] for t in top_themes}
    for r in clean_reviews:
        t_id = rid_to_theme.get(r.rid)
        if t_id in reviews_by_theme:
            reviews_by_theme[t_id].append(r)

    # 4. Dense embeddings for candidate proximity scoring
    cache_dir = run_store.base_dir / "cache"
    embeddings_list = embed_reviews(clean_reviews, llm_client, cache_dir=cache_dir)
    review_embeddings = {r.rid: emb for r, emb in zip(clean_reviews, embeddings_list, strict=False)}

    # 5. Retrieve candidate reviews per top theme (Task 4.1)
    candidates_by_theme = {}
    for t in top_themes:
        theme_revs = reviews_by_theme.get(t.theme_id, [])
        # Mean embedding of the theme's reviews: lets candidates be ranked by how on-topic they
        # are, not only by upvotes and star rating.
        vecs = [review_embeddings[r.rid] for r in theme_revs if r.rid in review_embeddings]
        theme_centroid = (
            np.mean(np.array(vecs, dtype=np.float32), axis=0).tolist() if vecs else None
        )
        cands = retrieve_quote_candidates(
            theme_id=t.theme_id,
            reviews=theme_revs,
            theme_centroid=theme_centroid,
            review_embeddings=review_embeddings,
            min_words=config.compose.quote_words[0],
            max_words=config.compose.quote_words[1],
            target_count=10,
        )
        candidates_by_theme[t.theme_id] = cands

    # 6. Span-based quote selection (Task 4.2)
    selected_quotes = select_quotes_with_llm(
        top_themes=top_themes,
        candidates_by_theme=candidates_by_theme,
        llm_client=llm_client,
    )

    # 7. Action ideas generation (Task 4.3)
    actions = generate_action_ideas(
        top_themes=top_themes,
        selected_quotes=selected_quotes,
        llm_client=llm_client,
    )

    # Window bounds
    window_weeks = config.ingest.window_weeks
    window_start, window_end = window_bounds(week, window_weeks)

    # 8. Render pulse (Task 4.4)
    _rendered_md, pulse = render_pulse(
        week=week,
        window_start=window_start,
        window_end=window_end,
        window_weeks=window_weeks,
        reviews=clean_reviews,
        top_themes=top_themes,
        quotes=selected_quotes,
        actions=actions,
        low_volume_threshold=min_reviews,
    )

    # 9. Word budget control & shorten pass (Task 4.5)
    # Shorten down to target_words (below the hard cap) so small later edits don't breach it.
    max_words = config.compose.max_words
    target_words = config.compose.target_words

    def _shorten_once(current: Pulse) -> Pulse:
        shortened_themes, shortened_actions = run_shorten_pass(
            top_themes=current.top_themes,
            actions=current.actions,
            current_word_count=current.word_count,
            max_words=target_words,
            llm_client=llm_client,
        )
        _md, shortened = render_pulse(
            week=week,
            window_start=window_start,
            window_end=window_end,
            window_weeks=window_weeks,
            reviews=clean_reviews,
            top_themes=shortened_themes,
            quotes=current.quotes,  # strictly locked!
            actions=shortened_actions,
            low_volume_threshold=min_reviews,
        )
        return shortened

    attempts = 0
    while pulse.word_count > target_words and attempts < max_repair_attempts:
        attempts += 1
        logger.info(
            "pulse_exceeds_target_word_budget_initiating_shorten_pass",
            word_count=pulse.word_count,
            target_words=target_words,
            max_words=max_words,
            attempt=attempts,
        )
        pulse = _shorten_once(pulse)

    # 10. Guardrails validation (Task 4.6)
    report = validate_pulse(
        pulse=pulse,
        all_clean_reviews=clean_reviews,
        all_themes=themes,
        config=config,
    )

    # Repair loop: only the word limit can be repaired automatically (by shortening).
    # Any other hard failure (quotes, PII, grounding, attribution) would give the same
    # result on re-validation, so fail immediately instead of looping.
    repair_attempt = 0
    while not report.passed and repair_attempt < max_repair_attempts:
        hard_unrepairable = _hard_unrepairable(report)
        if hard_unrepairable:
            logger.error(
                "guardrails_failed_unrepairable_checks",
                checks=hard_unrepairable,
                failures=report.hard_failures,
            )
            break

        repair_attempt += 1
        logger.warning(
            "guardrails_word_limit_failed_initiating_repair",
            attempt=repair_attempt,
            word_count=pulse.word_count,
        )
        pulse = _shorten_once(pulse)
        report = validate_pulse(
            pulse=pulse,
            all_clean_reviews=clean_reviews,
            all_themes=themes,
            config=config,
        )

    # Last resort: the LLM shorten passes can leave the body over the cap, because quotes are locked
    # and titles/stats are fixed. If the word limit is the only hard failure, clamp definitions and
    # rationales harder in stages until the pulse fits.
    if not report.passed and not _hard_unrepairable(report):
        for definition_words, rationale_words in CLAMP_STAGES:
            clamped_themes, clamped_actions = clamp_fields(
                pulse.top_themes, pulse.actions, definition_words, rationale_words
            )
            _md, pulse = render_pulse(
                week=week,
                window_start=window_start,
                window_end=window_end,
                window_weeks=window_weeks,
                reviews=clean_reviews,
                top_themes=clamped_themes,
                quotes=pulse.quotes,  # strictly locked!
                actions=clamped_actions,
                low_volume_threshold=min_reviews,
            )
            logger.warning(
                "guardrails_word_limit_clamped",
                definition_words=definition_words,
                rationale_words=rationale_words,
                word_count=pulse.word_count,
            )
            report = validate_pulse(
                pulse=pulse,
                all_clean_reviews=clean_reviews,
                all_themes=themes,
                config=config,
            )
            if report.passed:
                break

    # 11. Persistence
    if not persist:
        logger.info("compose_persistence_skipped", week=week, passed=report.passed)
    elif report.passed:
        run_store.save_pulse(week, pulse)
        run_store.save_eval_report(week, report.summary_dict())
        logger.info("pulse_and_eval_report_persisted", week=week)
    else:
        # Save eval report noting validation failure
        run_store.save_eval_report(week, report.summary_dict())
        logger.error("pulse_failed_guardrails_not_saving_pulse", week=week)

    return pulse, report
