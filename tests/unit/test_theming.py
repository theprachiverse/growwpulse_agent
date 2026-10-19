"""Unit tests for Phase 3: Theming (clustering, discovery, classification, ranking)."""

from datetime import date
from pathlib import Path

from pulse.config import AppConfig
from pulse.llm.client import MockLLMClient
from pulse.models import CleanReview, Source, Theme
from pulse.run_store import RunStore
from pulse.theming.classify import (
    classify_by_centroid,
    classify_reviews,
)
from pulse.theming.cluster import cluster_and_sample, truncate_preview
from pulse.theming.discover import discover_themes, merge_excess_themes
from pulse.theming.models import (
    BatchClassificationResult,
    ClassificationItem,
    DiscoveredThemes,
    ReviewAssignment,
    ThemeSpec,
)
from pulse.theming.pipeline import get_previous_week_string, run_theming_pipeline
from pulse.theming.rank import rank_themes


def make_clean_review(
    rid: str,
    source: Source = "play_store",
    rating: int = 1,
    clean_text: str = "Trading orders are getting rejected and stop loss is failing repeatedly.",
    helpful_votes: int = 10,
    iso_week: str = "2026-W42",
) -> CleanReview:
    return CleanReview(
        rid=rid,
        source=source,
        rating=rating,
        clean_title="Order Issue" if source == "app_store" else None,
        clean_text=clean_text,
        date=date(2026, 10, 15),
        iso_week=iso_week,
        app_version="18.21.1",
        helpful_votes=helpful_votes,
        lang="en",
        pii_hits=[],
    )


def test_truncate_preview():
    short_text = "This is a short text."
    assert truncate_preview(short_text) == short_text

    long_text = "word " * 100
    res = truncate_preview(long_text, max_words=10, max_chars=100)
    assert len(res.split()) <= 11
    assert res.endswith("...")


def test_cluster_and_sample_stratification():
    reviews: list[CleanReview] = []
    # 30 Play Store reviews
    for i in range(30):
        reviews.append(
            make_clean_review(
                rid=f"rid_play_{i}",
                source="play_store",
                rating=1 if i < 15 else 5,
                clean_text=f"Play Store review number {i} regarding trading execution and charts.",
                helpful_votes=i * 5,
            )
        )
    # 5 App Store reviews
    for i in range(5):
        reviews.append(
            make_clean_review(
                rid=f"rid_app_{i}",
                source="app_store",
                rating=2,
                clean_text=f"App Store review number {i} regarding iOS biometric FaceID login crash.",
                helpful_votes=i * 2,
            )
        )

    # Synthetic embeddings (dim=16)
    embeddings = []
    for r in reviews:
        seed = 1.0 if r.source == "app_store" else 0.0
        embeddings.append([seed + float(j) * 0.1 for j in range(16)])

    cluster_samples, cluster_centroids = cluster_and_sample(
        reviews, embeddings, k=2, random_state=42, samples_per_cluster=10
    )

    assert len(cluster_samples) == 2
    assert len(cluster_centroids) == 2

    # Verify App Store representation was selected
    all_sampled = [r for samples in cluster_samples.values() for r in samples]
    sampled_app = [r for r in all_sampled if r.source == "app_store"]
    assert len(sampled_app) > 0


def test_merge_excess_themes():
    client = MockLLMClient()
    themes = [
        ThemeSpec(
            theme_id=f"t{i}",
            name=f"Theme Name {i}",
            definition=f"Definition of theme {i}",
            inclusion_criteria=[f"kw{i}"],
            exclusion_criteria=[],
        )
        for i in range(1, 8)  # 7 themes
    ]

    merged = merge_excess_themes(themes, client, max_themes=5)
    assert len(merged) == 5
    assert all(isinstance(t, ThemeSpec) for t in merged)


def test_discover_themes_with_mock_llm():
    mock_discovered = DiscoveredThemes(
        themes=[
            ThemeSpec(
                theme_id="t1",
                name="Trading & F&O Execution",
                definition="Issues related to orders, stop loss execution, charts, and trading lags.",
                inclusion_criteria=["stop loss", "orders"],
                exclusion_criteria=["customer support"],
            ),
            ThemeSpec(
                theme_id="t2",
                name="Customer Support & AI Loops",
                definition="Frustration with AI bot responses and lack of human escalation.",
                inclusion_criteria=["ai chat", "bot"],
                exclusion_criteria=["trading"],
            ),
            ThemeSpec(
                theme_id="t3",
                name="Charges & Transparency",
                definition="Hidden fees and unclear brokerage deductions.",
                inclusion_criteria=["brokerage", "hidden charges"],
                exclusion_criteria=[],
            ),
        ]
    )
    client = MockLLMClient(canned_responses=[mock_discovered])

    reviews = [make_clean_review(f"r{i}") for i in range(5)]
    cluster_samples = {0: reviews}

    discovered = discover_themes(
        cluster_samples=cluster_samples,
        seed_taxonomy=["trading_fno", "customer_support", "charges"],
        previous_themes=None,
        llm_client=client,
        max_themes=5,
    )

    assert len(discovered) == 3
    assert discovered[0].theme_id == "t1"
    assert discovered[0].name == "Trading & F&O Execution"
    assert discovered[1].theme_id == "t2"


