"""Tests for the rolling LLM token budget and its Groq-client integration."""

from __future__ import annotations

import json
import os
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
    parse_groq_used,
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


def test_runs_age_out_of_the_rolling_24h_window(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock, max_runs_per_day=1)
    b.begin_run()
    clock.t += timedelta(hours=23)  # a UTC midnight has passed, but 24h have not
    with pytest.raises(BudgetExceededError, match="last 24h"):
        b.begin_run()
    clock.t += timedelta(hours=1, seconds=1)
    b.begin_run()
    assert b.status()["runs_used"] == 1


def test_tokens_refill_continuously_not_at_midnight(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock, daily_token_limit=240_000, safety_margin=1.0)  # 10k tokens/hour
    b.begin_run()
    b.record_tokens(100_000)
    assert b.status()["tokens_used"] == 100_000
    clock.t += timedelta(hours=3)
    st = b.status()
    assert st["tokens_used"] == 70_000  # 30k refilled; nothing special happened at 00:00
    assert st["tokens_refill_per_hour"] == 10_000


def test_status_says_when_the_next_run_fits(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock, daily_token_limit=240_000, safety_margin=1.0)  # 10k tokens/hour
    b.begin_run()
    b.record_tokens(230_000)  # the run's own cost (230k) is the next-run estimate; 10k are left
    st = b.status()
    # 230k needed - 10k left = 220k short -> 22h of refill at 10k/hour
    assert st["next_run_possible_at"] == (clock.t + timedelta(hours=22)).isoformat()
    with pytest.raises(BudgetExceededError, match="about 22h 0m"):
        b.begin_run()
    clock.t += timedelta(hours=22)
    assert b.status()["next_run_possible_at"] is None


def test_parse_groq_used() -> None:
    msg = "on tokens per day (TPD): Limit 200000, Used 199124, Requested 6073. Please try again"
    assert parse_groq_used(msg) == 199124
    assert parse_groq_used("tokens per minute (TPM): Limit 8000, Used 7000") is None


def test_ledger_adopts_the_usage_groq_reports_when_it_undercounted(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock, daily_token_limit=200_000)
    b.begin_run()
    b.record_tokens(132_576)  # what this ledger saw
    msg = "tokens per day (TPD): Limit 200000, Used 199124, Requested 6073. try again in 37m25s"
    b.mark_exhausted(retry_after_s=2245, groq_message=msg)
    assert b.status()["tokens_used"] == 199_124  # Groq's own figure wins


def test_legacy_day_based_ledger_is_carried_over(tmp_path: Path) -> None:
    clock = Clock()  # 2026-10-20 09:00 UTC
    legacy = {
        "day": "2026-10-20",
        "runs": 2,
        "tokens": 50_000,
        "current_run_tokens": 5_000,
        "run_tokens": [49_000],
        "blocked_until": None,
    }
    (tmp_path / "llm_budget.json").write_text(json.dumps(legacy), encoding="utf-8")
    st = make(tmp_path, clock, max_runs_per_day=4).status()
    assert st["runs_used"] == 2
    assert 0 < st["tokens_used"] <= 50_000  # kept (net of any refill since the file was written)


def test_a_previous_days_legacy_ledger_is_not_counted(tmp_path: Path) -> None:
    legacy = {"day": "2026-10-19", "runs": 3, "tokens": 150_000, "run_tokens": []}
    (tmp_path / "llm_budget.json").write_text(json.dumps(legacy), encoding="utf-8")
    st = make(tmp_path, Clock()).status()
    assert st["runs_used"] == 0 and st["tokens_used"] == 0


def test_token_estimate_is_learned_from_recorded_usage(tmp_path: Path) -> None:
    clock = Clock()
    b = make(
        tmp_path, clock, daily_token_limit=100_000, safety_margin=1.0, default_run_tokens=10_000
    )
    b.begin_run()
    b.record_tokens(60_000)
    # The next run is estimated from the last recorded one (60k), but only 40k is left.
    with pytest.raises(BudgetExceededError, match="Not enough tokens"):
        b.begin_run()


def test_run_cut_short_by_the_limit_does_not_block_once_tokens_refill(tmp_path: Path) -> None:
    """A run stopped at the limit records ~the whole allowance; once refilled it must still start."""
    clock = Clock()
    b = make(tmp_path, clock, daily_token_limit=200_000, safety_margin=0.9)
    b.begin_run()
    b.record_tokens(202_532)  # the aborted run overshot the 180k allowance
    clock.t += timedelta(days=1, hours=1)  # the bucket has fully drained
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
    with pytest.raises(BudgetExceededError, match="Not enough tokens"):
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


def test_exhaustion_without_hint_blocks_for_an_hour(tmp_path: Path) -> None:
    clock = Clock()
    b = make(tmp_path, clock)
    b.mark_exhausted(None)
    assert b.status()["blocked_until"] == (clock.t + timedelta(hours=1)).isoformat()


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


def test_old_ledger_that_learned_a_200k_run_does_not_block_a_cheaper_run(tmp_path: Path) -> None:
    """The live ledger had recorded a 202k run (one-model design); a ~30k run must be allowed."""
    clock = Clock()  # 2026-10-20 09:00 UTC
    legacy = {
        "day": "2026-10-20",
        "runs": 1,
        "tokens": 202_532,
        "current_run_tokens": 202_532,
        "run_tokens": [49_000, 202_532],
        "blocked_until": None,
    }
    path = tmp_path / "llm_budget.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    old = clock.t - timedelta(hours=8)
    os.utime(path, (old.timestamp(), old.timestamp()))  # written 8h ago: ~66k has refilled
    b = make(tmp_path, clock, default_run_tokens=30_000)
    st = b.status()
    assert st["estimated_run_tokens"] == 30_000  # not the stale 180k
    assert st["tokens_remaining"] >= 30_000
    b.begin_run()  # was: "Not enough daily tokens left: ~-22,532 remaining ..."


@respx.mock
def test_quota_error_message_is_plain_english(tmp_path: Path) -> None:
    b = make(tmp_path, Clock())
    client = GroqLocalEmbedClient(
        api_key="k", chat_model="openai/gpt-oss-120b", base_url="https://groq.test/v1", budget=b
    )
    respx.post("https://groq.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            text='{"error":{"message":"Rate limit reached ... on tokens per day (TPD): '
            'Limit 200000, Used 199958. Please try again in 37m25.1s."}}',
        )
    )
    with pytest.raises(DailyQuotaExceededError) as caught:
        client.complete_json("hi", ClassificationItem)
    msg = str(caught.value)
    assert "openai/gpt-oss-120b" in msg and "about 37m" in msg
    assert "{" not in msg and "org_" not in msg  # no raw JSON or account ids shown to the user
