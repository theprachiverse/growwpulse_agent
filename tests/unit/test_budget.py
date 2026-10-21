"""Tests for the persistent daily LLM budget and its Groq-client integration."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from pulse.config import BudgetConfig
from pulse.llm.budget import (
    BudgetExceededError,
    DailyQuotaExceededError,
    LLMBudget,
    parse_retry_seconds,
)
from pulse.llm.client import GroqLocalEmbedClient
from pulse.theming.models import ClassificationItem


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t


def make(tmp_path: Path, clock: Clock, **cfg: int | float) -> LLMBudget:
    return LLMBudget(tmp_path / "llm_budget.json", BudgetConfig(**cfg), now=clock)


def test_parse_retry_seconds() -> None:
    assert parse_retry_seconds("Please try again in 1h2m3.5s.") == pytest.approx(3723.5)
    assert parse_retry_seconds("try again in 28.5s") == pytest.approx(28.5)
    assert parse_retry_seconds("try again in 250ms") == pytest.approx(0.25)
    assert parse_retry_seconds("no hint here") is None


def test_run_limit_is_enforced_and_remembered_across_instances(tmp_path: Path) -> None:
    clock = Clock()
    for _ in range(3):
        make(tmp_path, clock, max_runs_per_day=3).begin_run()
    with pytest.raises(BudgetExceededError, match="3/3"):
        make(tmp_path, clock, max_runs_per_day=3).begin_run()


def test_budget_resets_on_the_next_utc_day(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock, max_runs_per_day=1)
    b.begin_run()
    with pytest.raises(BudgetExceededError):
        b.begin_run()
    clock.t += timedelta(days=1)
    b.begin_run()
    assert b.status()["runs_used"] == 1


def test_token_estimate_is_learned_from_recorded_usage(tmp_path: Path) -> None:
    clock = Clock()
    b = make(
        tmp_path, clock, daily_token_limit=100_000, safety_margin=1.0, default_run_tokens=10_000
    )
    b.begin_run()
    b.record_tokens(60_000)
    # The next run is estimated from the last recorded one (60k), but only 40k is left.
    with pytest.raises(BudgetExceededError, match="Not enough daily tokens"):
        b.begin_run()


def test_run_cut_short_by_the_daily_limit_does_not_block_the_next_day(tmp_path: Path) -> None:
    """A run stopped at the limit records ~the whole allowance; after the reset it must still start."""
    clock = Clock()
    b = make(tmp_path, clock, daily_token_limit=200_000, safety_margin=0.9)
    b.begin_run()
    b.record_tokens(202_532)  # the aborted run overshot the 180k allowance
    clock.t += timedelta(days=1)
    b.begin_run()  # was: "Not enough daily tokens left: ~180,000 remaining, a run needs ~202,532"
    assert b.status()["estimated_run_tokens"] == 180_000


def test_status_exposes_the_cost_model_for_the_ui(tmp_path: Path) -> None:
    st = make(tmp_path, Clock()).status()
    assert st["tokens_per_minute"] == 8_000
    assert set(st["cost_model"]) == {"per_review", "per_batch", "fixed"}


def test_default_estimate_used_before_any_history(tmp_path: Path) -> None:
    clock = Clock()
    b = make(
        tmp_path, clock, daily_token_limit=50_000, safety_margin=1.0, default_run_tokens=60_000
    )
    with pytest.raises(BudgetExceededError, match="Not enough daily tokens"):
        b.begin_run()


def test_exhaustion_blocks_until_reset_hint_then_clears(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock)
    b.mark_exhausted(retry_after_s=3600)
    with pytest.raises(DailyQuotaExceededError):
        b.begin_run()
    with pytest.raises(DailyQuotaExceededError):
        b.assert_available()
    clock.t += timedelta(hours=1, seconds=1)
    b.assert_available()


def test_exhaustion_without_hint_blocks_until_midnight_utc(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock)
    b.mark_exhausted(None)
    assert b.status()["blocked_until"].startswith("2026-10-21T00:00")


def test_corrupt_ledger_is_treated_as_empty(tmp_path: Path) -> None:
    (tmp_path / "llm_budget.json").write_text("{not json", encoding="utf-8")
    b = make(tmp_path, Clock())
    b.begin_run()
    assert b.status()["runs_used"] == 1


@respx.mock
def test_client_records_tokens_from_successful_calls(tmp_path: Path) -> None:
    b = make(tmp_path, Clock())
    client = GroqLocalEmbedClient(api_key="k", base_url="https://groq.test/v1", budget=b)
    body = ClassificationItem(rid="r", theme_id="t1", confidence=0.9).model_dump_json()
    respx.post("https://groq.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": body}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 300},
            },
        )
    )
    client.complete_json("hi", ClassificationItem)
    assert b.status()["tokens_used"] == 1000


def _ok(tokens: int = 1000) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": '{"rid":"r","theme_id":"t1"}'}}],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0},
        },
    )


@respx.mock
def test_reasoning_effort_is_sent_only_to_gpt_oss(tmp_path: Path) -> None:
    route = respx.post("https://groq.test/v1/chat/completions").mock(return_value=_ok())
    for model, expected in (("openai/gpt-oss-120b", "low"), ("llama-3.1-8b-instant", None)):
        client = GroqLocalEmbedClient(
            api_key="k",
            chat_model=model,
            base_url="https://groq.test/v1",
            reasoning_effort="low",
        )
        client.complete_json("hi", ClassificationItem)
        sent = json.loads(route.calls.last.request.content)
        assert sent.get("reasoning_effort") == expected


@respx.mock
def test_calls_are_paced_under_the_tokens_per_minute_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [0.0]
    slept: list[float] = []

    def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    monkeypatch.setattr("pulse.llm.client.time.monotonic", lambda: now[0])
    monkeypatch.setattr("pulse.llm.client.time.sleep", fake_sleep)
    respx.post("https://groq.test/v1/chat/completions").mock(return_value=_ok(7_000))
    b = make(tmp_path, Clock(), tokens_per_minute=8_000)
    client = GroqLocalEmbedClient(api_key="k", base_url="https://groq.test/v1", budget=b)

    client.complete_json("hi", ClassificationItem)  # 7k spent at t=0
    assert slept == []
    client.complete_json("hi", ClassificationItem)  # 7k + ~1k reserved for the reply > 8k
    assert len(slept) == 1 and 59 < slept[0] < 61  # waits for the first call to leave the window
    assert b.status()["tokens_used"] == 14_000


@respx.mock
def test_pacing_can_be_turned_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr("pulse.llm.client.time.sleep", slept.append)
    respx.post("https://groq.test/v1/chat/completions").mock(return_value=_ok(7_000))
    b = make(tmp_path, Clock(), tokens_per_minute=0)
    client = GroqLocalEmbedClient(api_key="k", base_url="https://groq.test/v1", budget=b)
    client.complete_json("hi", ClassificationItem)
    client.complete_json("hi", ClassificationItem)
    assert slept == []


@respx.mock
def test_client_fails_fast_and_remembers_when_groq_daily_quota_is_gone(tmp_path: Path) -> None:
    b = make(tmp_path, Clock())
    client = GroqLocalEmbedClient(api_key="k", base_url="https://groq.test/v1", budget=b)
    route = respx.post("https://groq.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            text='{"error":{"message":"Rate limit reached ... on tokens per day (TPD): '
            'Limit 200000, Used 199958. Please try again in 17m4.5s."}}',
        )
    )
    with pytest.raises(DailyQuotaExceededError):
        client.complete_json("hi", ClassificationItem)
    assert route.call_count == 1  # no retries against a spent daily quota
    assert b.status()["blocked_until"] is not None
    # A later call is refused locally without touching the network.
    with pytest.raises(DailyQuotaExceededError):
        client.complete_json("hi", ClassificationItem)
    assert route.call_count == 1
