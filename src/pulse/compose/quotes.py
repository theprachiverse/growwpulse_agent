"""Tasks 4.1 & 4.2: Quote Candidate Retrieval & Span-based Verbatim Quote Selection.

Retrieves diverse candidate reviews per top theme (ranking, severity, store stratification,
length filtering, PII/profanity guard), runs structured LLM span selection, enforces
verbatim exact-substring guarantees, and applies the soft cross-store source-mix rule.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np

from pulse.compose.models import (
    QuoteCandidate,
    QuoteSelectionResponse,
)
from pulse.models import CleanReview, Quote, Theme
from pulse.observability.logging import get_logger
from pulse.theming.discover import load_prompt_template

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

logger = get_logger("pulse.compose.quotes")

# Basic profanity filter for candidate safety
PROFANITY_PATTERN = re.compile(
    r"\b(fuck|shit|bitch|bastard|asshole|scamster|fraudster|chutiya|bakwas|mc|bc)\b",
    re.IGNORECASE,
)


def filter_candidate_review(
    review: CleanReview,
    min_words: int = 8,
    max_words: int = 40,
) -> bool:
    """Check if review qualifies as a clean, concise quote candidate."""
    words = review.clean_text.split()
    word_cnt = len(words)
    if word_cnt < min_words or word_cnt > max_words:
        return False

    # Discard if profanity present
    if PROFANITY_PATTERN.search(review.clean_text):
        return False

    # Discard if heavily scrubbed (more than 1 PII hit or multiple placeholders)
    if len(review.pii_hits) > 1:
        return False

    return review.clean_text.count("[") <= 1


def retrieve_quote_candidates(
    theme_id: str,
    reviews: list[CleanReview],
    theme_centroid: list[float] | None = None,
    review_embeddings: dict[str, list[float]] | None = None,
    min_words: int = 8,
    max_words: int = 40,
    target_count: int = 10,
) -> list[QuoteCandidate]:
    """Retrieve up to ~10 diverse quote candidates for a theme.

    Balances:
    - High helpful votes (consensus)
    - High semantic alignment (closest to theme centroid)
    - Rating diversity (low 1-2★, mid 3★, high 4-5★)
    - Source diversity (guarantees App Store representation when available)
    """
    candidates: list[QuoteCandidate] = []
    c_vec = np.array(theme_centroid, dtype=np.float32) if theme_centroid else None
    c_norm = np.linalg.norm(c_vec) if c_vec is not None and len(c_vec) > 0 else 0.0

    for r in reviews:
        if not filter_candidate_review(r, min_words=min_words, max_words=max_words):
            continue

        words = r.clean_text.split()
        word_cnt = len(words)

        dist = 0.0
        if c_vec is not None and c_norm > 0 and review_embeddings and r.rid in review_embeddings:
            r_vec = np.array(review_embeddings[r.rid], dtype=np.float32)
            r_norm = np.linalg.norm(r_vec)
            if r_norm > 0:
                # Cosine distance: 1 - cosine_similarity
                dist = float(1.0 - np.dot(r_vec / r_norm, c_vec / c_norm))

        candidates.append(
            QuoteCandidate(
                rid=r.rid,
                source=r.source,
                theme_id=theme_id,
                rating=r.rating,
                clean_text=r.clean_text,
                clean_title=r.clean_title,
                date=r.date,
                helpful_votes=r.helpful_votes,
                distance_to_centroid=round(dist, 4),
                word_count=word_cnt,
            )
        )

    if not candidates:
        return []

    # Scoring metric: prioritize helpful upvotes, centroid proximity, and 1-2 star severity
    def score_candidate(c: QuoteCandidate) -> float:
        severity_bonus = 25.0 if c.rating <= 2 else 0.0
        # Lower distance to centroid is better
        proximity_bonus = max(0.0, 1.0 - c.distance_to_centroid) * 20.0
        return float(c.helpful_votes) + severity_bonus + proximity_bonus

    # Partition by source to guarantee App Store representation
    play_cands = [c for c in candidates if c.source == "play_store"]
    app_cands = [c for c in candidates if c.source == "app_store"]

    play_cands.sort(key=score_candidate, reverse=True)
    app_cands.sort(key=score_candidate, reverse=True)

    selected: list[QuoteCandidate] = []
    # If app store candidates exist, ensure at least 2-3 are included
    num_app = min(3, len(app_cands))
    selected.extend(app_cands[:num_app])

    # Fill remaining from play store
    remaining_needed = target_count - len(selected)
    selected.extend(play_cands[:remaining_needed])

    # If still below target, take remaining app candidates
    if len(selected) < target_count and len(app_cands) > num_app:
        selected.extend(app_cands[num_app : target_count - len(selected) + num_app])

    # Final sort by score
    selected.sort(key=score_candidate, reverse=True)
    return selected[:target_count]


def extract_verbatim_span(
    candidate: QuoteCandidate,
    start_char: int,
    end_char: int,
    min_words: int = 8,
    max_words: int = 40,
) -> str:
    """Extract a verbatim substring from candidate review text, with boundary trimming."""
    text = candidate.clean_text
    text_len = len(text)

    # Sanity bounds check
    start = max(0, min(start_char, text_len - 1))
    end = max(start + 1, min(end_char, text_len))

    # Slice raw text
    span = text[start:end]

    # Trim leading/trailing whitespace and surrounding quotation marks
    # Note: Using strip preserves substring relationship when matched against original indices
    trimmed_span = span.strip(" \t\n\r\"'")

    # Ensure trimmed span is still an exact substring
    if trimmed_span and trimmed_span in text:
        words = trimmed_span.split()
        if min_words <= len(words) <= max_words:
            return trimmed_span

    # If the span was slightly out of bounds or sliced a word, fallback to whole candidate clean_text
    words = text.split()
    if min_words <= len(words) <= max_words:
        return text

    # Or take first complete sentence if within word count
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        s_clean = sentence.strip(" \"'")
        s_words = s_clean.split()
        if min_words <= len(s_words) <= max_words and s_clean in text:
            return s_clean

    return text


def balance_quote_sources(
    selected_quotes: list[Quote],
    candidate_pool_by_theme: dict[str, list[QuoteCandidate]],
) -> list[Quote]:
    """Enforce soft source-mix rule: ensure cross-store representation if candidates exist.

    If all 3 quotes come from one store and an eligible candidate exists from the other,
    swap the lowest-upvoted quote with the top candidate from the underrepresented store.
    """
    if len(selected_quotes) != 3:
        return selected_quotes

    sources = {q.source for q in selected_quotes}
    if len(sources) > 1:
        # Already mixed
        return selected_quotes

    current_source = selected_quotes[0].source
    other_source = "app_store" if current_source == "play_store" else "play_store"

    # Find best eligible candidate from other_source across any of the 3 themes
    best_candidate: QuoteCandidate | None = None
    target_theme_id: str | None = None

    for t_id, cands in candidate_pool_by_theme.items():
        alt_cands = [c for c in cands if c.source == other_source]
        if alt_cands:
            top_alt = alt_cands[0]
            if best_candidate is None or top_alt.helpful_votes > best_candidate.helpful_votes:
                best_candidate = top_alt
                target_theme_id = t_id

    if best_candidate and target_theme_id:
        logger.info(
            "applying_soft_source_mix_swap",
            replaced_theme=target_theme_id,
            new_source=other_source,
            new_rid=best_candidate.rid,
        )
        updated_quotes: list[Quote] = []
        for q in selected_quotes:
            if q.theme_id == target_theme_id:
                month_str = best_candidate.date.strftime("%b %Y")
                updated_quotes.append(
                    Quote(
                        rid=best_candidate.rid,
                        source=best_candidate.source,
                        theme_id=target_theme_id,
                        text=best_candidate.clean_text,
                        rating=best_candidate.rating,
                        month=month_str,
                    )
                )
            else:
                updated_quotes.append(q)
        return updated_quotes

    return selected_quotes


def select_quotes_with_llm(
    top_themes: list[Theme],
    candidates_by_theme: dict[str, list[QuoteCandidate]],
    llm_client: LLMClient,
) -> list[Quote]:
    """Select verbatim quotes for top 3 themes using span coordinates via LLM."""
    system_prompt = load_prompt_template(
        "select_quotes.md",
        fallback_text="Select exactly one quote span for each top theme. Return only character coordinates.",
    )

    theme_lines: list[str] = []
    cand_map: dict[str, QuoteCandidate] = {}

    for t in top_themes:
        cands = candidates_by_theme.get(t.theme_id, [])
        cand_text = []
        for c in cands:
            cand_map[c.rid] = c
            cand_text.append(
                f"- RID: {c.rid} | Source: {c.source} | Rating: {c.rating}* | Helpful: {c.helpful_votes}\n"
                f'  Text: "{c.clean_text}"'
            )
        theme_lines.append(
            f"### Theme {t.theme_id}: {t.name}\n"
            f"Definition: {t.definition}\n"
            f"Candidate Reviews:\n" + "\n".join(cand_text)
        )

    user_prompt = (
        "Select exactly one verbatim quote span for each of the following 3 themes.\n\n"
        + "\n\n".join(theme_lines)
        + "\n\nRemember: Return strictly character indices (start_char, end_char) within clean_text."
    )

    selected_quotes: list[Quote] = []
    try:
        response = llm_client.complete_json(
            prompt=user_prompt,
            schema=QuoteSelectionResponse,
            temperature=0.0,
            system_prompt=system_prompt,
        )

        for item in response.quotes:
            t_id = item.theme_id
            span_sel = item.selected_span
            candidate = cand_map.get(span_sel.rid)
            if not candidate:
                continue

            extracted_text = extract_verbatim_span(
                candidate=candidate,
                start_char=span_sel.start_char,
                end_char=span_sel.end_char,
            )

            # Ensure exact substring match
            if extracted_text not in candidate.clean_text:
                extracted_text = candidate.clean_text

            month_str = candidate.date.strftime("%b %Y")
            selected_quotes.append(
                Quote(
                    rid=candidate.rid,
                    source=candidate.source,
                    theme_id=t_id,
                    text=extracted_text,
                    rating=candidate.rating,
                    month=month_str,
                )
            )
    except Exception as exc:
        logger.warning("llm_quote_selection_failed_using_candidate_fallback", error=str(exc))

    # Check completeness: ensure each top theme has exactly 1 quote
    existing_theme_ids = {q.theme_id for q in selected_quotes}
    for t in top_themes:
        if t.theme_id not in existing_theme_ids:
            cands = candidates_by_theme.get(t.theme_id, [])
            if cands:
                top_c = cands[0]
                month_str = top_c.date.strftime("%b %Y")
                selected_quotes.append(
                    Quote(
                        rid=top_c.rid,
                        source=top_c.source,
                        theme_id=t.theme_id,
                        text=top_c.clean_text,
                        rating=top_c.rating,
                        month=month_str,
                    )
                )

    # Apply soft source-mix balancing
    balanced_quotes = balance_quote_sources(selected_quotes, candidates_by_theme)
    return balanced_quotes
