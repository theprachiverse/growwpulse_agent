"""Data models for Phase 4: Pulse Composition & Guardrails."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from pulse.models import Source


class QuoteCandidate(BaseModel):
    """Candidate review for verbatim quote selection."""

    model_config = ConfigDict(extra="forbid")

    rid: str
    source: Source
    theme_id: str
    rating: int = Field(ge=1, le=5)
    clean_text: str
    clean_title: str | None = None
    date: date
    helpful_votes: int = 0
    distance_to_centroid: float = 0.0
    word_count: int = 0


class QuoteSpanSelection(BaseModel):
    """Span-based selection coordinates within candidate review clean_text."""

    model_config = ConfigDict(extra="forbid")

    rid: str = Field(description="Selected review pseudonym rid")
    start_char: int = Field(ge=0, description="0-based start character index in clean_text")
    end_char: int = Field(gt=0, description="0-based end character index in clean_text")


class ThemeQuoteSelection(BaseModel):
    """Selected quote span for a specific theme."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(description="Theme ID (e.g. t1..t5)")
    selected_span: QuoteSpanSelection


class QuoteSelectionResponse(BaseModel):
    """Structured LLM response for quote selection across top-3 themes."""

    model_config = ConfigDict(extra="forbid")

    quotes: Annotated[
        list[ThemeQuoteSelection],
        Field(
            min_length=3, max_length=3, description="Exactly 3 quote selections, one per top theme"
        ),
    ]


class ActionIdeaItem(BaseModel):
    """A concrete product or ops action item proposed by LLM."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(description="Associated top theme ID")
    title: str = Field(description="Concise, action-oriented title")
    rationale: str = Field(description="1-2 sentence evidence-grounded rationale specific to Groww")
    owner_hint: Literal["Product", "Support", "Engineering", "Growth"] = Field(
        description="Functional owner for this action"
    )


class ActionIdeasResponse(BaseModel):
    """Structured LLM response for action ideas across top-3 themes."""

    model_config = ConfigDict(extra="forbid")

    actions: Annotated[
        list[ActionIdeaItem],
        Field(min_length=3, max_length=3, description="Exactly 3 action ideas, one per top theme"),
    ]


class ShortenedThemeItem(BaseModel):
    """Shortened definition for a theme during word budget control."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str
    shortened_definition: str


class ShortenedActionItem(BaseModel):
    """Shortened rationale for an action item during word budget control."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str
    shortened_rationale: str


class ShortenResponse(BaseModel):
    """Structured LLM response for the word-budget shortening pass."""

    model_config = ConfigDict(extra="forbid")

    themes: list[ShortenedThemeItem] = Field(default_factory=list)
    actions: list[ShortenedActionItem] = Field(default_factory=list)
