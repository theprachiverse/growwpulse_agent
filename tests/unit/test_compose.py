"""Unit tests for Phase 4: Pulse Composition & Guardrails."""

from datetime import date
from pathlib import Path

from pulse.compose.actions import generate_action_ideas
from pulse.compose.models import (
    ActionIdeaItem,
    ActionIdeasResponse,
    QuoteCandidate,
    ShortenedActionItem,
    ShortenedThemeItem,
    ShortenResponse,
)
from pulse.compose.pipeline import run_compose_pipeline
from pulse.compose.quotes import (
    balance_quote_sources,
    extract_verbatim_span,
    filter_candidate_review,
    retrieve_quote_candidates,
)
from pulse.compose.render import render_pulse
from pulse.compose.shorten import count_pulse_body_words, run_shorten_pass
from pulse.config import AppConfig
from pulse.guardrails.validators import validate_pulse
from pulse.llm.client import MockLLMClient
from pulse.models import ActionIdea, CleanReview, Quote, Source, Theme
from pulse.run_store import RunStore


def make_test_review(
    rid: str,
    source: Source = "play_store",
    rating: int = 1,
    clean_text: str = "Trading orders fail continuously during market open causing huge financial losses.",
    clean_title: str | None = None,
    helpful_votes: int = 25,
    iso_week: str = "2026-W42",
    pii_hits: list[str] | None = None,
) -> CleanReview:
    return CleanReview(
        rid=rid,
        source=source,
        rating=rating,
        clean_title=clean_title,
        clean_text=clean_text,
        date=date(2026, 10, 14),
        iso_week=iso_week,
        app_version="18.21.0",
        helpful_votes=helpful_votes,
        lang="en",
        pii_hits=pii_hits or [],
    )


def make_test_themes() -> list[Theme]:
    return [
        Theme(
            theme_id="t1",
            name="Trading & F&O Execution",
            definition="Orders getting rejected, charts lagging, and stop loss failing during peak trading volume.",
            review_count=450,
            share=0.35,
            avg_rating=2.1,
            low_rating_share=0.68,
            wow_delta=4.2,
            by_source={"play_store": 400, "app_store": 50},
        ),
        Theme(
            theme_id="t2",
            name="Customer Support & AI Bot Loops",
            definition="Inability to contact human agents, unhelpful AI bot responses, and unresolved escalations.",
            review_count=320,
            share=0.25,
            avg_rating=1.7,
            low_rating_share=0.82,
            wow_delta=-1.5,
            by_source={"play_store": 290, "app_store": 30},
        ),
        Theme(
            theme_id="t3",
            name="App Stability & Biometrics",
            definition="App crashes on launch, freezing after updates, and biometric authentication failure.",
            review_count=200,
            share=0.15,
            avg_rating=2.5,
            low_rating_share=0.55,
            wow_delta=0.8,
            by_source={"play_store": 180, "app_store": 20},
        ),
    ]


# --- 1. Candidate Filtering Tests ---


def test_filter_candidate_review_length():
    # Too short (< 8 words)
    r_short = make_test_review("r1", clean_text="App is bad and crashed.")
    assert not filter_candidate_review(r_short, min_words=8, max_words=40)

    # Valid (between 8 and 40 words)
    r_valid = make_test_review(
        "r2",
        clean_text="The trading app crashed while I was placing an options stop loss order.",
    )
    assert filter_candidate_review(r_valid, min_words=8, max_words=40)

    # Too long (> 40 words)
    r_long = make_test_review("r3", clean_text="word " * 45)
    assert not filter_candidate_review(r_long, min_words=8, max_words=40)


def test_filter_candidate_review_profanity_and_pii():
    # Contains profanity
    r_profane = make_test_review(
        "r4", clean_text="This app is total bakwas and fraudster team stealing money."
    )
    assert not filter_candidate_review(r_profane)

    # Contains heavy PII placeholders
    r_pii = make_test_review(
        "r5",
        clean_text="Contact me on [PHONE] or [EMAIL] regarding my transaction issue.",
        pii_hits=["PHONE", "EMAIL"],
    )
    assert not filter_candidate_review(r_pii)


# --- 2. Candidate Retrieval & Stratification Tests ---


