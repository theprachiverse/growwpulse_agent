"""End-to-End Orchestrator for the Groww Review Pulse Agent (Phase 7).

Chains together the full weekly pipeline:
1. Pre-flight health check (GET /healthz on MCP server before LLM spend)
2. Ingestion (P1)
3. Preprocessing & PII scrubbing (P2)
4. Theming (P3)
5. Pulse Composition & Guardrails (P4)
6. MCP Publishing (P5)
7. Retention pruning (P7.5)
8. Manifest & Audit Logging (P7.4)

Supports:
- Automatic target week resolution (last completed ISO week)
- Resumption from any step via --from-step
- Concurrent run mutual exclusion via file locks (EC-OPS-05)
- Low volume threshold handling (EC-CMP-37)
- Safe dry-run execution
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import httpx

from pulse.compose.pipeline import run_compose_pipeline
from pulse.config import Settings, get_settings
from pulse.guardrails.validators import ValidationReport
from pulse.ingest.fetcher import build_sources, fetch_all
from pulse.models import CleanReview, Pulse, RawReview, RunManifest, Source, Theme
from pulse.observability.logging import bind_run_context, clear_run_context, get_logger
from pulse.preprocess.pipeline import process_and_persist_reviews
from pulse.publish.pipeline import PublishOptions, PublishOutcome, publish_pulse
from pulse.publish.validate import server_url
from pulse.run_store import RunStore
from pulse.theming.pipeline import run_theming_pipeline
from pulse.utils.weeks import last_completed_week, parse_week, week_start_date

if TYPE_CHECKING:
    from pulse.llm.client import LLMClient
    from pulse.publish.mcp_client import ToolCaller

logger = get_logger("pulse.orchestrator")

PipelineStep = Literal["ingest", "preprocess", "theming", "compose", "publish"]
VALID_STEPS: tuple[PipelineStep, ...] = ("ingest", "preprocess", "theming", "compose", "publish")


class LockError(RuntimeError):
    """Raised when another run for the same week is already in progress (EC-OPS-05)."""


class PreflightError(RuntimeError):
    """Raised when pre-flight checks fail before starting pipeline execution."""


@dataclass
class OrchestratorResult:
    """Summary result returned after pipeline execution."""

    manifest: RunManifest
    pulse: Pulse | None = None
    validation_report: ValidationReport | None = None
    publish_outcome: PublishOutcome | None = None
    step_timings: dict[str, float] = field(default_factory=dict)
    pruned_items: dict[str, list[str]] = field(default_factory=dict)


def is_pid_alive(pid: int) -> bool:
    """Checks whether a process with the given PID is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


