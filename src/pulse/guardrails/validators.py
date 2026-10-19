"""Task 4.6: Guardrails & Validators.

Implements all hard and soft compliance checks from architecture section 4.5:
- Hard checks: theme count, quote count, verbatim exact substring, zero PII,
  word budget (<= 250 words), action grounding, no internal IDs (rid_*), source attribution.
- Soft checks: source coverage, professional tone.

Produces structured ValidationReport and blocks publishing if any hard check fails.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pulse.config import AppConfig
from pulse.models import CleanReview, Pulse, Theme
from pulse.observability.logging import get_logger
from pulse.preprocess.pii import scrub_pii

logger = get_logger("pulse.guardrails")


class ValidationReport(BaseModel):
    """Result of comprehensive pulse guardrail checks."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(description="True if all hard checks passed; False otherwise")
    hard_failures: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)
    word_count: int
    max_words_allowed: int
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def summary_dict(self) -> dict[str, Any]:
        """Convert report to dictionary suitable for JSON serialization in eval_report.json."""
        return {
            "passed": self.passed,
            "hard_failures": self.hard_failures,
            "soft_warnings": self.soft_warnings,
            "checks": self.checks,
            "word_count": self.word_count,
            "max_words_allowed": self.max_words_allowed,
            "validated_at": self.validated_at.isoformat(),
        }