def test_retrieve_quote_candidates_stratification():
    reviews: list[CleanReview] = []
    # 20 Play reviews
    for i in range(20):
        reviews.append(
            make_test_review(
                rid=f"r_play_{i}",
                source="play_store",
                rating=1,
                clean_text=f"Play store review number {i} regarding order cancellation and delays.",
                helpful_votes=i * 10,
            )
        )
    # 3 App Store reviews
    for i in range(3):
        reviews.append(
            make_test_review(
                rid=f"r_app_{i}",
                source="app_store",
                rating=2,
                clean_text=f"App store review number {i} regarding FaceID authentication error on iOS.",
                helpful_votes=i * 5,
            )
        )

    candidates = retrieve_quote_candidates(
        theme_id="t1",
        reviews=reviews,
        min_words=8,
        max_words=40,
        target_count=10,
    )

    assert len(candidates) <= 10
    sources = [c.source for c in candidates]
    # App Store reviews must be represented
    assert "app_store" in sources
    assert "play_store" in sources


# --- 3. Span-based Verbatim Quote Extraction Tests ---


def test_extract_verbatim_span():
    text = "Trading orders fail continuously during market open causing huge financial losses."
    cand = QuoteCandidate(
        rid="r_test",
        source="play_store",
        theme_id="t1",
        rating=1,
        clean_text=text,
        date=date(2026, 10, 14),
        helpful_votes=10,
        word_count=len(text.split()),
    )

    # Substring slice
    start = text.index("orders fail")
    end = text.index("market open") + len("market open")
    extracted = extract_verbatim_span(cand, start, end, min_words=5, max_words=20)

    assert extracted in text
    assert extracted == "orders fail continuously during market open"

    # Out of bounds coordinates fallback to full text
    extracted_oob = extract_verbatim_span(cand, -5, 500, min_words=5, max_words=30)
    assert extracted_oob in text


def test_balance_quote_sources_swap():
    # If all 3 quotes are from play_store, soft mix rule swaps one with app_store candidate
    quotes = [
        Quote(
            rid="q1",
            source="play_store",
            theme_id="t1",
            text="Quote text 1 for theme 1.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="q2",
            source="play_store",
            theme_id="t2",
            text="Quote text 2 for theme 2.",
            rating=2,
            month="Oct 2026",
        ),
        Quote(
            rid="q3",
            source="play_store",
            theme_id="t3",
            text="Quote text 3 for theme 3.",
            rating=1,
            month="Oct 2026",
        ),
    ]

    candidate_pool = {
        "t1": [],
        "t2": [
            QuoteCandidate(
                rid="q_app_t2",
                source="app_store",
                theme_id="t2",
                rating=2,
                clean_text="Customer support via phone is completely unreachable on iOS device.",
                date=date(2026, 10, 10),
                helpful_votes=50,
                word_count=10,
            )
        ],
        "t3": [],
    }

    balanced = balance_quote_sources(quotes, candidate_pool)
    assert len(balanced) == 3
    sources = {q.source for q in balanced}
    assert "app_store" in sources
    assert "play_store" in sources


# --- 4. Action Ideas Tests ---


def test_generate_action_ideas():
    themes = make_test_themes()
    quotes = [
        Quote(
            rid="q1",
            source="play_store",
            theme_id="t1",
            text="Orders keep failing constantly.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="q2",
            source="app_store",
            theme_id="t2",
            text="AI bot keeps looping without agent.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="q3",
            source="play_store",
            theme_id="t3",
            text="App crashes every time biometric triggers.",
            rating=1,
            month="Oct 2026",
        ),
    ]

    mock_resp = ActionIdeasResponse(
        actions=[
            ActionIdeaItem(
                theme_id="t1",
                title="Stop-Loss Circuit Pre-Validation",
                rationale="Audit order queue bottlenecks to prevent execution failures during market open spikes.",
                owner_hint="Engineering",
            ),
            ActionIdeaItem(
                theme_id="t2",
                title="AI Support Bot Human Handoff",
                rationale="Trigger automatic human routing when a user asks more than two repetitive queries.",
                owner_hint="Support",
            ),
            ActionIdeaItem(
                theme_id="t3",
                title="Biometric Fallback Hardening",
                rationale="Prevent crash loops on Android/iOS when biometric authentication fails.",
                owner_hint="Product",
            ),
        ]
    )

    client = MockLLMClient(canned_responses=[mock_resp])
    actions = generate_action_ideas(themes, quotes, client)

    assert len(actions) == 3
    assert {a.theme_id for a in actions} == {"t1", "t2", "t3"}
    assert all(a.owner_hint in ("Product", "Support", "Engineering", "Growth") for a in actions)


