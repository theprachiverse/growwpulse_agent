"""Persistent LLM budget that mirrors how Groq meters tokens-per-day.

Groq's daily limit is a rolling bucket, not a midnight reset: it refills continuously at
`daily_token_limit / 86400` tokens per second (~8.3k/hour for 200k/day). The ledger lives in
`<data_dir>/llm_budget.json` and tracks
- tokens spent, as a bucket level that drains at that rate (a run must fit under
  `daily_token_limit` x `safety_margin`),
- pipeline runs started in the last 24 hours (capped by `max_runs_per_day`),
- the token cost of recent runs, used to estimate whether another run still fits,
- a `blocked_until` timestamp set when Groq itself reports the quota is gone, plus the bucket
  level Groq reports in that message, so usage this ledger cannot see (other scripts, the
  playground, timed-out calls that still billed) is picked up.
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
WINDOW = timedelta(hours=24)
DAY_SECONDS = 86_400.0
NO_HINT_BLOCK = timedelta(hours=1)  # Groq always sends a hint for daily limits; this is a fallback


class BudgetExceededError(RuntimeError):
    """The LLM run or token budget does not allow another LLM run right now."""


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


def parse_groq_used(text: str) -> int | None:
    """Tokens Groq says are already used, from '... (TPD): Limit 200000, Used 199124, ...'."""
    m = re.search(r"\(TPD\):[^.]*?Used\s+(\d+)", text)
    return int(m.group(1)) if m else None


def _empty_state(day: str, now: datetime) -> dict[str, Any]:
    return {
        "day": day,
        "used": 0.0,  # bucket level (tokens spent, net of refill) at `used_at`
        "used_at": now.isoformat(),
        "run_starts": [],
        "current_run_tokens": 0,
        "run_tokens": [],
        "blocked_until": None,
    }


def _parse_time(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _fmt_wait(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if hours else f"{mins}m"


class LLMBudget:
    """File-backed rolling run/token ledger."""

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
    @property
    def refill_per_s(self) -> float:
        return self.config.daily_token_limit / DAY_SECONDS

    def _migrate_legacy(self, raw: dict[str, Any], state: dict[str, Any], now: datetime) -> None:
        """Older ledgers counted tokens/runs per UTC day; carry today's figures over once.

        Their learned per-run costs came from the one-model design (a run cost ~200k, the whole
        allowance), so they are dropped: kept, they would block every run until the bucket drained.
        """
        if "used_at" in raw:
            return
        state["run_tokens"] = []
        state["current_run_tokens"] = 0
        if raw.get("day") != now.date().isoformat():
            return
        try:
            stamp = datetime.fromtimestamp(self.path.stat().st_mtime, UTC)
        except OSError:
            stamp = now
        state["used"] = float(raw.get("tokens", 0) or 0)
        state["used_at"] = stamp.isoformat()
        state["run_starts"] = [stamp.isoformat()] * int(raw.get("runs", 0) or 0)

    def _load(self) -> dict[str, Any]:
        now = self._now()
        day = now.date().isoformat()
        state: dict[str, Any] = _empty_state(day, now)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state.update({k: v for k, v in raw.items() if k in state})
                self._migrate_legacy(raw, state, now)
        except (OSError, ValueError):
            pass

        if state["day"] != day:  # new UTC day: roll the finished run into the learned history
            history = list(state.get("run_tokens", []))
            if state.get("current_run_tokens", 0) > 0:
                history.append(int(state["current_run_tokens"]))
            state["run_tokens"] = history[-HISTORY_LEN:]
            state["current_run_tokens"] = 0
            state["day"] = day

        # Drain the bucket for the time since it was last written; drop run starts older than 24h.
        used_at = _parse_time(state["used_at"]) or now
        elapsed = max(0.0, (now - used_at).total_seconds())
        state["used"] = max(0.0, float(state["used"]) - elapsed * self.refill_per_s)
        state["used_at"] = now.isoformat()
        starts = (_parse_time(s) for s in state["run_starts"])
        state["run_starts"] = [s.isoformat() for s in starts if s and now - s < WINDOW]
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

    def _estimate(self, state: dict[str, Any]) -> int:
        recent = [int(t) for t in state.get("run_tokens", [])[-3:]]
        if state.get("current_run_tokens", 0) > 0:  # the latest run is not in history yet
            recent.append(int(state["current_run_tokens"]))
        if not recent:
            return self.config.default_run_tokens
        # A run cut short by the quota records about the whole allowance; learning that figure
        # uncapped would make every later run look unaffordable, even once tokens have refilled.
        return min(max(recent), self.token_allowance)

    def _blocked_until(self, state: dict[str, Any]) -> datetime | None:
        raw = state.get("blocked_until")
        if not raw:
            return None
        until = _parse_time(raw)
        return until if until and until > self._now() else None

    def _seconds_until_fits(self, state: dict[str, Any], need: int) -> float:
        """Seconds until `need` tokens fit under the allowance again (0 when they already do)."""
        deficit = float(state["used"]) + need - self.token_allowance
        return max(0.0, deficit / self.refill_per_s) if self.refill_per_s > 0 else 0.0

    def status(self, estimate: int | None = None) -> dict[str, Any]:
        """Ledger state; `estimate` overrides the learned per-run cost (used for capped classify)."""
        state = self._load()
        blocked = self._blocked_until(state)
        estimate = estimate if estimate is not None else self._estimate(state)
        wait = self._seconds_until_fits(state, estimate)
        used = int(state["used"])
        return {
            "day": state["day"],
            "window": "rolling_24h",
            "runs_used": len(state["run_starts"]),
            "runs_max": self.config.max_runs_per_day,
            "tokens_used": used,
            "tokens_allowance": self.token_allowance,
            "tokens_remaining": max(0, self.token_allowance - used),
            "tokens_refill_per_hour": int(self.refill_per_s * 3600),
            "estimated_run_tokens": estimate,
            "next_run_possible_at": (
                (self._now() + timedelta(seconds=wait)).isoformat() if wait > 0 else None
            ),
            "tokens_per_minute": self.config.tokens_per_minute,
            "cost_model": {
                "per_review": self.config.est_tokens_per_review,
                "per_batch": self.config.est_tokens_per_batch,
                "fixed": self.config.est_fixed_tokens,
            },
            "blocked_until": blocked.isoformat() if blocked else None,
        }

    # ------------------------------------------------------------------ gates
    def begin_run(self) -> None:
        """Reserve one of the runs allowed per rolling 24h, or raise with the reason."""
        state = self._load()
        self._raise_if_blocked(state)

        starts = state["run_starts"]
        if len(starts) >= self.config.max_runs_per_day:
            oldest = _parse_time(starts[0])
            when = (
                f" The oldest leaves the window at {oldest + WINDOW:%H:%M} UTC." if oldest else ""
            )
            raise BudgetExceededError(
                f"Run limit reached ({len(starts)}/{self.config.max_runs_per_day} "
                f"in the last 24h).{when}"
            )
        estimate = self._estimate(state)
        remaining = self.token_allowance - int(state["used"])
        if remaining < estimate:
            wait = self._seconds_until_fits(state, estimate)
            raise BudgetExceededError(
                f"Not enough tokens left: ~{remaining:,} remaining, a run needs ~{estimate:,} "
                f"(allowance {self.token_allowance:,}). Tokens refill gradually; enough will be "
                f"back in about {_fmt_wait(wait)}."
            )

        if state["current_run_tokens"] > 0:
            state["run_tokens"] = [*state["run_tokens"], int(state["current_run_tokens"])][
                -HISTORY_LEN:
            ]
        state["current_run_tokens"] = 0
        state["run_starts"] = [*starts, self._now().isoformat()]
        self._save(state)
        logger.info(
            "llm_run_reserved", runs=len(state["run_starts"]), tokens_used=int(state["used"])
        )

    def assert_available(self) -> None:
        """Per-call guard: stop immediately once Groq's quota is gone or the limit is hit."""
        state = self._load()
        self._raise_if_blocked(state)
        if state["used"] >= self.config.daily_token_limit:
            raise BudgetExceededError(
                f"Token limit reached ({int(state['used']):,}/{self.config.daily_token_limit:,})."
            )

    def _raise_if_blocked(self, state: dict[str, Any]) -> None:
        until = self._blocked_until(state)
        if until is not None:
            raise DailyQuotaExceededError(
                f"Groq token quota is exhausted; retry after {until:%Y-%m-%d %H:%M} UTC."
            )

    # ------------------------------------------------------------------ recording
    def record_tokens(self, tokens: int) -> None:
        if tokens <= 0:
            return
        state = self._load()
        state["used"] += tokens
        state["current_run_tokens"] += tokens
        self._save(state)

    def mark_exhausted(
        self, retry_after_s: float | None = None, groq_message: str | None = None
    ) -> None:
        """Remember that Groq refused us, so later runs fail fast; adopt Groq's own usage figure."""
        now = self._now()
        wait = min(timedelta(seconds=retry_after_s), MAX_BLOCK) if retry_after_s else NO_HINT_BLOCK
        until = now + wait
        state = self._load()
        state["blocked_until"] = until.isoformat()
        groq_used = parse_groq_used(groq_message or "")
        if groq_used is not None and groq_used > state["used"]:
            logger.warning(
                "llm_budget_synced_to_groq", ledger_used=int(state["used"]), groq_used=groq_used
            )
            state["used"] = float(groq_used)
        self._save(state)
        logger.warning("llm_quota_exhausted", blocked_until=until.isoformat())


