"""Data contracts for the Groww Review Pulse Agent.

Follows the schema defined in architecture.md section 6.
Typed with Pydantic v2.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["play_store", "app_store"]


class RawReview(BaseModel):
    """In-memory representation of a review as fetched from a source.

    Never persisted to disk or sent to the LLM (contains unscrubbed text,
    original store review IDs, and is prior to PII stripping).
    """

    model_config = ConfigDict(extra="forbid")

    source: Source
    review_id: str
    rating: int = Field(ge=1, le=5, description="Star rating 1..5")
    title: str | None = Field(default=None, description="App Store only; None for Play Store")
    text: str = Field(description="Raw review body")
    date: datetime = Field(description="Review timestamp (UTC)")
    app_version: str | None = Field(default=None, description="App version if reported")
    helpful_votes: int = Field(
        default=0,
        ge=0,
        description="Play thumbsUpCount / App Store voteSum",
    )


class CleanReview(BaseModel):
    """Cleaned, pseudonymized review persisted in data/runs/<week>/reviews_clean.jsonl.

    This represents the privacy boundary: zero PII, pseudonymous rid,
    and no author identity.
    """

    model_config = ConfigDict(extra="forbid")

    rid: str = Field(description="Pseudonymous ID: rid_<sha256(source:review_id)[:10]>")
    source: Source
    rating: int = Field(ge=1, le=5)
    clean_title: str | None = Field(default=None, description="PII-scrubbed title (App Store only)")
    clean_text: str = Field(description="PII-scrubbed body")
    date: date
    iso_week: str = Field(description="ISO week string, e.g. 2026-W40")
    app_version: str | None = None
    helpful_votes: int = Field(default=0, ge=0)
    lang: str = Field(default="en", description="Detected language tag (e.g. en, hi)")
    pii_hits: list[str] = Field(
        default_factory=list,
        description="Types of PII detected and replaced (e.g. ['PHONE', 'EMAIL'])",
    )


class Theme(BaseModel):
    """A discovered/classified review theme."""

    model_config = ConfigDict(extra="forbid")

    theme_id: str = Field(description="Short theme ID, e.g. t1..t5")
    name: str = Field(description="Short, specific theme name")
    definition: str = Field(description="1-2 sentence description of what belongs in this theme")
    review_count: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0, description="Share of total classified reviews (0..1)")
    avg_rating: float = Field(ge=1.0, le=5.0)
    low_rating_share: float = Field(ge=0.0, le=1.0, description="Share of 1-2 star reviews")
    wow_delta: float | None = Field(
        default=None,
        description="Week-over-week difference in share (percentage points), if prior run exists",
    )
    by_source: dict[Source, int] = Field(
        default_factory=dict,
        description="Counts broken down per source, e.g. {'play_store': 640, 'app_store': 85}",
    )


class Quote(BaseModel):
    """A verbatim quote selected for the weekly pulse."""

    model_config = ConfigDict(extra="forbid")

    rid: str = Field(description="Internal pseudonym rid; never rendered in final note")
    source: Source = Field(description="Rendered as 'Play Store' or 'App Store'")
    theme_id: str
    text: str = Field(description="Exact substring of clean_text")
    rating: int = Field(ge=1, le=5)
    month: str = Field(description="Formatted month/year, e.g. 'Oct 2026'")


class ActionIdea(BaseModel):
    """A concrete product/ops action linked to a top theme."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="Actionable title")
    rationale: str = Field(description="1-2 sentence rationale grounded in review findings")
    theme_id: str = Field(description="Must match a top-3 theme ID")
    owner_hint: Literal["Product", "Support", "Engineering", "Growth"]


class Pulse(BaseModel):
    """The complete weekly pulse note data contract."""

    model_config = ConfigDict(extra="forbid")

    week: str = Field(description="ISO week, e.g. 2026-W42")
    window_start: date
    window_end: date
    total_reviews: int = Field(ge=0)
    reviews_by_source: dict[Source, int]
    avg_rating_by_source: dict[Source, float]
    avg_rating: float = Field(ge=1.0, le=5.0)
    top_themes: Annotated[list[Theme], Field(min_length=3, max_length=3)]
    quotes: Annotated[list[Quote], Field(min_length=3, max_length=3)]
    actions: Annotated[list[ActionIdea], Field(min_length=3, max_length=3)]
    markdown: str = Field(description="Rendered markdown of the pulse")
    word_count: int = Field(ge=0, description="Body word count (target <= 250 words)")


class StepRecord(BaseModel):
    """Outcome of one MCP publish step (Doc create / Doc append / email)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "done", "unknown", "failed"] = "pending"
    request_id: str | None = None
    at: datetime | None = None
    error: str | None = None


class DocAppendRecord(StepRecord):
    content_sha256: str | None = None


class EmailRecord(StepRecord):
    mode: Literal["draft", "send"] | None = None
    draft_id: str | None = None
    message_id: str | None = None
    thread_id: str | None = None


class PublishState(BaseModel):
    """Per-week publish record (idempotency source of truth; never holds content)."""

    model_config = ConfigDict(extra="forbid")

    week: str
    doc_id: str | None = None
    doc_url: str | None = None
    doc_append: DocAppendRecord = Field(default_factory=DocAppendRecord)
    email: EmailRecord = Field(default_factory=EmailRecord)


class RunManifest(BaseModel):
    """Audit log and state tracking for a pipeline execution."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="Unique run ID, e.g. run_2026W42_1713000000")
    week: str = Field(description="Target ISO week, e.g. 2026-W42")
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["success", "failed_validation", "failed_publish", "error"]
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="Counts at each pipeline stage (fetched, clean, classified, etc.)",
    )
    coverage_start: dict[Source, date] = Field(
        default_factory=dict,
        description="Oldest review date reached per source",
    )
    source_errors: dict[Source, str] = Field(
        default_factory=dict,
        description="Per-source errors if partial failure occurred",
    )
    llm_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage metrics (e.g. {'tokens_prompt': 1200, 'tokens_completion': 400})",
    )
    doc_id: str | None = None
    doc_url: str | None = None
    draft_id: str | None = None
    publish: PublishState | None = None
    eval_summary: dict[str, bool] = Field(default_factory=dict)