# --- 5. Word Budget & Shortening Tests ---


def test_count_pulse_body_words():
    md = """# Groww App Reviews Pulse — 2026-W42
*Window: 2026-08-10 to 2026-10-18 (10 weeks) · Play Store: 1,388 reviews (2.89★) · App Store: 139 reviews (3.38★)*

## Top Themes
1. **Trading Execution** — 35% of reviews. Serious lag issues during market open hours.

## What Users Said
> "Orders are failing constantly on market open." — 1★, Play Store, Oct 2026

## Action Ideas
1. **Fix Orders** — Audit queue latency (Engineering, Trading Execution)
"""
    words = count_pulse_body_words(md)
    # Metadata header should be excluded
    assert words < 60
    assert words > 20


def test_run_shorten_pass_preserves_quotes():
    themes = make_test_themes()
    actions = [
        ActionIdea(
            theme_id="t1",
            title="Fix Trading Orders",
            rationale="This is a very long rationale explaining in excessive detail why trading orders must be fixed immediately without delay.",
            owner_hint="Engineering",
        ),
        ActionIdea(
            theme_id="t2",
            title="Fix Support",
            rationale="Customer support agents are unavailable causing long delays and unresolved tickets.",
            owner_hint="Support",
        ),
        ActionIdea(
            theme_id="t3",
            title="Fix App Crashing",
            rationale="Biometric login crashes must be resolved across devices.",
            owner_hint="Product",
        ),
    ]

    mock_shorten = ShortenResponse(
        themes=[
            ShortenedThemeItem(
                theme_id="t1", shortened_definition="Trading orders failing during peak volume."
            ),
            ShortenedThemeItem(
                theme_id="t2", shortened_definition="AI bot loops without human escalation."
            ),
            ShortenedThemeItem(
                theme_id="t3", shortened_definition="App crashes during biometric login."
            ),
        ],
        actions=[
            ShortenedActionItem(
                theme_id="t1",
                shortened_rationale="Audit order queue bottlenecks to prevent open spikes.",
            ),
            ShortenedActionItem(
                theme_id="t2",
                shortened_rationale="Trigger automatic human routing after two queries.",
            ),
            ShortenedActionItem(
                theme_id="t3", shortened_rationale="Prevent crash loops on biometric failure."
            ),
        ],
    )

    client = MockLLMClient(canned_responses=[mock_shorten])
    short_themes, short_actions = run_shorten_pass(
        top_themes=themes,
        actions=actions,
        current_word_count=280,
        max_words=250,
        llm_client=client,
    )

    assert len(short_themes) == 3
    assert len(short_actions) == 3
    assert short_themes[0].definition == "Trading orders failing during peak volume."


# --- 6. Guardrails Validation Tests ---


