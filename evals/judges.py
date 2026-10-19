"""LLM-as-judge runner: strict JSON, temperature 0, 3 repeats, median score."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from evals.metrics import median_with_spread
from pulse.models import CleanReview, Pulse

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient

PROMPT_DIR = Path(__file__).parent / "prompts"
JUDGE_PROMPT_VERSION = "v1"
REPEATS = 3


class JudgeScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    reason: str
    evidence: list[str] = Field(default_factory=list)


@dataclass
class JudgeResult:
    item: str
    score: float
    spread: float
    reasons: list[str] = field(default_factory=list)

    @property
    def low_agreement(self) -> bool:
        return self.spread > 1


def _prompt(name: str, **values: str) -> str:
    return (PROMPT_DIR / f"judge_{name}.md").read_text(encoding="utf-8").format(**values)


def judge_once_repeated(llm: LLMClient, prompt: str, item: str) -> JudgeResult:
    scores: list[int] = []
    reasons: list[str] = []
    for _ in range(REPEATS):
        res = llm.complete_json(prompt, JudgeScore, temperature=0.0)
        scores.append(res.score)
        reasons.append(res.reason)
    med, spread = median_with_spread(scores)
    return JudgeResult(item=item, score=med, spread=spread, reasons=reasons)


def _sample_reviews(reviews: list[CleanReview], rids: set[str], n: int = 10) -> str:
    members = sorted((r for r in reviews if r.rid in rids), key=lambda r: r.rid)
    picked = random.Random(0).sample(members, min(n, len(members)))
    return "\n".join(f"- ({r.rating} stars) {r.clean_text[:300]}" for r in picked)


def judge_theme_coherence(
    llm: LLMClient,
    pulse: Pulse,
    reviews: list[CleanReview],
    assignments: list[dict[str, Any]],
) -> list[JudgeResult]:
    out: list[JudgeResult] = []
    for t in pulse.top_themes:
        rids = {a["rid"] for a in assignments if a["theme_id"] == t.theme_id}
        prompt = _prompt(
            "theme_coherence",
            theme_name=t.name,
            theme_definition=t.definition,
            reviews=_sample_reviews(reviews, rids),
        )
        out.append(judge_once_repeated(llm, prompt, t.theme_id))
    return out


def judge_action_groundedness(
    llm: LLMClient,
    pulse: Pulse,
    reviews: list[CleanReview],
    assignments: list[dict[str, Any]],
) -> list[JudgeResult]:
    themes = {t.theme_id: t for t in pulse.top_themes}
    out: list[JudgeResult] = []
    for a in pulse.actions:
        t = themes[a.theme_id]
        rids = {x["rid"] for x in assignments if x["theme_id"] == t.theme_id}
        quotes = "\n".join(f'- "{q.text}"' for q in pulse.quotes if q.theme_id == t.theme_id)
        prompt = _prompt(
            "action_groundedness",
            action_title=a.title,
            action_rationale=a.rationale,
            theme_name=t.name,
            theme_definition=t.definition,
            theme_stats=(
                f"{t.share * 100:.1f}% of reviews, {t.low_rating_share * 100:.1f}% are 1-2 stars"
            ),
            quotes=quotes or "(none)",
            reviews=_sample_reviews(reviews, rids),
        )
        out.append(judge_once_repeated(llm, prompt, a.theme_id))
    return out


def judge_pulse_usefulness(llm: LLMClient, pulse: Pulse) -> JudgeResult:
    return judge_once_repeated(llm, _prompt("pulse_usefulness", pulse=pulse.markdown), pulse.week)


def mean_score(results: list[JudgeResult]) -> float:
    return sum(r.score for r in results) / len(results) if results else 0.0