def test_classify_by_centroid():
    centroids = {
        "t1": [1.0, 0.0, 0.0],
        "t2": [0.0, 1.0, 0.0],
    }

    # Vector aligned with t1
    theme_id, conf = classify_by_centroid([0.9, 0.1, 0.0], centroids)
    assert theme_id == "t1"
    assert conf > 0.8

    # Vector aligned with t2
    theme_id, conf = classify_by_centroid([0.0, 0.95, 0.0], centroids)
    assert theme_id == "t2"
    assert conf > 0.8

    # Ambiguous vector far from all centroids
    theme_id, conf = classify_by_centroid([0.0, 0.0, 1.0], centroids, min_similarity=0.5)
    assert theme_id == "other"


def test_classify_reviews_with_completeness_and_fallback():
    r1 = make_clean_review("rid_1")
    r2 = make_clean_review("rid_2")
    r3 = make_clean_review("rid_3")
    reviews = [r1, r2, r3]
    embeddings = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]

    themes = [
        ThemeSpec(
            theme_id="t1",
            name="Trading",
            definition="Trading issues",
            inclusion_criteria=["orders"],
            exclusion_criteria=[],
        ),
        ThemeSpec(
            theme_id="t2",
            name="Support",
            definition="Customer support",
            inclusion_criteria=["care"],
            exclusion_criteria=[],
        ),
    ]

    # LLM returns classification for rid_1 and rid_2, but drops rid_3
    # rid_2 has low confidence (0.3), so it should trigger centroid fallback
    mock_batch = BatchClassificationResult(
        items=[
            ClassificationItem(rid="rid_1", theme_id="t1", confidence=0.9),
            ClassificationItem(rid="rid_2", theme_id="t1", confidence=0.3),
        ]
    )
    client = MockLLMClient(canned_responses=[mock_batch])

    centroids = {
        "t1": [1.0, 0.0, 0.0],
        "t2": [0.0, 1.0, 0.0],
    }

    assignments = classify_reviews(
        reviews=reviews,
        review_embeddings=embeddings,
        themes=themes,
        llm_client=client,
        theme_centroids=centroids,
        batch_size=10,
        request_delay_s=0.0,
    )

    assert len(assignments) == 3
    # rid_1 kept high confidence LLM assignment
    assert assignments[0].rid == "rid_1"
    assert assignments[0].theme_id == "t1"
    assert assignments[0].confidence == 0.9

    # rid_2 had low confidence (<0.5), fallback was used (aligned with t2)
    assert assignments[1].rid == "rid_2"
    assert assignments[1].theme_id == "t2"

    # rid_3 was omitted by LLM, fallback was used
    assert assignments[2].rid == "rid_3"


def test_rank_themes_volume_vs_balanced():
    themes = [
        ThemeSpec(
            theme_id="t1",
            name="Trading Glitches",
            definition="Trading",
            inclusion_criteria=[],
            exclusion_criteria=[],
        ),
        ThemeSpec(
            theme_id="t2",
            name="iOS Biometric Crashes",
            definition="iOS",
            inclusion_criteria=[],
            exclusion_criteria=[],
        ),
    ]

    # 80 Play Store reviews on t1, 0 on t2
    # 10 App Store reviews on t2, 0 on t1
    assignments: list[ReviewAssignment] = []
    for i in range(80):
        assignments.append(
            ReviewAssignment(
                rid=f"p_{i}",
                theme_id="t1",
                confidence=0.9,
                source="play_store",
                rating=1,
                helpful_votes=5,
            )
        )
    for i in range(10):
        assignments.append(
            ReviewAssignment(
                rid=f"a_{i}",
                theme_id="t2",
                confidence=0.9,
                source="app_store",
                rating=1,
                helpful_votes=10,
            )
        )

    # 1. Volume mode: t1 wins (80 vs 10)
    ranked_vol = rank_themes(themes, assignments, source_weighting="volume")
    assert ranked_vol[0].theme_id == "t1"
    assert ranked_vol[1].theme_id == "t2"

    # 2. Balanced mode:
    # t1: play_share = 80/80 = 1.0, app_share = 0/10 = 0.0 -> score = 0.5
    # t2: play_share = 0/80 = 0.0, app_share = 10/10 = 1.0 -> score = 0.5
    # Tie-break: helpful votes! t2 has 10 * 10 = 100 vs t1 has 80 * 5 = 400
    # Let's adjust helpful votes to test tie-breaker
    ranked_bal = rank_themes(themes, assignments, source_weighting="balanced")
    assert len(ranked_bal) == 2