def classify_budget(llm_cfg: Any, base_dir: Path | str) -> LLMBudget | None:
    """Separate ledger (`llm_budget_classify.json`) for the classification model's own quota."""
    if not llm_cfg.budget.enabled:
        return None
    c = llm_cfg.classify
    cfg = llm_cfg.budget.model_copy(
        update={
            "daily_token_limit": c.daily_token_limit,
            "tokens_per_minute": c.tokens_per_minute,
            "safety_margin": c.safety_margin,
        }
    )
    return LLMBudget(Path(base_dir) / "llm_budget_classify.json", cfg)


def budget_from_settings(settings: Any) -> LLMBudget | None:
    """The ledger for this deployment's data dir, or None when the budget is disabled."""
    cfg: BudgetConfig = settings.app_config.llm.budget
    if not cfg.enabled:
        return None
    return LLMBudget(Path(settings.data_dir) / "llm_budget.json", cfg)


def budget_status(settings: Any) -> dict[str, Any] | None:
    """Heavy-model ledger status, plus a `classify` entry when classification has its own model."""
    ledger = budget_from_settings(settings)
    if ledger is None:
        return None
    status = ledger.status()
    llm_cfg = settings.app_config.llm
    classify = classify_budget(llm_cfg, settings.data_dir) if llm_cfg.classify.model else None
    if classify is not None:
        c = llm_cfg.classify
        b = llm_cfg.budget
        # This ledger has no begin_run, so its learned history would be the whole window's spend;
        # size a run from the caps instead (0 = uncapped: keep the learned figure).
        capped = bool(c.max_reviews_per_run and c.max_residual_reviews)
        sent = c.max_reviews_per_run + c.max_residual_reviews
        batches = -(-sent // 50)  # default batch size
        estimate = sent * b.est_tokens_per_review + batches * b.est_tokens_per_batch
        status["classify"] = {
            **classify.status(estimate=estimate if capped else None),
            "model": c.model,
            "max_reviews": c.max_reviews_per_run,
            "max_residual_reviews": c.max_residual_reviews,
        }
    return status
