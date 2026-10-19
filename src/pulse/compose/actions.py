"""Task 4.3: Action Ideas Generation.

Generates 3 concrete, evidence-grounded, Groww-specific action ideas (one per top theme)
with clear ownership hints ('Product', 'Support', 'Engineering', 'Growth') and strict brevity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pulse.compose.models import ActionIdeasResponse
from pulse.models import ActionIdea, Quote, Theme
from pulse.observability.logging import get_logger
from pulse.theming.discover import load_prompt_template

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.compose.actions")

FALLBACK_ACTIONS: dict[
    str, tuple[str, str, Literal["Product", "Support", "Engineering", "Growth"]]
] = {
    "app stability": (
        "Biometric Session Renewal & Keep-Alive",
        "Implement non-blocking biometric re-auth and background session keep-alive to eliminate crashes during app switching.",
        "Engineering",
    ),
    "trading": (
        "Stop-Loss Order Pre-Validation & Chart Sync",
        "Introduce order pre-validation checks and sync TradingView indicators to prevent order rejection panics during peak volume.",
        "Product",
    ),
    "support": (
        "AI Chatbot Human-Handoff Fast-Track",
        "Introduce a 2-prompt loop detection threshold in the support bot to route users directly to human executives.",
        "Support",
    ),
    "charges": (
        "Pre-Order Brokerage & Tax Breakdown",
        "Display an itemized breakdown of brokerage, STT, and exchange charges on the order confirmation screen.",
        "Product",
    ),
    "payment": (
        "Instant UPI Deposit Status Polling",
        "Add automated NPCI payment polling with instant in-app status updates for pending fund transfers.",
        "Engineering",
    ),
}


def get_fallback_action(theme: Theme) -> ActionIdea:
    """Provide a high-quality domain fallback action for a theme if LLM fails."""
    t_name_lower = theme.name.lower()
    for key, (title, rationale, owner) in FALLBACK_ACTIONS.items():
        if key in t_name_lower:
            return ActionIdea(
                title=title,
                rationale=rationale,
                theme_id=theme.theme_id,
                owner_hint=owner,
            )

    # General default grounded in theme definition
    return ActionIdea(
        title=f"Targeted Remediation for {theme.name}",
        rationale=f"Audit root causes behind {theme.review_count} user reviews and ship targeted sprint fixes for {theme.definition.lower()}",
        theme_id=theme.theme_id,
        owner_hint="Product",
    )


def generate_action_ideas(
    top_themes: list[Theme],
    selected_quotes: list[Quote],
    llm_client: LLMClient,
) -> list[ActionIdea]:
    """Generate 3 concrete action ideas corresponding to top 3 themes using LLM."""
    if len(top_themes) != 3:
        raise ValueError(
            f"Expected exactly 3 top themes for action generation, got {len(top_themes)}"
        )

    system_prompt = load_prompt_template(
        "actions.md",
        fallback_text="Generate 3 concrete, Groww-specific action ideas, one per top theme.",
    )

    quote_by_theme = {q.theme_id: q for q in selected_quotes}

    theme_prompts: list[str] = []
    for t in top_themes:
        quote = quote_by_theme.get(t.theme_id)
        quote_text = (
            f'"{quote.text}" ({quote.rating}*, {quote.source})' if quote else "No quote selected."
        )
        wow_text = f" ({t.wow_delta:+.1f} pts WoW)" if t.wow_delta is not None else ""
        theme_prompts.append(
            f"### Theme {t.theme_id}: {t.name}\n"
            f"- Definition: {t.definition}\n"
            f"- Volume: {t.review_count} reviews ({t.share * 100:.1f}% of total{wow_text})\n"
            f"- Severity: {t.low_rating_share * 100:.1f}% are 1-2 star complaints\n"
            f"- Stores: Play Store: {t.by_source.get('play_store', 0)}, App Store: {t.by_source.get('app_store', 0)}\n"
            f"- Representative User Quote: {quote_text}"
        )

    user_prompt = (
        "Generate exactly 3 actionable initiatives tailored for Groww leadership—one for each theme below:\n\n"
        + "\n\n".join(theme_prompts)
        + "\n\nRequirements:\n"
        "- Action title (4-8 words)\n"
        "- Crisp rationale (1-2 sentences, ~20-30 words)\n"
        "- Exactly one owner_hint per action from: Product, Support, Engineering, Growth\n"
        "- Output strictly matching the ActionIdeasResponse schema."
    )

    actions: list[ActionIdea] = []
    try:
        response = llm_client.complete_json(
            prompt=user_prompt,
            schema=ActionIdeasResponse,
            temperature=0.3,
            system_prompt=system_prompt,
        )

        for item in response.actions:
            actions.append(
                ActionIdea(
                    title=item.title.strip(),
                    rationale=item.rationale.strip(),
                    theme_id=item.theme_id,
                    owner_hint=item.owner_hint,
                )
            )
    except Exception as exc:
        logger.warning("llm_action_generation_failed_using_fallback", error=str(exc))

    # Validate and fill missing actions if any
    theme_ids = [t.theme_id for t in top_themes]
    existing_themes = {a.theme_id: a for a in actions if a.theme_id in theme_ids}

    final_actions: list[ActionIdea] = []
    for t in top_themes:
        if t.theme_id in existing_themes:
            final_actions.append(existing_themes[t.theme_id])
        else:
            final_actions.append(get_fallback_action(t))

    return final_actions[:3]