def validate_pulse(
    pulse: Pulse,
    all_clean_reviews: list[CleanReview],
    all_themes: list[Theme],
    config: AppConfig,
) -> ValidationReport:
    """Run all Phase 4 hard and soft guardrail checks against the generated pulse."""
    hard_failures: list[str] = []
    soft_warnings: list[str] = []
    checks: dict[str, bool] = {}

    reviews_by_rid = {r.rid: r for r in all_clean_reviews}
    theme_ids_all = {t.theme_id for t in all_themes}

    # 1. Theme count check (Hard)
    # len(top_themes) == 3, 1 <= len(all_themes) <= 5, all top themes belong to discovered themes
    top_t_ids = {t.theme_id for t in pulse.top_themes}
    theme_count_ok = (
        len(pulse.top_themes) == 3
        and 1 <= len(all_themes) <= config.theming.max_themes
        and top_t_ids.issubset(theme_ids_all)
    )
    checks["theme_count"] = theme_count_ok
    if not theme_count_ok:
        hard_failures.append(
            f"Theme count check failed: pulse has {len(pulse.top_themes)} top themes, "
            f"expected 3 matching discovered themes (total themes: {len(all_themes)})."
        )

    # 2. Quote count check (Hard)
    # Exactly 3 quotes, exactly 1 quote per top theme
    quote_themes = {q.theme_id for q in pulse.quotes}
    quote_count_ok = len(pulse.quotes) == 3 and quote_themes == top_t_ids
    checks["quote_count"] = quote_count_ok
    if not quote_count_ok:
        hard_failures.append(
            f"Quote count check failed: pulse has {len(pulse.quotes)} quotes with themes {quote_themes}, "
            f"expected exactly 3 corresponding to top themes {top_t_ids}."
        )

    # 3. Verbatim exact-substring check (Hard)
    verbatim_ok = True
    for idx, q in enumerate(pulse.quotes, start=1):
        rev = reviews_by_rid.get(q.rid)
        if not rev:
            verbatim_ok = False
            hard_failures.append(f"Quote {idx} references unknown review ID '{q.rid}'.")
        elif q.text not in rev.clean_text:
            verbatim_ok = False
            hard_failures.append(
                f"Quote {idx} is NOT an exact substring of review '{q.rid}'.\n"
                f'  Quote text: "{q.text}"\n'
                f'  Original clean_text: "{rev.clean_text}"'
            )
    checks["verbatim"] = verbatim_ok

    # 4. PII re-scan check (Hard)
    # Re-scan rendered markdown with every regex recognizer
    _, pii_hits = scrub_pii(pulse.markdown)
    pii_ok = len(pii_hits) == 0
    checks["pii"] = pii_ok
    if not pii_ok:
        hard_failures.append(
            f"PII re-scan check failed: detected {len(pii_hits)} sensitive PII pattern hits: {pii_hits}."
        )

    # 5. Word limit check (Hard)
    # Target <= config.compose.max_words (default 250) on pulse body
    from pulse.compose.shorten import count_pulse_body_words

    body_words = count_pulse_body_words(pulse.markdown)
    max_words = config.compose.max_words
    word_limit_ok = body_words <= max_words
    checks["word_limit"] = word_limit_ok
    if not word_limit_ok:
        hard_failures.append(
            f"Word limit check failed: pulse body has {body_words} words, exceeding maximum of {max_words} words."
        )

    # 6. Action count and grounding check (Hard)
    # Exactly 3 actions, each matching a top theme
    action_themes = {a.theme_id for a in pulse.actions}
    actions_grounded_ok = len(pulse.actions) == 3 and action_themes == top_t_ids
    checks["action_grounding"] = actions_grounded_ok
    if not actions_grounded_ok:
        hard_failures.append(
            f"Action grounding check failed: pulse has {len(pulse.actions)} actions for themes {action_themes}, "
            f"expected exactly 3 corresponding to top themes {top_t_ids}."
        )

    # 7. No identifiers check (Hard)
    # Ensure no internal pseudonym review IDs or hash identifiers appear in rendered markdown
    no_ids_ok = "rid_" not in pulse.markdown and not re.search(r"\b[0-9a-f]{20,}\b", pulse.markdown)
    checks["no_identifiers"] = no_ids_ok
    if not no_ids_ok:
        hard_failures.append(
            "No identifiers check failed: detected 'rid_' or raw hash tokens in published markdown."
        )

    # 8. Source attribution check (Hard)
    # Each quote's source matches its review, and the quote's own rendered line names that store
    source_attr_ok = True
    md_lines = pulse.markdown.splitlines()
    for idx, q in enumerate(pulse.quotes, start=1):
        rev = reviews_by_rid.get(q.rid)
        if rev and q.source != rev.source:
            source_attr_ok = False
            hard_failures.append(
                f"Quote {idx} source mismatch: quote labelled '{q.source}' but review origin is '{rev.source}'."
            )
        expected_label = "Play Store" if q.source == "play_store" else "App Store"
        quote_lines = [ln for ln in md_lines if ln.lstrip().startswith(">") and q.text in ln]
        if quote_lines and not any(expected_label in ln for ln in quote_lines):
            source_attr_ok = False
            hard_failures.append(
                f"Quote {idx} missing proper '{expected_label}' attribution on its own line."
            )
    checks["source_attribution"] = source_attr_ok

    # 8b. Quote rules (Hard): word length within bounds, 3 distinct reviews
    min_w, max_w = config.compose.quote_words
    quote_rules_ok = True
    for idx, q in enumerate(pulse.quotes, start=1):
        n_words = len(q.text.split())
        if not (min_w <= n_words <= max_w):
            quote_rules_ok = False
            hard_failures.append(
                f"Quote {idx} has {n_words} words; allowed range is {min_w}-{max_w}."
            )
    if len({q.rid for q in pulse.quotes}) != len(pulse.quotes):
        quote_rules_ok = False
        hard_failures.append("Quotes must come from 3 distinct reviews; found a repeated review.")
    checks["quote_rules"] = quote_rules_ok

    # --- Soft checks ---
    # 9. Source coverage (Soft)
    play_count = pulse.reviews_by_source.get("play_store", 0)
    app_count = pulse.reviews_by_source.get("app_store", 0)
    source_cov_ok = play_count > 0 and app_count > 0
    checks["source_coverage"] = source_cov_ok
    if not source_cov_ok:
        soft_warnings.append(
            f"Source coverage warning: Play Store has {play_count} reviews, App Store has {app_count} reviews."
        )

    # 10. Tone / Professionalism (Soft)
    # Simple heuristic checks for professional executive tone
    unprofessional_tokens = ["omg", "lol", "wtf", "lmao", "bruh", "screwed", "cheater"]
    lower_md = pulse.markdown.lower()
    tone_hits = [t for t in unprofessional_tokens if re.search(rf"\b{re.escape(t)}\b", lower_md)]
    tone_ok = len(tone_hits) == 0
    checks["tone"] = tone_ok
    if not tone_ok:
        soft_warnings.append(
            f"Tone warning: potentially casual tokens detected in pulse text: {tone_hits}"
        )

    passed = len(hard_failures) == 0
    report = ValidationReport(
        passed=passed,
        hard_failures=hard_failures,
        soft_warnings=soft_warnings,
        checks=checks,
        word_count=body_words,
        max_words_allowed=max_words,
    )

    if passed:
        logger.info(
            "guardrails_validation_passed",
            word_count=body_words,
            soft_warnings_count=len(soft_warnings),
        )
    else:
        logger.error(
            "guardrails_validation_failed",
            failures_count=len(hard_failures),
            failures=hard_failures,
        )

    return report
