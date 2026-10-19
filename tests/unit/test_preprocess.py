"""Comprehensive unit tests for Phase 2 Preprocessing & PII Scrubbing.

Covers:
- Task 2.1: Cleaner, Unicode normalization, zero-width stripping, > 8 words filter.
- Task 2.2: PII scrubber with ordered recognizers, false positive guards, and zero-leak test.
- Task 2.3: Pseudonymization (rid generation).
- Task 2.4: Language & metadata enrichment.
- Task 2.5: Embeddings disk caching.
- Task 2.6: Snapshot accumulation & merging.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pulse.config import AppConfig
from pulse.llm.client import MockLLMClient
from pulse.models import CleanReview, RawReview
from pulse.preprocess.cleaner import (
    count_words,
    has_meaningful_text,
    normalize_text,
    should_keep_review,
    strip_emojis,
)
from pulse.preprocess.embeddings import (
    EmbeddingCache,
    embed_reviews,
    text_to_embed,
)
from pulse.preprocess.enrich import detect_language, enrich_review_metadata
from pulse.preprocess.pii import scrub_pii, scrub_review_fields
from pulse.preprocess.pipeline import process_and_persist_reviews, process_single_review
from pulse.preprocess.pseudonymize import generate_rid
from pulse.run_store import RunStore


# ---------------------------------------------------------------------------
# 2.1 Cleaner Tests
# ---------------------------------------------------------------------------
def test_cleaner_unicode_nfc_and_zero_width() -> None:
    # Text with zero-width space (\u200b) and unnormalized unicode
    raw = "Groww\u200b is   an \u200cawesome\ufeff  app\tfor trading\nand investments."
    cleaned = normalize_text(raw)
    assert "\u200b" not in cleaned
    assert "\u200c" not in cleaned
    assert "\ufeff" not in cleaned
    assert "  " not in cleaned
    assert cleaned == "Groww is an awesome app for trading and investments."


def test_cleaner_count_words() -> None:
    # 5 words
    assert count_words("Groww stocks and mutual funds") == 5
    # Emojis don't count as words
    assert count_words("👍 🚀 🔥") == 0
    # Mixed emojis and words
    assert count_words("Best app ever 👍 🔥 💯 for mutual funds") == 6


def test_cleaner_has_meaningful_text() -> None:
    assert has_meaningful_text("Hello") is True
    assert has_meaningful_text("   ") is False
    assert has_meaningful_text("👍 🚀 🔥") is False
    assert has_meaningful_text("!@#$%^&*()") is False


def test_cleaner_should_keep_review_strictly_more_than_8_words() -> None:
    # Exactly 8 words: should NOT keep (user requirement: "more than 8 words")
    eight_words = "One two three four five six seven eight"
    assert count_words(eight_words) == 8
    assert should_keep_review(eight_words, min_words=8) is False

    # 7 words: should NOT keep
    seven_words = "One two three four five six seven"
    assert count_words(seven_words) == 7
    assert should_keep_review(seven_words, min_words=8) is False

    # Exactly 9 words: SHOULD keep (> 8 words)
    nine_words = "One two three four five six seven eight nine"
    assert count_words(nine_words) == 9
    assert should_keep_review(nine_words, min_words=8) is True

    # 12 words: SHOULD keep
    twelve_words = "Groww is very helpful for investing in mutual funds and stocks daily"
    assert count_words(twelve_words) == 12
    assert should_keep_review(twelve_words, min_words=8) is True

    # Emoji-only: should NOT keep
    assert should_keep_review("👍 👍 👍 👍 👍 👍 👍 👍 👍 👍", min_words=8) is False


def test_cleaner_strip_emojis() -> None:
    text_with_emojis = "Super app 👍 🚀 🔥 very smooth trading 📈 and investment experience 💯"
    stripped = strip_emojis(text_with_emojis)
    assert "👍" not in stripped
    assert "🚀" not in stripped
    assert "🔥" not in stripped
    assert "📈" not in stripped
    assert "💯" not in stripped
    assert "Super app" in stripped
    assert "very smooth trading" in stripped


def test_cleaner_drop_emoji_heavy_reviews() -> None:
    # Review with only 3 words and lots of emojis: dropped
    text = "👍 👍 🚀 🔥 Good app! 💯 📈"
    assert should_keep_review(text, min_words=8, remove_emojis=True) is False


def test_language_filter_drops_non_english() -> None:
    # Gibberish / non-english review
    raw_foreign = RawReview(
        source="play_store",
        review_id="foreign_1",
        rating=2,
        text="csbaacc za d cs c dsebzasS cs d xx Vr ss w xx ss G cs ac c ex t ss b xx",
        date=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
    )
    # When english_only=True, this must be dropped
    assert process_single_review(raw_foreign, min_words=8, english_only=True) is None

    # English review with > 8 words: kept
    raw_english = RawReview(
        source="play_store",
        review_id="eng_1",
        rating=5,
        text="Groww mutual funds and stock investments are extremely convenient and fast.",
        date=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
    )
    clean = process_single_review(raw_english, min_words=8, english_only=True)
    assert clean is not None
    assert clean.lang == "en"


# ---------------------------------------------------------------------------
# 2.2 PII Scrubber Tests (Positives, Negatives, Leakage)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw_text", "expected_placeholder", "expected_type"),
    [
        # Email
        ("Reach me at user@example.com for info", "[EMAIL]", "EMAIL"),
        ("Send email to support.team@sub.domain.co.in please", "[EMAIL]", "EMAIL"),
        # UPI handle
        ("Paid via 9876543210@okaxis yesterday", "[UPI]", "UPI"),
        ("My upi id is rajesh.kumar@paytm kindly refund", "[UPI]", "UPI"),
        ("Transferred to user@upi account", "[UPI]", "UPI"),
        # Aadhaar
        ("Aadhaar 1234 5678 9012 was rejected", "[AADHAAR]", "AADHAAR"),
        ("Card 234567890123 verification pending", "[AADHAAR]", "AADHAAR"),
        # PAN
        ("Submitted PAN ABCDE1234F last week", "[PAN]", "PAN"),
        ("My pan card abcde1234f was not verified", "[PAN]", "PAN"),
        # Phone (IN)
        ("Call me on +91 9876543210 immediately", "[PHONE]", "PHONE"),
        ("Contact +91-9876543210 for support", "[PHONE]", "PHONE"),
        ("Mobile number 9876543210 failed OTP", "[PHONE]", "PHONE"),
        ("Call 98765 43210 now", "[PHONE]", "PHONE"),
        ("Dial 09876543210 please", "[PHONE]", "PHONE"),
        # IFSC
        ("IFSC is SBIN0001234 for my branch", "[IFSC]", "IFSC"),
        ("Bank code HDFC0000001 rejected transfer", "[IFSC]", "IFSC"),
        # URL
        ("Check link https://groww.in/stocks/tcs for details", "[URL]", "URL"),
        ("Visit www.groww.in to see charts", "[URL]", "URL"),
        # Generic Account Number
        ("A/C number 12345678901234 credited", "[ACCOUNT_NUMBER]", "ACCOUNT_NUMBER"),
    ],
)
def test_pii_recognizer_positives(
    raw_text: str,
    expected_placeholder: str,
    expected_type: str,
) -> None:
    scrubbed, hits = scrub_pii(raw_text)
    assert expected_placeholder in scrubbed
    assert expected_type in hits


@pytest.mark.parametrize(
    "benign_text",
    [
        "Stock price fell to ₹1,500 after market close.",
        "I invested Rs. 5000 in Tata Motors mutual fund.",
        "Total portfolio balance is 15000 INR today.",
        "Bought 500000 units in Nifty 50 ETF.",
        "App updated on 2026-10-14 without any glitch.",
        "Running on version 5.12.3 and 18.21.1 smoothly.",
        "Waited for 10 minutes in the support queue.",
    ],
)
def test_pii_false_positive_guards(benign_text: str) -> None:
    """Ensure normal financial text, dates, prices, and versions are NOT scrubbed as PII."""
    scrubbed, hits = scrub_pii(benign_text)
    assert hits == []
    assert scrubbed == benign_text


def test_pii_zero_leakage_rescan() -> None:
    """Exit criterion: running the scrubber over scrubbed text produces 0 hits."""
    text_with_pii = (
        "Hello support, my email is user.test@gmail.com and phone is +91 9876543210. "
        "I paid via user@okaxis and my PAN is ABCDE1234F. "
        "Aadhaar is 1234 5678 9012 and bank IFSC is SBIN0001234. "
        "Visit https://groww.in/faq for more details."
    )
    scrubbed, first_hits = scrub_pii(text_with_pii)
    assert len(first_hits) >= 6
    assert "[EMAIL]" in scrubbed
    assert "[PHONE]" in scrubbed
    assert "[UPI]" in scrubbed
    assert "[PAN]" in scrubbed
    assert "[AADHAAR]" in scrubbed
    assert "[IFSC]" in scrubbed
    assert "[URL]" in scrubbed

    # Re-scan scrubbed output: must produce 0 hits and zero modifications
    re_scrubbed, second_hits = scrub_pii(scrubbed)
    assert second_hits == []
    assert re_scrubbed == scrubbed


def test_scrub_review_fields_title_and_text() -> None:
    title = "Bug with email user@gmail.com"
    text = "Please reach out to phone 9876543210 about this issue."
    clean_title, clean_text, hits = scrub_review_fields(title, text)
    assert clean_title == "Bug with email [EMAIL]"
    assert clean_text == "Please reach out to phone [PHONE] about this issue."
    assert set(hits) == {"EMAIL", "PHONE"}


# ---------------------------------------------------------------------------
# 2.3 Pseudonymization Tests
# ---------------------------------------------------------------------------
def test_pseudonymization() -> None:
    rid1 = generate_rid("play_store", "rev_1001")
    rid2 = generate_rid("play_store", "rev_1001")
    rid3 = generate_rid("app_store", "rev_1001")

    assert rid1.startswith("rid_")
    assert len(rid1) == 14  # 'rid_' + 10 chars
    # Deterministic
    assert rid1 == rid2
    # Distinct per source
    assert rid1 != rid3


# ---------------------------------------------------------------------------
# 2.4 Enrichment Tests
# ---------------------------------------------------------------------------
def test_language_detection() -> None:
    assert detect_language("Groww is an outstanding application for long term investing.") == "en"
    # Fallback on short text
    assert detect_language("hi") == "en"


def test_enrich_review_metadata() -> None:
    dt = datetime(2026, 10, 14, 15, 30, tzinfo=UTC)
    review_date, iso_week = enrich_review_metadata(dt)
    assert str(review_date) == "2026-10-14"
    assert iso_week == "2026-W42"


# ---------------------------------------------------------------------------
# 2.5 Embeddings & Cache Tests
# ---------------------------------------------------------------------------
def test_embedding_cache_miss_and_hit(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    client = MockLLMClient()
    reviews = [
        CleanReview(
            rid="rid_1",
            source="play_store",
            rating=5,
            clean_title=None,
            clean_text="Groww makes stock market investing simple and accessible for everyone.",
            date=datetime(2026, 10, 14).date(),
            iso_week="2026-W42",
        ),
        CleanReview(
            rid="rid_2",
            source="app_store",
            rating=2,
            clean_title="Chart lag",
            clean_text="Options trading chart lags significantly during morning market opening.",
            date=datetime(2026, 10, 14).date(),
            iso_week="2026-W42",
        ),
    ]

    # First run: cache misses, vectors computed
    vectors1 = embed_reviews(reviews, client, cache_dir=cache_dir)
    assert len(vectors1) == 2
    assert len(vectors1[0]) == client.embed_dim

    cache = EmbeddingCache(cache_dir=cache_dir)
    assert len(cache) == 2

    # Second run: cache hit, vectors loaded directly
    vectors2 = embed_reviews(reviews, client, cache_dir=cache_dir)
    import numpy as np

    assert np.allclose(vectors1, vectors2, atol=1e-5)


def test_text_to_embed_canonical_representation() -> None:
    assert (
        text_to_embed("Great app", "Smooth experience for SIP investments")
        == "Great app. Smooth experience for SIP investments"
    )
    assert (
        text_to_embed(None, "Smooth experience for SIP investments")
        == "Smooth experience for SIP investments"
    )


# ---------------------------------------------------------------------------
# 2.6 End-to-End Pipeline & Snapshot Accumulation Tests
# ---------------------------------------------------------------------------
def test_process_single_review_drops_short_and_scrubs() -> None:
    # Review with 5 words: dropped (< 9 words)
    raw_short = RawReview(
        source="play_store",
        review_id="p1",
        rating=5,
        text="Very good and smooth app",
        date=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
    )
    assert process_single_review(raw_short, min_words=8) is None

    # Review with 12 words and PII: kept, scrubbed, pseudonymized
    raw_valid = RawReview(
        source="play_store",
        review_id="p2",
        rating=1,
        text=("Please call me at 9876543210 my withdrawal to bank account failed repeatedly."),
        date=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
    )
    clean = process_single_review(raw_valid, min_words=8)
    assert clean is not None
    assert clean.rid.startswith("rid_")
    assert "9876543210" not in clean.clean_text
    assert "[PHONE]" in clean.clean_text
    assert "PHONE" in clean.pii_hits
    assert clean.iso_week == "2026-W42"


def test_pipeline_snapshot_accumulation_and_persistence(tmp_path: Path) -> None:
    store = RunStore(base_dir=tmp_path)
    config = AppConfig()
    config.ingest.min_words = 8
    client = MockLLMClient()

    # Create prior stored snapshot for App Store in week 2026-W40
    prior_app_store_review = CleanReview(
        rid="rid_prior_ios",
        source="app_store",
        rating=5,
        clean_text=(
            "Historically saved review for mutual funds direct investment portfolio tracking."
        ),
        date=datetime(2026, 10, 1).date(),
        iso_week="2026-W40",
    )
    store.save_snapshot("app_store", "2026-W40", [prior_app_store_review])

    # Incoming live reviews for week 2026-W42
    raw_reviews = [
        # Short review (8 words): must be dropped
        RawReview(
            source="play_store",
            review_id="rev_short",
            rating=5,
            text="One two three four five six seven eight",
            date=datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        ),
        # Valid Play Store review (10 words): kept
        RawReview(
            source="play_store",
            review_id="rev_play_1",
            rating=5,
            text="Groww mutual funds and stock investments are extremely convenient and fast.",
            date=datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        ),
        # Valid App Store review (11 words): kept
        RawReview(
            source="app_store",
            review_id="rev_app_1",
            rating=4,
            title="Clean UI",
            text="I really enjoy using Groww for automated SIP investments every month.",
            date=datetime(2026, 10, 14, 12, 0, tzinfo=UTC),
        ),
    ]

    # Live coverage starts from W42, so W40 is missing and must be merged from snapshots
    coverage_start = {"app_store": datetime(2026, 10, 14).date()}

    result = process_and_persist_reviews(
        raw_reviews=raw_reviews,
        target_week="2026-W42",
        n_weeks=3,
        config=config,
        run_store=store,
        llm_client=client,
        coverage_start=coverage_start,
        compute_embeddings=True,
    )

    # rev_short was dropped, rev_play_1 & rev_app_1 kept, and prior_app_store_review was merged
    assert len(result) == 3
    rids = {r.rid for r in result}
    assert "rid_prior_ios" in rids

    # Verify persisted clean reviews on disk
    persisted = store.load_clean_reviews("2026-W42")
    assert len(persisted) == 3

    # Verify per-source snapshot was created for W42
    w42_play_snapshot = store.load_snapshot("play_store", "2026-W42")
    assert len(w42_play_snapshot) == 1
    assert w42_play_snapshot[0].source == "play_store"


# --- Edge-case regressions (EC-PII-11/12/20-26, G2/G3/G4) ---


@pytest.mark.parametrize(
    ("raw_text", "must_be_gone", "expected_type"),
    [
        ("Order ID GRW2410AB93XZ still pending", "GRW2410AB93XZ", "ID"),
        ("ticket #8812345 closed without reply", "8812345", "ID"),
        ("GRW2410AB93XZ is my order", "GRW2410AB93XZ", "ID"),
        ("Device id 3f2504e0-4f89-11d3-9a0c-0305e82c3301 blocked", "3f2504e0", "DEVICE_ID"),
    ],
)
def test_ec_pii_ids_and_device_ids(raw_text: str, must_be_gone: str, expected_type: str) -> None:
    scrubbed, hits = scrub_pii(raw_text)
    assert must_be_gone not in scrubbed
    assert expected_type in hits


@pytest.mark.parametrize(
    "benign_text",
    [
        "Bought NIFTY24OCT24500CE and BANKNIFTY2410251000PE yesterday",
        "my order got rejected again",
        "placed an order for 24500 CE",
        "iOS 18 update broke Face ID",
    ],
)
def test_ec_pii_id_false_positive_guards(benign_text: str) -> None:
    scrubbed, hits = scrub_pii(benign_text)
    assert hits == []
    assert scrubbed == benign_text


def test_ec_pii_26_idempotent() -> None:
    once, _ = scrub_pii(
        "Order ID GRW2410AB93XZ call 9876543210 id 3f2504e0-4f89-11d3-9a0c-0305e82c3301"
    )
    twice, hits = scrub_pii(once)
    assert once == twice
    assert hits == []


def test_presidio_missing_fails_loudly() -> None:
    from pulse.preprocess.pii import PresidioUnavailableError, _presidio_engines

    _presidio_engines.cache_clear()
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        with pytest.raises(PresidioUnavailableError):
            scrub_pii("Rahul Sharma was rude", enable_presidio=True)


def test_long_word_with_few_digits_is_not_an_id() -> None:
    scrubbed, hits = scrub_pii('No "same-time" comparison10 AM volume')
    assert hits == []
    assert "comparison10" in scrubbed


def test_mixed_hindi_english_review_is_dropped_by_english_filter() -> None:
    from pulse.models import RawReview
    from pulse.preprocess.enrich import is_english, non_latin_letter_ratio
    from pulse.preprocess.pipeline import process_single_review

    mixed = "Withdrawal taking too long yaar मेरा पैसा अटक गया please fix this soon"
    assert non_latin_letter_ratio(mixed) > 0.05
    assert not is_english(mixed, "en")
    assert is_english("Withdrawal is taking far too long, please fix this soon", "en")
    assert not is_english("Withdrawal is taking far too long", "hi")

    raw = RawReview(
        source="play_store",
        review_id="x1",
        rating=1,
        text=mixed,
        date=datetime(2026, 10, 14, tzinfo=UTC),
    )
    assert process_single_review(raw, min_words=3, english_only=True) is None
    assert process_single_review(raw, min_words=3, english_only=False) is not None
