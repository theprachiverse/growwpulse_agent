"""Residual theme pass: recover a missed domain from a large 'other' bucket."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pulse.models import CleanReview
from pulse.theming.models import BatchClassificationResult, ReviewAssignment, ThemeSpec
from pulse.theming.residual import ResidualThemeResult, other_share, residual_pass


def _review(i: int, rating: int = 1) -> CleanReview:
    return CleanReview(
        rid=f"rid_{i:04d}",
        source="play_store",
        rating=rating,
        clean_text=f"review number {i} about something in the app",
        date=date(2026, 10, 10),
        iso_week="2026-W41",
    )


def _spec(tid: str, name: str) -> ThemeSpec:
    return ThemeSpec(theme_id=tid, name=name, definition=f"{name} issues.")


def _assign(rid: str, theme: str) -> ReviewAssignment:
    return ReviewAssignment(
        rid=rid, theme_id=theme, confidence=0.9, source="play_store", rating=1, helpful_votes=0
    )


class ScriptedLLM:
    """Answers residual discovery with `proposal`, and classification from `truth`."""

    def __init__(self, proposal: ResidualThemeResult, truth: dict[str, str]) -> None:
        self.proposal = proposal
        self.truth = truth
        self.discovery_calls = 0

    def complete_json(self, prompt: str, schema: Any, temperature: float = 0.0, **_: Any) -> Any:
        if schema is ResidualThemeResult:
            self.discovery_calls += 1
            return self.proposal
        assert schema is BatchClassificationResult
        rids = dict.fromkeys(re.findall(r"rid_\d{4}", prompt.split("Available theme IDs")[0]))
        items = [
            {"rid": r, "theme_id": self.truth.get(r, "other"), "confidence": 0.9} for r in rids
        ]
        return BatchClassificationResult.model_validate({"items": items})

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(len(t) % 7), 0.5] for t in texts]


def _setup(n_total: int = 100, n_other: int = 40, n_mf: int = 20):
    reviews = [_review(i) for i in range(n_total)]
    embs = [[1.0, 0.0, 0.0] for _ in reviews]
    themes = [_spec("t1", "Trading"), _spec("t2", "Support")]
    assignments = [_assign(r.rid, "other" if i < n_other else "t1") for i, r in enumerate(reviews)]
    truth = {f"rid_{i:04d}": "t3" for i in range(n_mf)}  # the hidden domain, inside 'other'
    return reviews, embs, themes, assignments, truth


def _proposal() -> ResidualThemeResult:
    return ResidualThemeResult(found=True, theme=_spec("tX", "Mutual Funds & Account Servicing"))


def test_accepts_new_theme_and_reduces_other() -> None:
    reviews, embs, themes, assignments, truth = _setup()
    llm = ScriptedLLM(_proposal(), truth)
    new_themes, new_assign = residual_pass(
        reviews, embs, themes, assignments, llm, max_themes=5, threshold=0.15, min_capture=0.03
    )
    assert [t.theme_id for t in new_themes] == ["t1", "t2", "t3"]  # id forced to next slot
    assert other_share(assignments) == 0.4
    assert other_share(new_assign) < 0.4
    assert sum(a.theme_id == "t3" for a in new_assign) == 20
    # non-'other' assignments are untouched
    before = {a.rid: a.theme_id for a in assignments if a.theme_id != "other"}
    assert all(next(x for x in new_assign if x.rid == r).theme_id == t for r, t in before.items())


def test_skips_when_other_small_or_no_free_slot() -> None:
    reviews, embs, themes, assignments, truth = _setup(n_other=5)
    llm = ScriptedLLM(_proposal(), truth)
    out = residual_pass(
        reviews, embs, themes, assignments, llm, max_themes=5, threshold=0.15, min_capture=0.03
    )
    assert out == (themes, assignments) and llm.discovery_calls == 0

    reviews, embs, themes, assignments, truth = _setup()
    llm = ScriptedLLM(_proposal(), truth)
    out = residual_pass(
        reviews, embs, themes, assignments, llm, max_themes=2, threshold=0.15, min_capture=0.03
    )
    assert out == (themes, assignments) and llm.discovery_calls == 0


def test_rejects_theme_that_captures_too_little() -> None:
    reviews, embs, themes, assignments, _ = _setup()
    truth = {"rid_0000": "t3"}  # only 1% of reviews
    llm = ScriptedLLM(_proposal(), truth)
    out = residual_pass(
        reviews, embs, themes, assignments, llm, max_themes=5, threshold=0.15, min_capture=0.03
    )
    assert out == (themes, assignments)


def test_no_theme_found_or_llm_failure_keeps_original() -> None:
    reviews, embs, themes, assignments, truth = _setup()
    none = ScriptedLLM(ResidualThemeResult(found=False), truth)
    assert residual_pass(
        reviews, embs, themes, assignments, none, max_themes=5, threshold=0.15, min_capture=0.03
    ) == (themes, assignments)

    class Boom(ScriptedLLM):
        def complete_json(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("rate limited")

    boom = Boom(_proposal(), truth)
    assert residual_pass(
        reviews, embs, themes, assignments, boom, max_themes=5, threshold=0.15, min_capture=0.03
    ) == (themes, assignments)