def test_guardrails_pass_valid_pulse():
    config = AppConfig()
    reviews = [
        make_test_review(
            "r1",
            "play_store",
            clean_text="Trading orders fail continuously during market open causing huge financial losses.",
        ),
        make_test_review(
            "r2",
            "app_store",
            clean_text="Customer support via phone is completely unreachable on iOS device.",
        ),
        make_test_review(
            "r3",
            "play_store",
            clean_text="App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    themes = make_test_themes()
    quotes = [
        Quote(
            rid="r1",
            source="play_store",
            theme_id="t1",
            text="Trading orders fail continuously during market open causing huge financial losses.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="r2",
            source="app_store",
            theme_id="t2",
            text="Customer support via phone is completely unreachable on iOS device.",
            rating=2,
            month="Oct 2026",
        ),
        Quote(
            rid="r3",
            source="play_store",
            theme_id="t3",
            text="App crashes constantly every single time biometric face authentication triggers.",
            rating=1,
            month="Oct 2026",
        ),
    ]
    actions = [
        ActionIdea(
            title="Fix Orders",
            rationale="Resolve order queue delays.",
            theme_id="t1",
            owner_hint="Engineering",
        ),
        ActionIdea(
            title="Fix Support",
            rationale="Fast-track bot to agent.",
            theme_id="t2",
            owner_hint="Support",
        ),
        ActionIdea(
            title="Fix Crashing",
            rationale="Harden biometric session.",
            theme_id="t3",
            owner_hint="Product",
        ),
    ]

    _, pulse = render_pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        window_weeks=10,
        reviews=reviews,
        top_themes=themes,
        quotes=quotes,
        actions=actions,
    )

    report = validate_pulse(pulse, reviews, themes, config)
    assert report.passed
    assert len(report.hard_failures) == 0
    assert report.checks["verbatim"]
    assert report.checks["pii"]
    assert report.checks["word_limit"]
    assert report.checks["source_attribution"]


def test_guardrails_rejects_tampered_quote():
    config = AppConfig()
    reviews = [
        make_test_review(
            "r1",
            "play_store",
            clean_text="Trading orders fail continuously during market open causing huge financial losses.",
        ),
        make_test_review(
            "r2",
            "app_store",
            clean_text="Customer support via phone is completely unreachable on iOS device.",
        ),
        make_test_review(
            "r3",
            "play_store",
            clean_text="App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    themes = make_test_themes()
    # Tamper with quote text: "fail" -> "glitch"
    quotes = [
        Quote(
            rid="r1",
            source="play_store",
            theme_id="t1",
            text="Trading orders glitch continuously during market open causing huge financial losses.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="r2",
            source="app_store",
            theme_id="t2",
            text="Customer support via phone is completely unreachable on iOS device.",
            rating=2,
            month="Oct 2026",
        ),
        Quote(
            rid="r3",
            source="play_store",
            theme_id="t3",
            text="App crashes constantly every single time biometric face authentication triggers.",
            rating=1,
            month="Oct 2026",
        ),
    ]
    actions = [
        ActionIdea(
            title="Fix Orders", rationale="Resolve delays.", theme_id="t1", owner_hint="Engineering"
        ),
        ActionIdea(
            title="Fix Support", rationale="Fast-track bot.", theme_id="t2", owner_hint="Support"
        ),
        ActionIdea(
            title="Fix Crashing",
            rationale="Harden biometrics.",
            theme_id="t3",
            owner_hint="Product",
        ),
    ]

    _, pulse = render_pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        window_weeks=10,
        reviews=reviews,
        top_themes=themes,
        quotes=quotes,
        actions=actions,
    )

    report = validate_pulse(pulse, reviews, themes, config)
    assert not report.passed
    assert not report.checks["verbatim"]
    assert any("NOT an exact substring" in f for f in report.hard_failures)


def test_guardrails_rejects_pii_leak():
    config = AppConfig()
    reviews = [
        make_test_review(
            "r1",
            "play_store",
            clean_text="Trading orders fail continuously during market open causing huge financial losses.",
        ),
        make_test_review(
            "r2",
            "app_store",
            clean_text="Customer support via phone is completely unreachable on iOS device.",
        ),
        make_test_review(
            "r3",
            "play_store",
            clean_text="App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    themes = make_test_themes()
    # Inject PII phone number into definition
    themes[0] = themes[0].model_copy(
        update={"definition": "Orders failing, please call user on +91 9876543210 immediately."}
    )
    quotes = [
        Quote(
            rid="r1",
            source="play_store",
            theme_id="t1",
            text="Trading orders fail continuously during market open causing huge financial losses.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="r2",
            source="app_store",
            theme_id="t2",
            text="Customer support via phone is completely unreachable on iOS device.",
            rating=2,
            month="Oct 2026",
        ),
        Quote(
            rid="r3",
            source="play_store",
            theme_id="t3",
            text="App crashes constantly every single time biometric face authentication triggers.",
            rating=1,
            month="Oct 2026",
        ),
    ]
    actions = [
        ActionIdea(
            title="Fix Orders", rationale="Resolve delays.", theme_id="t1", owner_hint="Engineering"
        ),
        ActionIdea(
            title="Fix Support", rationale="Fast-track bot.", theme_id="t2", owner_hint="Support"
        ),
        ActionIdea(
            title="Fix Crashing",
            rationale="Harden biometrics.",
            theme_id="t3",
            owner_hint="Product",
        ),
    ]

    _, pulse = render_pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        window_weeks=10,
        reviews=reviews,
        top_themes=themes,
        quotes=quotes,
        actions=actions,
    )

    report = validate_pulse(pulse, reviews, themes, config)
    assert not report.passed
    assert not report.checks["pii"]
    assert any("PII re-scan" in f for f in report.hard_failures)


def test_guardrails_rejects_mislabelled_source():
    config = AppConfig()
    reviews = [
        make_test_review(
            "r1",
            "play_store",
            clean_text="Trading orders fail continuously during market open causing huge financial losses.",
        ),
        make_test_review(
            "r2",
            "app_store",
            clean_text="Customer support via phone is completely unreachable on iOS device.",
        ),
        make_test_review(
            "r3",
            "play_store",
            clean_text="App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    themes = make_test_themes()
    # Mislabel r1 as app_store instead of play_store
    quotes = [
        Quote(
            rid="r1",
            source="app_store",
            theme_id="t1",
            text="Trading orders fail continuously during market open causing huge financial losses.",
            rating=1,
            month="Oct 2026",
        ),
        Quote(
            rid="r2",
            source="app_store",
            theme_id="t2",
            text="Customer support via phone is completely unreachable on iOS device.",
            rating=2,
            month="Oct 2026",
        ),
        Quote(
            rid="r3",
            source="play_store",
            theme_id="t3",
            text="App crashes constantly every single time biometric face authentication triggers.",
            rating=1,
            month="Oct 2026",
        ),
    ]
    actions = [
        ActionIdea(
            title="Fix Orders", rationale="Resolve delays.", theme_id="t1", owner_hint="Engineering"
        ),
        ActionIdea(
            title="Fix Support", rationale="Fast-track bot.", theme_id="t2", owner_hint="Support"
        ),
        ActionIdea(
            title="Fix Crashing",
            rationale="Harden biometrics.",
            theme_id="t3",
            owner_hint="Product",
        ),
    ]

    _, pulse = render_pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        window_weeks=10,
        reviews=reviews,
        top_themes=themes,
        quotes=quotes,
        actions=actions,
    )

    report = validate_pulse(pulse, reviews, themes, config)
    assert not report.passed
    assert not report.checks["source_attribution"]


# --- 7. Full End-to-End Pipeline Test ---


def test_run_compose_pipeline_mock(tmp_path: Path):
    store = RunStore(base_dir=tmp_path)
    week = "2026-W42"

    reviews = [
        make_test_review(
            "r1",
            "play_store",
            clean_text="Trading orders fail continuously during market open causing huge financial losses.",
        ),
        make_test_review(
            "r2",
            "app_store",
            clean_text="Customer support via phone is completely unreachable on iOS device.",
        ),
        make_test_review(
            "r3",
            "play_store",
            clean_text="App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    store.save_clean_reviews(week, reviews)

    themes = make_test_themes()
    store.save_themes(week, themes)

    assignments = [
        {
            "rid": "r1",
            "theme_id": "t1",
            "confidence": 0.9,
            "source": "play_store",
            "rating": 1,
            "helpful_votes": 25,
        },
        {
            "rid": "r2",
            "theme_id": "t2",
            "confidence": 0.9,
            "source": "app_store",
            "rating": 2,
            "helpful_votes": 10,
        },
        {
            "rid": "r3",
            "theme_id": "t3",
            "confidence": 0.9,
            "source": "play_store",
            "rating": 1,
            "helpful_votes": 15,
        },
    ]
    store.save_assignments(week, assignments)

    config = AppConfig()
    client = MockLLMClient()

    pulse, report = run_compose_pipeline(
        week=week,
        config=config,
        run_store=store,
        llm_client=client,
        dry_run=False,
    )

    assert pulse is not None
    assert report.passed
    assert pulse.word_count <= 250
    assert len(pulse.top_themes) == 3
    assert len(pulse.quotes) == 3
    assert len(pulse.actions) == 3

    # Check persistence
    saved_pulse = store.load_pulse(week)
    assert saved_pulse is not None
    assert saved_pulse.word_count == pulse.word_count

    eval_report = store.load_eval_report(week)
    assert eval_report is not None
    assert eval_report["passed"] is True


# --- Edge-case regressions (G1, G7, G8, G9) ---


def _build_pulse_for_guardrails(quotes, reviews, low_volume_threshold=0):
    themes = make_test_themes()
    actions = [
        ActionIdea(title="A", rationale="Fix it.", theme_id=t, owner_hint="Product")
        for t in ("t1", "t2", "t3")
    ]
    _, pulse = render_pulse(
        week="2026-W42",
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        window_weeks=10,
        reviews=reviews,
        top_themes=themes,
        quotes=quotes,
        actions=actions,
        low_volume_threshold=low_volume_threshold,
    )
    return pulse, themes


def _three_reviews_and_quotes(texts=None, sources=("play_store", "app_store", "play_store")):
    texts = texts or [
        "Trading orders fail continuously during market open causing huge financial losses.",
        "Customer support via phone is completely unreachable on iOS device.",
        "App crashes constantly every single time biometric face authentication triggers.",
    ]
    reviews = [make_test_review(f"r{i + 1}", sources[i], clean_text=texts[i]) for i in range(3)]
    quotes = [
        Quote(
            rid=f"r{i + 1}",
            source=sources[i],
            theme_id=f"t{i + 1}",
            text=texts[i],
            rating=1,
            month="Oct 2026",
        )
        for i in range(3)
    ]
    return reviews, quotes


def test_ec_grd_07_attribution_checked_on_quote_line():
    """Header always names both stores; a wrong label on the quote line must still fail."""
    reviews, quotes = _three_reviews_and_quotes()
    pulse, themes = _build_pulse_for_guardrails(quotes, reviews)
    assert validate_pulse(pulse, reviews, themes, AppConfig()).checks["source_attribution"]

    # Swap the label on the quote's own line only (review data unchanged)
    q1 = quotes[0].text
    bad_md = pulse.markdown.replace(f'"{q1}" — 1★, Play Store', f'"{q1}" — 1★, App Store')
    assert bad_md != pulse.markdown
    bad = pulse.model_copy(update={"markdown": bad_md})
    report = validate_pulse(bad, reviews, themes, AppConfig())
    assert not report.checks["source_attribution"]


def test_g8_quote_length_and_distinct_reviews():
    short = "Orders fail."
    reviews, quotes = _three_reviews_and_quotes(
        texts=[
            short,
            "Customer support via phone is completely unreachable on iOS device.",
            "App crashes constantly every single time biometric face authentication triggers.",
        ]
    )
    pulse, themes = _build_pulse_for_guardrails(quotes, reviews)
    assert not validate_pulse(pulse, reviews, themes, AppConfig()).checks["quote_rules"]

    reviews, quotes = _three_reviews_and_quotes()
    quotes[1] = quotes[1].model_copy(update={"rid": "r1", "text": quotes[0].text})
    pulse, themes = _build_pulse_for_guardrails(quotes, reviews)
    assert not validate_pulse(pulse, reviews, themes, AppConfig()).checks["quote_rules"]


def test_g7_tone_catches_punctuated_tokens():
    reviews, quotes = _three_reviews_and_quotes()
    pulse, themes = _build_pulse_for_guardrails(quotes, reviews)
    noisy = pulse.model_copy(update={"markdown": pulse.markdown + "\nlol, this is bad.\n"})
    assert not validate_pulse(noisy, reviews, themes, AppConfig()).checks["tone"]


def test_g9_low_volume_banner_not_counted_as_body_words():
    reviews, quotes = _three_reviews_and_quotes()
    normal, _ = _build_pulse_for_guardrails(quotes, reviews, low_volume_threshold=0)
    low, _ = _build_pulse_for_guardrails(quotes, reviews, low_volume_threshold=50)
    assert "Low volume" in low.markdown
    assert "Low volume" not in normal.markdown
    assert low.word_count == normal.word_count


def _setup_pipeline_store(tmp_path: Path) -> tuple[RunStore, str]:
    store = RunStore(base_dir=tmp_path)
    week = "2026-W42"
    texts = [
        (
            "play_store",
            "Trading orders fail continuously during market open causing huge financial losses.",
        ),
        ("app_store", "Customer support via phone is completely unreachable on iOS device."),
        (
            "play_store",
            "App crashes constantly every single time biometric face authentication triggers.",
        ),
    ]
    store.save_clean_reviews(
        week, [make_test_review(f"r{i + 1}", s, clean_text=t) for i, (s, t) in enumerate(texts)]
    )
    store.save_themes(week, make_test_themes())
    store.save_assignments(
        week,
        [
            {
                "rid": f"r{i + 1}",
                "theme_id": f"t{i + 1}",
                "confidence": 0.9,
                "source": texts[i][0],
                "rating": 1,
                "helpful_votes": 10,
            }
            for i in range(3)
        ],
    )
    return store, week


def test_repair_loop_fails_fast_on_unrepairable_check(tmp_path: Path, monkeypatch):
    """A non-word-count hard failure must not trigger repeated re-validation."""
    import pulse.compose.pipeline as pipeline_mod
    from pulse.guardrails.validators import ValidationReport

    store, week = _setup_pipeline_store(tmp_path)
    calls = {"n": 0}

    def fake_validate(**kwargs):
        calls["n"] += 1
        return ValidationReport(
            passed=False,
            hard_failures=["PII re-scan check failed"],
            checks={"pii": False, "word_limit": True, "tone": True},
            word_count=100,
            max_words_allowed=250,
        )

    monkeypatch.setattr(pipeline_mod, "validate_pulse", fake_validate)
    _, report = run_compose_pipeline(
        week=week, config=AppConfig(), run_store=store, llm_client=MockLLMClient(), dry_run=True
    )
    assert not report.passed
    assert calls["n"] == 1


def test_shortening_targets_target_words_not_max(tmp_path: Path, monkeypatch):
    import pulse.compose.pipeline as pipeline_mod

    store, week = _setup_pipeline_store(tmp_path)
    seen: list[int] = []
    real = pipeline_mod.run_shorten_pass

    def spy(**kwargs):
        seen.append(kwargs["max_words"])
        return real(**kwargs)

    monkeypatch.setattr(pipeline_mod, "run_shorten_pass", spy)
    config = AppConfig()
    config.compose.target_words = 60  # force shortening on a pulse that is under 250
    pulse, _ = run_compose_pipeline(
        week=week, config=config, run_store=store, llm_client=MockLLMClient(), dry_run=True
    )
    assert seen and set(seen) == {60}
    assert pulse.word_count <= 250


# --- Quotes must support the theme they are filed under ---


def _quote_candidate(rid: str, theme_id: str, text: str) -> QuoteCandidate:
    return QuoteCandidate(
        rid=rid,
        source="play_store",
        theme_id=theme_id,
        rating=1,
        clean_text=text,
        clean_title=None,
        date=date(2026, 10, 14),
        helpful_votes=1,
        distance_to_centroid=0.1,
        word_count=len(text.split()),
    )


class _MislabellingLLM:
    """Files the customer-care review under the trading theme (the bug seen in the W42 pulse)."""

    def complete_json(self, prompt, schema, temperature=0.0, **_):
        from pulse.compose.models import QuoteSelectionResponse

        def pick(theme_id, rid, end):
            return {
                "theme_id": theme_id,
                "selected_span": {"rid": rid, "start_char": 0, "end_char": end},
            }

        return QuoteSelectionResponse.model_validate(
            {
                "quotes": [
                    pick("t1", "r_trade", 80),
                    pick("t2", "r_care", 80),  # fine
                    pick("t3", "r_care", 80),  # wrong: r_care is not a candidate of t3
                ]
            }
        )


def test_quote_filed_under_the_wrong_theme_is_rejected_and_replaced():
    from pulse.compose.quotes import select_quotes_with_llm

    trade = "Stop loss orders get rejected during market open and charts lag badly every day."
    care = "Customer care never replies to my complaints and the support bot keeps looping."
    crash = "The app crashes on launch after the update and biometric login keeps failing."
    candidates = {
        "t1": [_quote_candidate("r_trade", "t1", trade)],
        "t2": [_quote_candidate("r_care", "t2", care)],
        "t3": [_quote_candidate("r_crash", "t3", crash)],
    }
    quotes = select_quotes_with_llm(make_test_themes(), candidates, _MislabellingLLM())

    by_theme = {q.theme_id: q for q in quotes}
    assert set(by_theme) == {"t1", "t2", "t3"}
    assert by_theme["t3"].rid == "r_crash"  # fell back to a t3 candidate, not the care review
    assert len({q.rid for q in quotes}) == 3  # no review quoted twice


def test_theme_centroid_ranks_on_topic_candidates_first():
    on_topic = make_test_review(
        rid="r_on",
        rating=3,
        helpful_votes=0,
        clean_text="Stop loss order got rejected during market open and the chart lagged badly.",
    )
    off_topic = make_test_review(
        rid="r_off",
        rating=3,
        helpful_votes=0,
        clean_text="Please add a portfolio return value column like Moneycontrol has, thanks.",
    )
    embeddings = {"r_on": [1.0, 0.0], "r_off": [0.0, 1.0]}
    ranked = retrieve_quote_candidates(
        theme_id="t1",
        reviews=[off_topic, on_topic],
        theme_centroid=[1.0, 0.0],  # the trading theme points the same way as r_on
        review_embeddings=embeddings,
        target_count=2,
    )
    assert [c.rid for c in ranked] == ["r_on", "r_off"]


# --- The word limit is guaranteed even when the LLM's shortening does not help ---


def test_deterministic_shorten_cuts_at_a_clause_not_mid_phrase():
    from pulse.compose.shorten import deterministic_shorten

    text = "Users report slow order execution during market open, and charts that freeze often"
    out = deterministic_shorten(text, max_words=9)
    assert len(out.split()) <= 9 and out.endswith(".")
    assert not out.rstrip(".").endswith(("and", "that", "with"))


def test_word_limit_is_enforced_even_when_llm_shortening_does_nothing(tmp_path: Path, monkeypatch):
    import pulse.compose.pipeline as pipeline_mod

    store, week = _setup_pipeline_store(tmp_path)

    def lazy_shorten(top_themes, actions, current_word_count, max_words, llm_client):
        return top_themes, actions  # the LLM "shortened" nothing

    monkeypatch.setattr(pipeline_mod, "run_shorten_pass", lazy_shorten)
    config = AppConfig()
    config.compose.max_words = 215
    config.compose.target_words = 200
    baseline, _ = run_compose_pipeline(
        week=week, config=AppConfig(), run_store=store, llm_client=MockLLMClient(), dry_run=True
    )
    assert baseline.word_count > 215  # the unclamped pulse really is over the limit

    pulse, report = run_compose_pipeline(
        week=week, config=config, run_store=store, llm_client=MockLLMClient(), dry_run=True
    )
    assert report.passed, report.hard_failures
    assert pulse.word_count <= 215
    assert [q.text for q in pulse.quotes] == [q.text for q in baseline.quotes]  # quotes untouched


# --- persist=False leaves the run directory untouched (pulse compose --dry-run) ---


def test_persist_false_writes_nothing_and_default_still_saves(tmp_path: Path):
    store, week = _setup_pipeline_store(tmp_path)
    run_dir = store.run_dir(week)

    run_compose_pipeline(
        week=week,
        config=AppConfig(),
        run_store=store,
        llm_client=MockLLMClient(),
        dry_run=True,
        persist=False,
    )
    assert not (run_dir / "pulse.json").exists()
    assert not (run_dir / "pulse.md").exists()
    assert not (run_dir / "eval_report.json").exists()

    # The orchestrator's dry run (persist left at its default) still saves the pulse.
    run_compose_pipeline(
        week=week, config=AppConfig(), run_store=store, llm_client=MockLLMClient(), dry_run=True
    )
    assert (run_dir / "pulse.json").exists() and (run_dir / "pulse.md").exists()


# --- Running out of LLM quota during shortening must not abort the run ---


class _OutOfQuotaOnShorten(MockLLMClient):
    """Quote/action calls fall back as usual; the shorten call finds the quota gone."""

    def complete_json(self, prompt, schema, temperature=0.0, **kwargs):
        from pulse.compose.models import ShortenResponse
        from pulse.llm.budget import DailyQuotaExceededError

        if schema is ShortenResponse:
            raise DailyQuotaExceededError("Groq's daily token limit is used up")
        return super().complete_json(prompt, schema, temperature, **kwargs)


def test_quota_gone_during_shortening_still_finishes_under_the_limit(tmp_path: Path):
    store, week = _setup_pipeline_store(tmp_path)
    config = AppConfig()
    config.compose.max_words = 215
    config.compose.target_words = 200
    pulse, report = run_compose_pipeline(
        week=week, config=config, run_store=store, llm_client=_OutOfQuotaOnShorten(), dry_run=True
    )
    assert report.passed, report.hard_failures
    assert pulse.word_count <= 215
    assert (store.run_dir(week) / "pulse.json").exists()  # saved, not paused
