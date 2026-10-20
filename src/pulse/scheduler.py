"""Weekly scheduler for the always-on cloud container (Railway).

Runs as its own process next to the API (see scripts/railway_start.sh). Every hour it checks
whether it is Monday at/after PULSE_SCHEDULE_HOUR_UTC (default 03:30 UTC = 09:00 IST) and the last
completed week has no successful run yet; if so it runs the pipeline. The orchestrator's per-week
file lock keeps this safe if a run is started from the dashboard at the same time.

PULSE_SCHEDULE_LIVE=1 publishes (Doc + email per config); otherwise runs are dry runs.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from pulse.config import get_settings
from pulse.llm.budget import BudgetExceededError
from pulse.observability.logging import get_logger
from pulse.orchestrator import run_pipeline
from pulse.run_store import RunStore
from pulse.utils.weeks import last_completed_week

logger = get_logger("pulse.scheduler")

CHECK_EVERY_SECONDS = 3600


def due(now: datetime, hour_utc: float = 3.5) -> bool:
    """Monday, at or after the configured UTC hour."""
    return now.weekday() == 0 and now.hour + now.minute / 60 >= hour_utc


def already_done(store: RunStore, week: str) -> bool:
    manifest = store.load_manifest(week)
    return manifest is not None and manifest.status == "success"


def tick() -> None:
    now = datetime.now(UTC)
    hour = float(os.environ.get("PULSE_SCHEDULE_HOUR_UTC", "3.5"))
    if not due(now, hour):
        return
    week = last_completed_week()
    settings = get_settings()
    if already_done(RunStore(base_dir=settings.data_dir), week):
        return
    live = os.environ.get("PULSE_SCHEDULE_LIVE", "").lower() in {"1", "true", "yes"}
    logger.info("scheduled_run_start", week=week, live=live)
    try:
        run_pipeline(week=week, dry_run=not live, settings=settings, check_health=live)
    except BudgetExceededError as exc:
        logger.warning("scheduled_run_skipped_budget", week=week, reason=str(exc))
    except Exception as exc:
        logger.error("scheduled_run_failed", week=week, error=str(exc))


def main() -> None:
    logger.info("scheduler_started")
    while True:
        tick()
        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()
