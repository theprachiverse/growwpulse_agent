"""Task 3.2: Theme Discovery & Normalization.

Synthesizes stratified cluster exemplars, seed taxonomy, and previous run themes
into at most 5 actionable, functional product themes using structured LLM generation.
Enforces hard caps via semantic similarity clustering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from pulse.models import CleanReview, Theme
from pulse.observability.logging import get_logger
from pulse.theming.cluster import truncate_preview
from pulse.theming.models import DiscoveredThemes, ThemeSpec

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.theming.discover")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "llm" / "prompts"


def load_prompt_template(filename: str, fallback_text: str) -> str:
    """Load prompt template from file, with fallback string if missing."""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return fallback_text


def format_cluster_samples_for_prompt(cluster_samples: dict[int, list[CleanReview]]) -> str:
    """Format sampled reviews per cluster for the discovery prompt."""
    lines: list[str] = []
    for c_id, exemplars in cluster_samples.items():
        lines.append(f"\n### Cluster {c_id + 1} ({len(exemplars)} representative samples):")
        for r in exemplars:
            preview = truncate_preview(r.clean_text, max_words=45, max_chars=250)
            title_part = f"[{r.clean_title}] " if r.clean_title else ""
            lines.append(
                f'- ({r.source}, {r.rating}*, helpful={r.helpful_votes}) {title_part}"{preview}"'
            )
    return "\n".join(lines)


def merge_excess_themes(
    themes: list[ThemeSpec],
    llm_client: LLMClient,
    max_themes: int = 5,
) -> list[ThemeSpec]:
    """Merge themes pairwise using cosine similarity until count <= max_themes."""
    if len(themes) <= max_themes:
        return themes

    logger.warning("excess_themes_detected", count=len(themes), target=max_themes)
    current_themes = list(themes)

    while len(current_themes) > max_themes:
        # Compute embeddings for current theme definitions
        texts = [f"{t.name}: {t.definition}" for t in current_themes]
        vectors = np.array(llm_client.embed(texts), dtype=np.float32)

        # Compute cosine similarity matrix
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        normed = vectors / norms
        sim_matrix = np.dot(normed, normed.T)

        # Mask diagonal
        np.fill_diagonal(sim_matrix, -1.0)

        # Find most similar pair
        i, j = np.unravel_index(np.argmax(sim_matrix), sim_matrix.shape)
        if i > j:
            i, j = j, i

        t1 = current_themes[i]
        t2 = current_themes[j]

        # Merge t2 into t1
        merged_inclusion = list(dict.fromkeys(t1.inclusion_criteria + t2.inclusion_criteria))
        merged_exclusion = [e for e in t1.exclusion_criteria if e not in merged_inclusion]

        merged_theme = ThemeSpec(
            theme_id=t1.theme_id,
            name=f"{t1.name} & {t2.name}",
            definition=f"{t1.definition} Also encompasses {t2.name.lower()} issues.",
            inclusion_criteria=merged_inclusion,
            exclusion_criteria=merged_exclusion,
        )

        current_themes[i] = merged_theme
        current_themes.pop(j)

    return current_themes


def discover_themes(
    cluster_samples: dict[int, list[CleanReview]],
    seed_taxonomy: list[str],
    previous_themes: list[Theme] | None,
    llm_client: LLMClient,
    max_themes: int = 5,
) -> list[ThemeSpec]:
    """Discover 3-5 functional product themes from cluster samples using LLM.

    Args:
        cluster_samples: Mapping of cluster_id -> exemplar reviews.
        seed_taxonomy: List of seed topic hints from config.
        previous_themes: Previous week's ranked themes for naming consistency.
        llm_client: LLM client supporting structured complete_json.
        max_themes: Hard ceiling on theme count (default: 5).

    Returns:
        list[ThemeSpec]: Discovered and normalized product themes.
    """
    system_prompt = load_prompt_template(
        "discover_themes.md",
        fallback_text="You are an expert product analytics agent for Groww.",
    )

    sample_text = format_cluster_samples_for_prompt(cluster_samples)
    seed_text = ", ".join(seed_taxonomy) if seed_taxonomy else "General investment app topics"

    prev_text = "None (this is the initial run)."
    if previous_themes:
        prev_text = "; ".join(f"{t.theme_id}: {t.name} ({t.definition})" for t in previous_themes)

    user_prompt = (
        f"Analyze the following review clusters from Groww users and discover between 3 and {max_themes} "
        "distinct, actionable, functional product themes.\n\n"
        f"### Seed Taxonomy Guidance:\n{seed_text}\n\n"
        f"### Previous Week Themes (for stability guidance):\n{prev_text}\n\n"
        f"### Review Clusters & Samples:\n{sample_text}\n\n"
        f"Instructions:\n"
        f"1. Produce between 3 and {max_themes} themes with short IDs t1, t2, ..., tN.\n"
        "2. Themes MUST be functional product domains (Trading/F&O, Support, Performance, Charges, etc.).\n"
        "3. Do NOT create sentiment-based buckets like 'Positive Reviews' or 'Complaints'.\n"
        "4. Include inclusion and exclusion criteria for each theme.\n"
    )

    try:
        result = llm_client.complete_json(
            prompt=user_prompt,
            schema=DiscoveredThemes,
            temperature=0.0,
            system_prompt=system_prompt,
        )
        discovered = result.themes
    except Exception as exc:
        logger.error("theme_discovery_llm_failed", error=str(exc))
        # Fallback to high-confidence seed themes if LLM fails
        discovered = [
            ThemeSpec(
                theme_id="t1",
                name="Trading & F&O Execution",
                definition="Issues related to orders, stop loss execution, charts, and trading lags.",
                inclusion_criteria=[
                    "stop loss",
                    "order rejected",
                    "tradingview",
                    "intraday",
                    "option",
                ],
                exclusion_criteria=["customer care", "account opening"],
            ),
            ThemeSpec(
                theme_id="t2",
                name="Customer Support & Escalation",
                definition="Difficulty reaching human support, AI bot loops, and ticket resolution.",
                inclusion_criteria=[
                    "customer care",
                    "ai chat",
                    "helpline",
                    "executive",
                    "call disconnect",
                ],
                exclusion_criteria=["app crash", "charges"],
            ),
            ThemeSpec(
                theme_id="t3",
                name="App Stability & Performance",
                definition="Technical glitches, freezing, crashing, and biometric login problems.",
                inclusion_criteria=[
                    "app hangs",
                    "crashing",
                    "biometric",
                    "back button",
                    "slow loading",
                ],
                exclusion_criteria=["brokerage", "kyc"],
            ),
            ThemeSpec(
                theme_id="t4",
                name="Charges & Fee Transparency",
                definition="Hidden fees, unexpected brokerage deductions, and pricing confusion.",
                inclusion_criteria=["brokerage charges", "hidden fee", "amc", "deduction", "gst"],
                exclusion_criteria=["payment gateway", "sip"],
            ),
        ]

    # Enforce hard cap
    if len(discovered) > max_themes:
        discovered = merge_excess_themes(discovered, llm_client, max_themes=max_themes)

    # Normalize theme IDs to t1..tN
    normalized: list[ThemeSpec] = []
    for idx, t in enumerate(discovered, start=1):
        normalized.append(
            ThemeSpec(
                theme_id=f"t{idx}",
                name=t.name,
                definition=t.definition,
                inclusion_criteria=t.inclusion_criteria,
                exclusion_criteria=t.exclusion_criteria,
            )
        )

    logger.info(
        "theme_discovery_completed", count=len(normalized), names=[t.name for t in normalized]
    )
    return normalized
