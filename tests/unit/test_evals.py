"""Phase 6 tests: eval metrics, judges and the offline PII / publish suites."""

from __future__ import annotations

import pytest
from evals import judges
from evals.metrics import macro_f1, map_themes_to_golden, stability_jaccard
from evals.run_evals import (
    EvalResult,
    HarnessError,
    _expected_top3,
    eval_pii,
    eval_publish,
    exit_code,
    summarize,
)

from pulse.llm.client import MockLLMClient
from pulse.models import Theme
from pulse.preprocess.pii import scrub_pii
from tests.unit.test_publish import make_pulse


def test_hungarian_mapping_and_macro_f1_perfect() -> None:
    gold = {f"r{i}": "a" for i in range(6)} | {f"s{i}": "b" for i in range(6)}
    pred = {r: ("t9" if g == "a" else "t3") for r, g in gold.items()}  # ids differ from labels
    assert map_themes_to_golden(pred, gold) == {"other": "other", "t9": "a", "t3": "b"}
    f1, per = macro_f1(pred, gold)
    assert f1 == pytest.approx(1.0) and per["a"]["recall"] == 1.0


def test_unmatched_golden_label_scores_zero() -> None:
    gold = {"r1": "a", "r2": "a", "s1": "b", "s2": "b"}
    pred = {"r1": "t1", "r2": "t1", "s1": "t1", "s2": "t1"}  # one theme swallows both topics
    f1, per = macro_f1(pred, gold)
    assert per["a"]["f1"] > 0 and per["b"]["f1"] == 0.0
    assert f1 < 1.0


def test_stability_jaccard() -> None:
    a = {"r1": "x", "r2": "x", "r3": "y", "r4": "y"}
    assert stability_jaccard(a, {"r1": "p", "r2": "p", "r3": "q", "r4": "q"}) == pytest.approx(1.0)
    half = stability_jaccard(a, {"r1": "p", "r2": "p", "r3": "p", "r4": "p"})
    assert 0 < half < 1
    assert stability_jaccard(a, {}) == 0.0


def _theme(tid: str, count: int) -> Theme:
    return Theme(
        theme_id=tid,
        name=f"Theme {tid}",
        definition="d",
        review_count=count,
        share=0.1,
        avg_rating=3.0,
        low_rating_share=0.5,
        by_source={"play_store": count, "app_store": 0},
    )


def test_expected_top3_volume_with_tie_breakers() -> None:
    def a(rid: str, tid: str, rating: int, helpful: int = 0) -> dict:
        return {
            "rid": rid,
            "theme_id": tid,
            "source": "play_store",
            "rating": rating,
            "helpful_votes": helpful,
        }

    assignments = (
        [a(f"a{i}", "t1", 1) for i in range(5)]
        + [a(f"b{i}", "t2", 5) for i in range(5)]  # same volume, lower severity
        + [a(f"c{i}", "t3", 1) for i in range(3)]
        + [a(f"d{i}", "t4", 1) for i in range(1)]
        + [a(f"o{i}", "other", 1) for i in range(20)]  # biggest bucket, never in Top 3
    )
    themes = [_theme(t, 1) for t in ("t1", "t2", "t3", "t4")]
    assert _expected_top3(assignments, themes, "volume") == ["t1", "t2", "t3"]


def test_exit_code_and_summary() -> None:
    ok = EvalResult("A", "a", "hard", 1, "x", True)
    bad_soft = EvalResult("B", "b", "soft", 1, "x", False)
    bad_track = EvalResult("C", "c", "track", 1, "x", False)
    assert exit_code([ok, bad_soft, bad_track]) == 0
    bad_q = EvalResult("D", "d", "quality", 1, "x", False)
    assert exit_code([ok, bad_q]) == 1
    assert summarize([ok, bad_q, bad_soft])["quality_passed"] == 0
    assert summarize([ok, bad_q, bad_soft])["soft_warnings"] == 1


def test_pii_eval_tier_a_is_clean_and_negatives_untouched() -> None:
    results = {r.id: r for r in eval_pii()}
    assert results["EV-PII-01"].passed, results["EV-PII-01"].details
    assert results["EV-PII-04"].passed, results["EV-PII-04"].details
    assert results["EV-PII-05"].passed


def test_plus91_phone_without_separators_is_a_phone_not_aadhaar() -> None:
    out, hits = scrub_pii("reach me on +919876543210 please")
    assert out == "reach me on [PHONE] please" and hits == ["PHONE"]
    # a plain 12-digit number is still an Aadhaar
    assert scrub_pii("id 987654321098")[1] == ["AADHAAR"]


def test_publish_evals_all_pass_on_a_valid_pulse() -> None:
    results = eval_publish(make_pulse())
    failed = [(r.id, r.value) for r in results if not r.passed]
    assert not failed, failed


def test_judges_take_median_of_three_and_flag_disagreement() -> None:
    llm = MockLLMClient(
        canned_responses=[
            {"score": 2, "reason": "a"},
            {"score": 4, "reason": "b"},
            {"score": 5, "reason": "c"},
        ]
    )
    res = judges.judge_pulse_usefulness(llm, make_pulse())
    assert res.score == 4.0 and res.spread == 3.0 and res.low_agreement
    assert judges.mean_score([res]) == 4.0


def test_judge_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValueError):
        judges.JudgeScore(score=7, reason="x")


def test_missing_fixture_is_a_harness_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from evals.run_evals import _jsonl

    with pytest.raises(HarnessError):
        _jsonl(tmp_path / "nope.jsonl")
