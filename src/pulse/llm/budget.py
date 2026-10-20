"""Persistent daily LLM budget: remembers Groq limits across runs and processes.

The ledger lives in `<data_dir>/llm_budget.json` and resets at 00:00 UTC. It tracks
- pipeline runs started today (capped by `max_runs_per_day`),
- tokens spent today (capped by `daily_token_limit` x `safety_margin`),
- the token cost of recent runs, used to estimate whether another run still fits,
- a `blocked_until` timestamp set when Groq itself reports the daily quota is gone
  (covers usage by other scripts or keys that this ledger cannot see).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pulse.config import BudgetConfig
from pulse.observability.logging import get_logger

logger = get_logger("pulse.llm.budget")

HISTORY_LEN = 5
MAX_BLOCK = timedelta(hours=24)


class BudgetExceededError(RuntimeError):
    """The daily run or token budget does not allow another LLM run right now."""


class DailyQuotaExceededError(BudgetExceededError):
    """Groq answered 429 because the account's tokens-per-day quota is exhausted."""


def parse_retry_seconds(text: str) -> float | None:
    """Seconds from Groq's 'try again in 1h2m3.4s' hint, or None when absent."""
    m = re.search(r"try again in\s+([0-9hms.]+)", text)
    if not m:
        return None
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    parts = re.findall(r"([\d.]+)(ms|h|m|s)", m.group(1))
    if not parts:
        return None
    return sum(float(v) * units[u] for v, u in parts)


def _empty_state(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "runs": 0,
        "tokens": 0,
        "current_run_tokens": 0,
        "run_tokens": [],
        "blocked_until": None,
    }


class LLMBudget:
    """File-backed daily run/token ledger."""

    def __init__(
        self,
        path: Path | str,
        config: BudgetConfig,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = Path(path)
        self.config = config
        self._now = now

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict[str, Any]:
        day = self._now().date().isoformat()
        state: dict[str, Any] = _empty_state(day)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state.update(raw)
        except (OSError, ValueError):
            pass
        if state["day"] != day:  # new UTC day: counters and block reset, history is kept
            history = list(state.get("run_tokens", []))
            if state.get("current_run_tokens", 0) > 0:
                history.append(int(state["current_run_tokens"]))
            state = _empty_state(day)
            state["run_tokens"] = history[-HISTORY_LEN:]
        return state

    def _save(self, state: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("llm_budget_save_failed", error=str(exc))

    # ------------------------------------------------------------------ derived values
    @property
    def token_allowance(self) -> int:
        return int(self.config.daily_token_limit * self.config.safety_margin)

    @staticmethod
    def _estimate(state: dict[str, Any], default: int) -> int:
        recent = [int(t) for t in state.get("run_tokens", [])[-3:]]
        if state.get("current_run_tokens", 0) > 0:  # the latest run is not in history yet
            recent.append(int(state["current_run_tokens"]))
        return max(recent) if recent else default

    def _blocked_until(self, state: dict[str, Any]) -> datetime | None:
        raw = state.get("blocked_until")
        if not raw:
            return None
        try:
            until = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return until if until > self._now() else None

    def status(self) -> dict[str, Any]:
        state = self._load()
        used = int(state["tokens"])
        blocked = self._blocked_until(state)
        return {
            "day": state["day"],
            "runs_used": int(state["runs"]),
            "runs_max": self.config.max_runs_per_day,
            "tokens_used": used,
            "tokens_allowance": self.token_allowance,
            "tokens_remaining": max(0, self.token_allowance - used),
            "estimated_run_tokens": self._estimate(state, self.config.default_run_tokens),
            "blocked_until": blocked.isoformat() if blocked else None,
        }

    # ------------------------------------------------------------------ gates
    def begin_run(self) -> None:
        """Reserve one of today's runs, or raise BudgetExceededError with the reason."""
        state = self._load()
        self._raise_if_blocked(state)

        if state["runs"] >= self.config.max_runs_per_day:
            raise BudgetExceededError(
                f"Daily run limit reached ({state['runs']}/{self.config.max_runs_per_day}). "
                "The budget resets at 00:00 UTC."
            )
        estimate = self._estimate(state, self.config.default_run_tokens)
        remaining = self.token_allowance - int(state["tokens"])
        if remaining < estimate:
            raise BudgetExceededError(
                f"Not enough daily tokens left: ~{remaining:,} remaining, a run needs "
                f"~{estimate:,} (allowance {self.token_allowance:,}). Resets at 00:00 UTC."
            )

        if state["current_run_tokens"] > 0:
            state["run_tokens"] = [*state["run_tokens"], int(state["current_run_tokens"])][
                -HISTORY_LEN:
            ]
        state["current_run_tokens"] = 0
        state["runs"] += 1
        self._save(state)
        logger.info("llm_run_reserved", runs=state["runs"], tokens_used=state["tokens"])

    def assert_available(self) -> None:
        """Per-call guard: stop immediately once Groq's quota is gone or the limit is hit."""
        state = self._load()
        self._raise_if_blocked(state)
        if state["tokens"] >= self.config.daily_token_limit:
            raise BudgetExceededError(
                f"Daily token limit reached ({state['tokens']:,}/{self.config.daily_token_limit:,})."
            )

    def _raise_if_blocked(self, state: dict[str, Any]) -> None:
        until = self._blocked_until(state)
        if until is not None:
            raise DailyQuotaExceededError(
                f"Groq daily token quota is exhausted; retry after {until:%Y-%m-%d %H:%M} UTC."
            )

    # ------------------------------------------------------------------ recording
    def record_tokens(self, tokens: int) -> None:
        if tokens <= 0:
            return
        state = self._load()
        state["tokens"] += tokens
        state["current_run_tokens"] += tokens
        self._save(state)

    def mark_exhausted(self, retry_after_s: float | None = None) -> None:
        """Remember that Groq refused us for the day, so later runs fail fast."""
        now = self._now()
        tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
        until = (
            now + min(timedelta(seconds=retry_after_s), MAX_BLOCK) if retry_after_s else tomorrow
        )
        state = self._load()
        state["blocked_until"] = until.isoformat()
        self._save(state)
        logger.warning("llm_daily_quota_exhausted", blocked_until=until.isoformat())


def budget_from_settings(settings: Any) -> LLMBudget | None:
    """The ledger for this deployment's data dir, or None when the budget is disabled."""
    cfg: BudgetConfig = settings.app_config.llm.budget
    if not cfg.enabled:
        return None
    return LLMBudget(Path(settings.data_dir) / "llm_budget.json", cfg)
