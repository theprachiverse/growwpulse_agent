"""Unit tests for Pydantic data contracts (Task 0.3)."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from pulse.models import (
    ActionIdea,
    CleanReview,
    Pulse,
    Quote,
    RawReview,
    RunManifest,
    Theme,
)


def test_raw_review_valid() -> None:
    review = RawReview(
        source="play_store",
        review_id="gp_12345",
        rating=5,
        title=None,
        text="Fast app, love the mutual fund tracking!",
        date=datetime.now(UTC),
        app_version="5.12.0",
        helpful_votes=3,
    )
    assert review.rating == 5
    assert review.source == "play_store"
    assert review.helpful_votes == 3


def test_raw_review_rating_out_of_bounds() -> None:
    with pytest.raises(ValidationError):
        RawReview(
            source="play_store",
            review_id="1",
            rating=6,  # invalid rating
            text="invalid",
            date=datetime.now(UTC),
        )

    with pytest.raises(ValidationError):
        RawReview(
            source="play_store",
            review_id="1",
            rating=0,  # invalid rating
            text="invalid",
            date=datetime.now(UTC),
        )


def test_clean_review_contract() -> None:
    clean = CleanReview(
        rid="rid_a1b2c3d4e5",
        source="app_store",
        rating=1,
        clean_title="KYC delay",
        clean_text="My verification has been pending for days. Called [PHONE].",
        date=date(2026, 10, 10),
        iso_week="2026-W41",
        app_version="5.12.1",
        helpful_votes=2,
        lang="en",
        pii_hits=["PHONE"],
    )
    assert clean.rid.startswith("rid_")
    assert clean.pii_hits == ["PHONE"]


def test_theme_contract() -> None:
    theme = Theme(
        theme_id="t1",
        name="Withdrawals & Payouts",
        definition="Delays or failures during bank withdrawal and UPI payout requests.",
        review_count=150,
        share=0.25,
        avg_rating=2.1,
        low_rating_share=0.68,
        wow_delta=0.04,
        by_source={"play_store": 130, "app_store": 20},
    )
    assert theme.share == 0.25
    assert theme.by_source["play_store"] == 130


def test_quote_and_action_contracts() -> None:
    quote = Quote(
        rid="rid_abc1234567",
        source="play_store",
        theme_id="t1",
        text="Money was deducted from my bank but not added to Groww balance for 24 hours.",
        rating=1,
        month="Oct 2026",
    )
    assert quote.theme_id == "t1"
    assert quote.rating == 1

    action = ActionIdea(
        title="Automated UPI refund status webhooks",
        rationale="Provide instant status feedback for pending UPI transactions within 15 minutes.",
        theme_id="t1",
        owner_hint="Engineering",
    )
    assert action.owner_hint == "Engineering"


def test_pulse_exact_3_constraints() -> None:
    # Pulse requires exactly 3 themes, 3 quotes, 3 actions
    theme = Theme(
        theme_id="t1",
        name="Withdrawals",
        definition="Def",
        review_count=10,
        share=0.2,
        avg_rating=2.0,
        low_rating_share=0.5,
    )
    quote = Quote(
        rid="rid_1",
        source="play_store",
        theme_id="t1",
        text="Quote text",
        rating=1,
        month="Oct 2026",
    )
    action = ActionIdea(
        title="Action 1",
        rationale="Rationale",
        theme_id="t1",
        owner_hint="Product",
    )

    # Valid with exactly 3 each
    pulse = Pulse(
        week="2026-W42",
        window_start=date(2026, 8, 4),
        window_end=date(2026, 10, 11),
        total_reviews=100,
        reviews_by_source={"play_store": 80, "app_store": 20},
        avg_rating_by_source={"play_store": 3.8, "app_store": 4.1},
        avg_rating=3.86,
        top_themes=[theme, theme, theme],
        quotes=[quote, quote, quote],
        actions=[action, action, action],
        markdown="# Pulse Note",
        word_count=180,
    )
    assert len(pulse.top_themes) == 3
    assert len(pulse.quotes) == 3
    assert len(pulse.actions) == 3

    # Reject if less than 3
    with pytest.raises(ValidationError):
        Pulse(
            week="2026-W42",
            window_start=date(2026, 8, 4),
            window_end=date(2026, 10, 11),
            total_reviews=100,
            reviews_by_source={"play_store": 80, "app_store": 20},
            avg_rating_by_source={"play_store": 3.8, "app_store": 4.1},
            avg_rating=3.86,
            top_themes=[theme, theme],  # only 2!
            quotes=[quote, quote, quote],
            actions=[action, action, action],
            markdown="# Pulse Note",
            word_count=180,
        )


def test_run_manifest_contract() -> None:
    manifest = RunManifest(
        run_id="run_2026W42_001",
        week="2026-W42",
        started_at=datetime.now(UTC),
        status="success",
        counts={"play_store_fetched": 200, "clean": 190},
    )
    assert manifest.status == "success"
    assert manifest.counts["clean"] == 190
