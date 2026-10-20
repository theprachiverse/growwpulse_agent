"""Task 4.5: Word Budget Control & Shortening Pass.

Enforces the <= 250 words limit on the pulse body (excluding the top header metadata line).
If over budget, runs up to 2 shorten passes on theme definitions and action rationales only.
Quotes are strictly locked and never modified.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pulse.compose.models import ShortenResponse
from pulse.llm.budget import BudgetExceededError
from pulse.models import ActionIdea, Theme
from pulse.observability.logging import get_logger
from pulse.theming.discover import load_prompt_template

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.compose.shorten")


def count_pulse_body_words(markdown: str) -> int:
    """Count words in pulse markdown body, strictly excluding top header metadata lines.

    Lines up to and including the *Window: ...* metadata line are ignored.
    """
    lines = markdown.strip().splitlines()
    body_lines: list[str] = []
    found_header_end = False

    for line in lines:
        stripped = line.strip()
        if not found_header_end:
            # Skip # title and *Window...* line
            if stripped.startswith("# ") or stripped.startswith("*Window:"):
                continue
            if not stripped:
                continue
            # When we hit the first section heading (## Top Themes), body starts
            found_header_end = True

        if found_header_end:
            body_lines.append(line)

    body_text = "\n".join(body_lines)
    return len(body_text.split())


def deterministic_shorten(text: str, max_words: int) -> str:
    """Concise algorithmic fallback to shorten definitions or rationales to sentence/clause boundaries."""
    words = text.strip().split()
    if len(words) <= max_words:
        return text

    # Try to cut at first sentence
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if sentences and len(sentences[0].split()) <= max_words:
        return sentences[0].strip()

    # Otherwise slice words and terminate with period
    sliced = " ".join(words[:max_words]).rstrip(",;:-")
    if not sliced.endswith("."):
        sliced += "."
    return sliced


def run_shorten_pass(
    top_themes: list[Theme],
    actions: list[ActionIdea],
    current_word_count: int,
    max_words: int,
    llm_client: LLMClient,
) -> tuple[list[Theme], list[ActionIdea]]:
    """Execute one shorten pass using LLM (or deterministic fallback) on definitions and rationales."""
    logger.info(
        "executing_word_budget_shorten_pass",
        current_words=current_word_count,
        target_max_words=max_words,
    )

    system_prompt = load_prompt_template(
        "shorten.md",
        fallback_text="Condense theme definitions and action rationales. Quotes are strictly locked.",
    )

    theme_lines = "\n".join(
        f'- Theme {t.theme_id} ({t.name}): "{t.definition}"' for t in top_themes
    )
    action_lines = "\n".join(
        f'- Action for {a.theme_id} ({a.title}): "{a.rationale}"' for a in actions
    )

    user_prompt = (
        f"The current pulse body has {current_word_count} words, which exceeds the limit of {max_words} words.\n\n"
        f"Please aggressively condense the following definitions and rationales.\n"
        f"DO NOT TOUCH QUOTES. Focus strictly on definitions and rationales.\n\n"
        f"### Theme Definitions to Condense:\n{theme_lines}\n\n"
        f"### Action Rationales to Condense:\n{action_lines}\n\n"
        "Return valid JSON matching the ShortenResponse schema."
    )

    shortened_themes: dict[str, str] = {}
    shortened_actions: dict[str, str] = {}

    try:
        response = llm_client.complete_json(
            prompt=user_prompt,
            schema=ShortenResponse,
            temperature=0.0,
            system_prompt=system_prompt,
        )

        for t_item in response.themes:
            if t_item.shortened_definition.strip():
                shortened_themes[t_item.theme_id] = t_item.shortened_definition.strip()

        for a_item in response.actions:
            if a_item.shortened_rationale.strip():
                shortened_actions[a_item.theme_id] = a_item.shortened_rationale.strip()
    except BudgetExceededError:
        raise
    except Exception as exc:
        logger.warning("llm_shorten_pass_failed_using_algorithmic_fallback", error=str(exc))

    # Apply shortened text or deterministic fallback
    new_themes: list[Theme] = []
    for t in top_themes:
        new_def = shortened_themes.get(t.theme_id) or deterministic_shorten(
            t.definition, max_words=12
        )
        new_themes.append(t.model_copy(update={"definition": new_def}))

    new_actions: list[ActionIdea] = []
    for a in actions:
        new_rat = shortened_actions.get(a.theme_id) or deterministic_shorten(
            a.rationale, max_words=18
        )
        new_actions.append(a.model_copy(update={"rationale": new_rat}))

    return new_themes, new_actions