@contextmanager
def acquire_run_lock(run_dir: Path, week: str) -> Generator[Path, None, None]:
    """Acquires a file lock in the week's run directory to prevent concurrent runs (EC-OPS-05)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_file = run_dir / ".lock"
    pid = os.getpid()

    if lock_file.exists():
        try:
            content = lock_file.read_text(encoding="utf-8").strip().splitlines()
            existing_pid_str = content[0] if content else ""
            existing_pid = int(existing_pid_str) if existing_pid_str.isdigit() else None
        except Exception:
            existing_pid = None

        if existing_pid is not None and not is_pid_alive(existing_pid):
            logger.warning("stale_run_lock_detected_cleaning_up", week=week, stale_pid=existing_pid)
            with suppress(OSError):
                lock_file.unlink()
        else:
            raise LockError(
                f"Run for week '{week}' is locked by active process PID {existing_pid or 'unknown'} "
                f"({lock_file}). If this is a stale lock from a crashed run, delete the file."
            )

    lock_file.write_text(f"{pid}\n{datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    try:
        yield lock_file
    finally:
        if lock_file.exists():
            with suppress(OSError):
                lock_file.unlink()


def check_mcp_health(mcp_server_url: str, timeout: float = 10.0) -> bool:
    """Performs a GET /healthz pre-flight check on the MCP server (Task 7.3)."""
    health_url = mcp_server_url.rstrip("/")
    if health_url.endswith("/mcp"):
        health_url = health_url[:-4] + "/healthz"
    elif not health_url.endswith("/healthz"):
        health_url = health_url + "/healthz"

    try:
        resp = httpx.get(health_url, timeout=timeout)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("mcp_health_check_failed", url=health_url, error=str(exc))
        return False


def prune_retention(
    run_store: RunStore,
    target_week: str,
    retention_weeks: int = 12,
) -> dict[str, list[str]]:
    """Prunes run artifacts and snapshots older than retention_weeks (Task 7.5, EC-OPS-09).

    Keeps run_manifest.json, pulse.json, pulse.md, eval_report.json, and publish_state.json forever.
    Deletes large files (reviews_clean.jsonl, assignments.jsonl) and old snapshots.
    """
    pruned_runs: list[str] = []
    pruned_snapshots: list[str] = []

    try:
        target_monday = week_start_date(target_week)
    except Exception:
        return {"pruned_runs": [], "pruned_snapshots": []}

    # 1. Prune data/runs/<week>/
    runs_dir = run_store.base_dir / "runs"
    if runs_dir.exists():
        for week_dir in runs_dir.iterdir():
            if not week_dir.is_dir():
                continue
            try:
                w_monday = week_start_date(week_dir.name)
            except Exception:
                continue

            age_weeks = (target_monday - w_monday).days // 7
            if age_weeks > retention_weeks:
                # Remove large clean reviews and assignments
                for heavy_file in ("reviews_clean.jsonl", "assignments.jsonl"):
                    target = week_dir / heavy_file
                    if target.exists():
                        target.unlink()
                        pruned_runs.append(f"{week_dir.name}/{heavy_file}")

    # 2. Prune data/snapshots/<source>/<week>.jsonl
    snapshots_dir = run_store.base_dir / "snapshots"
    if snapshots_dir.exists():
        for source_dir in snapshots_dir.iterdir():
            if not source_dir.is_dir():
                continue
            for snap_file in source_dir.glob("*.jsonl"):
                stem = snap_file.stem
                try:
                    w_monday = week_start_date(stem)
                except Exception:
                    continue

                age_weeks = (target_monday - w_monday).days // 7
                if age_weeks > retention_weeks:
                    snap_file.unlink()
                    pruned_snapshots.append(f"{source_dir.name}/{snap_file.name}")

    if pruned_runs or pruned_snapshots:
        logger.info(
            "retention_pruning_complete",
            pruned_runs_count=len(pruned_runs),
            pruned_snapshots_count=len(pruned_snapshots),
            retention_weeks=retention_weeks,
        )

    return {"pruned_runs": pruned_runs, "pruned_snapshots": pruned_snapshots}


def estimate_llm_cost(llm_usage: dict[str, int]) -> float:
    """Estimates approximate USD cost based on prompt and completion token counts."""
    # Blended estimate: ~$0.60 per 1M prompt tokens, ~$0.80 per 1M completion tokens
    prompt_tokens = llm_usage.get("tokens_prompt", 0)
    completion_tokens = llm_usage.get("tokens_completion", 0)
    cost = (prompt_tokens * 0.0000006) + (completion_tokens * 0.0000008)
    return round(cost, 4)


class Orchestrator:
    """Coordinates end-to-end execution of the review pulse pipeline."""

    def __init__(
        self,
        settings: Settings | None = None,
        run_store: RunStore | None = None,
        llm_client: LLMClient | None = None,
        mcp_client: ToolCaller | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.run_store = run_store or RunStore(base_dir=self.settings.data_dir)
        self._llm_client = llm_client
        self._mcp_client = mcp_client

    def get_llm(self, spends_tokens: bool = True) -> LLMClient:
        """The LLM client; reserves one of today's budgeted runs when a real client is built.

        Injected clients (tests, callers with their own limits) bypass the daily budget.
        """
        if self._llm_client is not None:
            return self._llm_client
        from pulse.llm.budget import budget_from_settings
        from pulse.llm.client import get_llm_client

        budget = budget_from_settings(self.settings)
        if budget is not None and spends_tokens:
            budget.begin_run()  # raises BudgetExceededError before any work or spend
        return get_llm_client(budget=budget)

    def run(
        self,
        week: str = "auto",
        window_weeks: int | None = None,
        dry_run: bool = False,
        from_step: str | None = None,
        send: bool = False,
        force_append: bool = False,
        resend: bool = False,
        retention_weeks: int = 12,
        skip_retention: bool = False,
        check_health: bool = True,
        batch_size: int | None = None,
    ) -> OrchestratorResult:
        """Execute the weekly pulse pipeline."""
        target_week = last_completed_week() if week == "auto" else week
        # Validate format
        parse_week(target_week)
        effective_window = window_weeks or self.settings.app_config.ingest.window_weeks

        if from_step and from_step not in VALID_STEPS:
            raise ValueError(
                f"Invalid --from-step '{from_step}'. Must be one of: {', '.join(VALID_STEPS)}"
            )

        run_id = f"run_{target_week}_{int(time.time())}"
        started_at = datetime.now(UTC)
        bind_run_context(run_id=run_id, week=target_week)
        logger.info(
            "pipeline_start",
            run_id=run_id,
            week=target_week,
            dry_run=dry_run,
            from_step=from_step,
        )

        run_dir = self.run_store.run_dir(target_week)
        timings: dict[str, float] = {}
        counts: dict[str, int] = {}
        coverage_start: dict[Source, date] = {}
        source_errors: dict[Source, str] = {}

        # 0. Acquire file lock (EC-OPS-05)
        with acquire_run_lock(run_dir, target_week):
            # Pre-flight check (Task 7.3) if publishing is active
            publish_active = not dry_run and self.settings.app_config.publish.email_mode != "off"
            if check_health and publish_active and (from_step is None or from_step == "publish"):
                mcp_url = server_url(self.settings)
                logger.info("preflight_health_check_start", url=mcp_url)
                if not check_mcp_health(mcp_url):
                    msg = (
                        f"Pre-flight health check failed for MCP server at {mcp_url}. "
                        "The server is unreachable or returned non-200. Pipeline aborted before LLM spend."
                    )
                    logger.error("preflight_health_check_failed", message=msg)
                    manifest = RunManifest(
                        run_id=run_id,
                        week=target_week,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        status="error",
                        source_errors=source_errors,
                    )
                    self.run_store.save_manifest(manifest)
                    raise PreflightError(msg)
                logger.info("preflight_health_check_ok")

            llm = self.get_llm(spends_tokens=from_step != "publish")
            clean_reviews: list[CleanReview] = []
            ranked_themes: list[Theme] = []
            pulse_obj: Pulse | None = None
            validation_report: ValidationReport | None = None
            publish_outcome: PublishOutcome | None = None
            raw_reviews: list[RawReview] = []

            # ---------------------------------------------------------------- Step 1: Ingest
            step_order = list(VALID_STEPS)
            start_idx = step_order.index(from_step) if from_step else 0

            if start_idx <= step_order.index("ingest"):
                t0 = time.perf_counter()
                bind_run_context(step="ingest")
                logger.info("step_ingest_start")
                sources = build_sources(self.settings.app_config)
                fetch_res = fetch_all(
                    sources=sources,
                    week=target_week,
                    n_weeks=effective_window,
                )
                raw_reviews = fetch_res.reviews
                coverage_start = fetch_res.coverage_start
                source_errors = fetch_res.source_errors
                counts["raw_reviews"] = len(raw_reviews)
                for src, cnt in fetch_res.counts_by_source().items():
                    counts[f"raw_{src}"] = cnt
                timings["ingest"] = round(time.perf_counter() - t0, 3)
                logger.info(
                    "step_ingest_complete", count=len(raw_reviews), duration_s=timings["ingest"]
                )

            # ---------------------------------------------------------------- Step 2: Preprocess
            if start_idx <= step_order.index("preprocess"):
                t0 = time.perf_counter()
                bind_run_context(step="preprocess")
                logger.info("step_preprocess_start")
                clean_reviews = process_and_persist_reviews(
                    raw_reviews=raw_reviews,
                    target_week=target_week,
                    n_weeks=effective_window,
                    config=self.settings.app_config,
                    run_store=self.run_store,
                    llm_client=llm,
                    coverage_start=coverage_start,
                    compute_embeddings=True,
                )
                counts["clean_reviews"] = len(clean_reviews)
                timings["preprocess"] = round(time.perf_counter() - t0, 3)
                logger.info(
                    "step_preprocess_complete",
                    count=len(clean_reviews),
                    duration_s=timings["preprocess"],
                )

            # ---------------------------------------------------------------- Low Volume Check
            if start_idx <= step_order.index("preprocess"):
                min_reviews = self.settings.app_config.ingest.min_reviews
                if (
                    len(clean_reviews) < min_reviews
                    and self.settings.app_config.ingest.skip_on_low_volume
                ):
                    msg = (
                        f"Low volume detected: {len(clean_reviews)} clean reviews collected "
                        f"(threshold: {min_reviews}). Pipeline aborted per ingest.skip_on_low_volume."
                    )
                    logger.warning(
                        "low_volume_aborted", count=len(clean_reviews), min_reviews=min_reviews
                    )
                    manifest = RunManifest(
                        run_id=run_id,
                        week=target_week,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        status="error",
                        counts=counts,
                        coverage_start=coverage_start,
                        source_errors=source_errors,
                    )
                    self.run_store.save_manifest(manifest)
                    return OrchestratorResult(
                        manifest=manifest,
                        step_timings=timings,
                    )

            # ---------------------------------------------------------------- Step 3: Theming
            if start_idx <= step_order.index("theming"):
                t0 = time.perf_counter()
                bind_run_context(step="theming")
                logger.info("step_theming_start")

                # If resuming from theming, ensure clean reviews are loaded (EC-OPS-06)
                if not clean_reviews:
                    clean_reviews = self.run_store.load_clean_reviews(target_week)
                    if not clean_reviews:
                        raise FileNotFoundError(
                            f"Cannot resume from step 'theming': missing clean reviews artifact "
                            f"at {run_dir / 'reviews_clean.jsonl'}. Run preprocess first."
                        )
                    counts["clean_reviews"] = len(clean_reviews)

                eff_batch_size = (
                    batch_size
                    or getattr(self.settings.app_config.theming, "batch_size", None)
                    or 50
                )
                ranked_themes, assignments = run_theming_pipeline(
                    week=target_week,
                    config=self.settings.app_config,
                    run_store=self.run_store,
                    llm_client=llm,
                    batch_size=eff_batch_size,
                    dry_run=dry_run,
                )
                counts["themes_count"] = len(ranked_themes)
                counts["assigned_reviews"] = len(assignments)
                timings["theming"] = round(time.perf_counter() - t0, 3)
                logger.info(
                    "step_theming_complete",
                    themes=len(ranked_themes),
                    duration_s=timings["theming"],
                )

            # ---------------------------------------------------------------- Step 4: Compose
            if start_idx <= step_order.index("compose"):
                t0 = time.perf_counter()
                bind_run_context(step="compose")
                logger.info("step_compose_start")

                if not clean_reviews:
                    clean_reviews = self.run_store.load_clean_reviews(target_week)
                    if not clean_reviews:
                        raise FileNotFoundError(
                            f"Cannot resume from step 'compose': missing reviews_clean.jsonl at {run_dir}"
                        )
                themes_loaded = self.run_store.load_themes(target_week)
                if not themes_loaded:
                    raise FileNotFoundError(
                        f"Cannot resume from step 'compose': missing themes.json at {run_dir}"
                    )

                pulse_obj, validation_report = run_compose_pipeline(
                    week=target_week,
                    config=self.settings.app_config,
                    run_store=self.run_store,
                    llm_client=llm,
                    dry_run=dry_run,
                )
                counts["pulse_words"] = pulse_obj.word_count
                timings["compose"] = round(time.perf_counter() - t0, 3)
                logger.info(
                    "step_compose_complete",
                    word_count=pulse_obj.word_count,
                    passed=validation_report.passed,
                    duration_s=timings["compose"],
                )

                if not validation_report.passed:
                    logger.error(
                        "pipeline_guardrails_failed",
                        failures=validation_report.hard_failures,
                    )
                    manifest = RunManifest(
                        run_id=run_id,
                        week=target_week,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        status="failed_validation",
                        counts=counts,
                        coverage_start=coverage_start,
                        source_errors=source_errors,
                        llm_usage=llm.get_usage(),
                        eval_summary=validation_report.checks,
                    )
                    if not dry_run:
                        self.run_store.save_manifest(manifest)
                    return OrchestratorResult(
                        manifest=manifest,
                        pulse=pulse_obj,
                        validation_report=validation_report,
                        step_timings=timings,
                    )

            # ---------------------------------------------------------------- Step 5: Publish
            if start_idx <= step_order.index("publish"):
                t0 = time.perf_counter()
                bind_run_context(step="publish")
                logger.info("step_publish_start")

                opts = PublishOptions(
                    send=send,
                    force_append=force_append,
                    resend=resend,
                    dry_run=dry_run,
                )
                publish_outcome = asyncio.run(
                    publish_pulse(
                        settings=self.settings,
                        run_store=self.run_store,
                        week=target_week,
                        opts=opts,
                        client=self._mcp_client,
                    )
                )
                timings["publish"] = round(time.perf_counter() - t0, 3)
                logger.info(
                    "step_publish_complete",
                    status=publish_outcome.status,
                    duration_s=timings["publish"],
                )

            # ---------------------------------------------------------------- Step 6: Retention
            pruned_items: dict[str, list[str]] = {}
            if not dry_run and not skip_retention:
                bind_run_context(step="retention")
                pruned_items = prune_retention(
                    run_store=self.run_store,
                    target_week=target_week,
                    retention_weeks=retention_weeks,
                )

            # ---------------------------------------------------------------- Final Manifest
            finished_at = datetime.now(UTC)
            llm_usage = llm.get_usage()
            llm_usage["estimated_cost_usd_micros"] = int(estimate_llm_cost(llm_usage) * 1_000_000)

            # Determine final run status
            final_status: Literal["success", "failed_validation", "failed_publish", "error"]
            if publish_outcome and publish_outcome.status == "failed_publish":
                final_status = "failed_publish"
            elif validation_report and not validation_report.passed:
                final_status = "failed_validation"
            else:
                final_status = "success"

            pub_state = (
                publish_outcome.state
                if publish_outcome
                else self.run_store.load_publish_state(target_week)
            )
            eval_dict = validation_report.checks if validation_report else {}

            manifest = RunManifest(
                run_id=run_id,
                week=target_week,
                started_at=started_at,
                finished_at=finished_at,
                status=final_status,
                counts=counts,
                coverage_start=coverage_start,
                source_errors=source_errors,
                llm_usage=llm_usage,
                doc_id=pub_state.doc_id if pub_state else None,
                doc_url=pub_state.doc_url if pub_state else None,
                draft_id=pub_state.email.draft_id if pub_state and pub_state.email else None,
                publish=pub_state,
                eval_summary=eval_dict,
            )

            self.run_store.save_manifest(manifest)

            total_duration = round((finished_at - started_at).total_seconds(), 2)
            timings["total"] = total_duration

            logger.info(
                "pulse_run_summary",
                run_id=run_id,
                week=target_week,
                status=final_status,
                total_duration_s=total_duration,
                doc_url=manifest.doc_url,
                tokens=llm_usage,
            )
            clear_run_context()

            return OrchestratorResult(
                manifest=manifest,
                pulse=pulse_obj or self.run_store.load_pulse(target_week),
                validation_report=validation_report,
                publish_outcome=publish_outcome,
                step_timings=timings,
                pruned_items=pruned_items,
            )


def run_pipeline(
    week: str = "auto",
    window_weeks: int | None = None,
    dry_run: bool = False,
    from_step: str | None = None,
    send: bool = False,
    force_append: bool = False,
    resend: bool = False,
    retention_weeks: int = 12,
    skip_retention: bool = False,
    settings: Settings | None = None,
    run_store: RunStore | None = None,
    llm_client: LLMClient | None = None,
    mcp_client: ToolCaller | None = None,
    check_health: bool = True,
    batch_size: int | None = None,
) -> OrchestratorResult:
    """Convenience helper to run the orchestrator."""
    orchestrator = Orchestrator(
        settings=settings,
        run_store=run_store,
        llm_client=llm_client,
        mcp_client=mcp_client,
    )
    return orchestrator.run(
        week=week,
        window_weeks=window_weeks,
        dry_run=dry_run,
        from_step=from_step,
        send=send,
        force_append=force_append,
        resend=resend,
        retention_weeks=retention_weeks,
        skip_retention=skip_retention,
        check_health=check_health,
        batch_size=batch_size,
    )