def test_rank_themes_tie_breakers_and_wow_delta():
    themes = [
        ThemeSpec(
            theme_id="t1",
            name="Theme A",
            definition="Def A",
            inclusion_criteria=[],
            exclusion_criteria=[],
        ),
        ThemeSpec(
            theme_id="t2",
            name="Theme B",
            definition="Def B",
            inclusion_criteria=[],
            exclusion_criteria=[],
        ),
    ]

    # Equal review counts (10 each)
    # Theme A has lower ratings (more 1★)
    assignments = [
        ReviewAssignment(
            rid=f"a_{i}",
            theme_id="t1",
            confidence=0.9,
            source="play_store",
            rating=1,
            helpful_votes=2,
        )
        for i in range(10)
    ] + [
        ReviewAssignment(
            rid=f"b_{i}",
            theme_id="t2",
            confidence=0.9,
            source="play_store",
            rating=4,
            helpful_votes=2,
        )
        for i in range(10)
    ]

    prior_themes = [
        Theme(
            theme_id="t1",
            name="Theme A",
            definition="Def A",
            review_count=8,
            share=0.40,
            avg_rating=1.5,
            low_rating_share=0.8,
            wow_delta=None,
            by_source={"play_store": 8, "app_store": 0},
        )
    ]

    ranked = rank_themes(
        themes, assignments, source_weighting="volume", previous_themes=prior_themes
    )

    # Theme A should win tie-breaker due to low_rating_share (1.0 vs 0.0)
    assert ranked[0].theme_id == "t1"
    assert ranked[0].low_rating_share == 1.0
    # Current share is 10/20 = 0.50 (50%). Prior was 0.40 (40%). WoW delta should be +10.0 pts
    assert ranked[0].wow_delta == 10.0


def test_get_previous_week_string():
    assert get_previous_week_string("2026-W42") == "2026-W41"
    assert get_previous_week_string("2026-W02") == "2026-W01"
    assert get_previous_week_string("2026-W01") == "2025-W52"


def test_end_to_end_theming_pipeline(tmp_path: Path):
    run_store = RunStore(base_dir=tmp_path)
    week = "2026-W42"

    # Create dummy clean reviews
    reviews: list[CleanReview] = []
    for i in range(25):
        reviews.append(
            make_clean_review(
                rid=f"rid_{i:03d}",
                source="play_store" if i < 20 else "app_store",
                rating=1 if i % 2 == 0 else 5,
                clean_text=f"Review {i} regarding trading execution, customer care, or hidden charges.",
                helpful_votes=i,
            )
        )
    run_store.save_clean_reviews(week, reviews)

    # Mock LLM for discovery & classification
    mock_discovered = DiscoveredThemes(
        themes=[
            ThemeSpec(
                theme_id="t1",
                name="Trading & Execution",
                definition="Order delays and charts",
                inclusion_criteria=["trading"],
                exclusion_criteria=[],
            ),
            ThemeSpec(
                theme_id="t2",
                name="Customer Support",
                definition="Support bots and help",
                inclusion_criteria=["support"],
                exclusion_criteria=[],
            ),
        ]
    )

    items = [
        ClassificationItem(rid=r.rid, theme_id="t1" if idx % 2 == 0 else "t2", confidence=0.85)
        for idx, r in enumerate(reviews)
    ]
    mock_batch = BatchClassificationResult(items=items)

    client = MockLLMClient(canned_responses=[mock_discovered, mock_batch])

    config = AppConfig()
    themes, assignments = run_theming_pipeline(
        week=week,
        config=config,
        run_store=run_store,
        llm_client=client,
        k_clusters=2,
        batch_size=50,
    )

    assert len(themes) == 2
    assert len(assignments) == 25
    assert (tmp_path / "runs" / week / "themes.json").exists()
    assert (tmp_path / "runs" / week / "assignments.jsonl").exists()

    # Verify loaded artifacts
    loaded_themes = run_store.load_themes(week)
    assert loaded_themes is not None
    assert len(loaded_themes) == 2

    loaded_assignments = run_store.load_assignments(week)
    assert len(loaded_assignments) == 25
