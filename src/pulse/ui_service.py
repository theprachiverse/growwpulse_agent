"""UI-facing services used by the FastAPI app.

Holds the dashboard-saved settings (recipients, Doc override), the single background
pipeline job, and the Doc/email publish action, for the web dashboard.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pulse.config import Settings, get_settings
from pulse.observability.logging import get_logger
from pulse.publish.docs_publisher import doc_url_for
from pulse.publish.validate import _EMAIL_RE, parse_doc_id, validate_publish_settings
from pulse.run_store import RunStore
from pulse.utils.weeks import last_completed_week

if TYPE_CHECKING:  # heavy modules are imported lazily so read-only hosts (Vercel) stay small
    from pulse.publish.pipeline import PublishOutcome

logger = get_logger("pulse.ui_service")

_ui_lock = threading.Lock()
_publish_lock = threading.Lock()
_job_lock = threading.Lock()
_job: dict[str, Any] = {"state": "idle"}


class BusyError(RuntimeError):
    """Another run or publish is already in progress."""


def store() -> RunStore:
    return RunStore(base_dir=get_settings().data_dir)


# ---------------------------------------------------------------- saved settings
def _ui_settings_path() -> Path:
    return Path(get_settings().data_dir) / "state" / "ui_settings.json"


def load_ui_settings() -> dict[str, Any]:
    try:
        raw = json.loads(_ui_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {
        "recipient": raw.get("recipient") or None,
        "recipients": [r for r in raw.get("recipients", []) if isinstance(r, str)],
        "doc_id": raw.get("doc_id") or None,
    }


def _save_ui_settings(ui: dict[str, Any]) -> None:
    path = _ui_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ui, indent=2), encoding="utf-8")


def effective_settings(recipient: str | None = None) -> Settings:
    """Settings with dashboard overrides applied (recipient may be overridden per call)."""
    base = get_settings()
    ui = load_ui_settings()
    update: dict[str, Any] = {}
    chosen = recipient or ui["recipient"]
    if chosen:
        update["pulse_recipient"] = chosen
    if ui["doc_id"]:
        update["pulse_doc_id"] = ui["doc_id"]
    return base.model_copy(update=update) if update else base


def clean_email(value: str) -> str:
    """Returns the trimmed address or raises ValueError."""
    value = value.strip()
    if not _EMAIL_RE.match(value):
        raise ValueError(f"'{value}' is not a plain email address like name@example.com")
    return value


def settings_payload() -> dict[str, Any]:
    base = get_settings()
    ui = load_ui_settings()
    eff = effective_settings()
    try:
        doc_id = parse_doc_id(eff.pulse_doc_id)
    except ValueError:
        doc_id = None
    ref = store().load_doc_ref()
    if not doc_id and ref and ref.get("doc_id"):
        doc_id = ref["doc_id"]
    recipients = list(ui["recipients"])
    for r in (eff.pulse_recipient, base.pulse_recipient):
        if r and r not in recipients:
            recipients.append(r)
    return {
        "recipient": eff.pulse_recipient,
        "recipients": recipients,
        "doc_id": doc_id,
        "doc_url": doc_url_for(doc_id) if doc_id else None,
        "doc_title": base.app_config.publish.doc_title,
        "auto_create_doc": base.app_config.publish.auto_create_doc,
        "email_mode": base.app_config.publish.email_mode,
        "mcp_token_set": bool(base.mcp_auth_token),
        "problems": validate_publish_settings(eff, email_needed=True),
    }


def update_settings(
    recipient: str | None = None,
    remove_recipient: str | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """Raises ValueError on a bad address or Doc ID."""
    with _ui_lock:
        ui = load_ui_settings()
        if recipient is not None and recipient.strip():
            addr = clean_email(recipient)
            if addr not in ui["recipients"]:
                ui["recipients"].append(addr)
            ui["recipient"] = addr
        if remove_recipient:
            ui["recipients"] = [r for r in ui["recipients"] if r != remove_recipient]
            if ui["recipient"] == remove_recipient:
                ui["recipient"] = None
        if doc_id is not None:
            ui["doc_id"] = parse_doc_id(doc_id)
        _save_ui_settings(ui)
    return settings_payload()


# ---------------------------------------------------------------- background pipeline job
def job_status() -> dict[str, Any]:
    return dict(_job)


def is_running() -> bool:
    return _job.get("state") == "running"


def _run_job(kwargs: dict[str, Any]) -> None:
    try:
        from pulse.orchestrator import run_pipeline

        result = run_pipeline(settings=effective_settings(), skip_retention=True, **kwargs)
        _job.update(
            state="done",
            status=result.manifest.status,
            week=result.manifest.week,
            step_timings=result.step_timings,
            finished_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        logger.error("run_failed", error=str(exc))
        _job.update(state="failed", error=str(exc), finished_at=datetime.now(UTC).isoformat())


def start_job(
    *, week: str = "auto", dry_run: bool = True, skip_health_check: bool = False, **kwargs: Any
) -> dict[str, Any]:
    """Starts the pipeline in a background thread. Raises BusyError if one is running."""
    with _job_lock:
        if is_running():
            raise BusyError("A run is already in progress")
        _job.clear()
        _job.update(
            state="running", week=week, dry_run=dry_run, started_at=datetime.now(UTC).isoformat()
        )
    kwargs.update(week=week, dry_run=dry_run, check_health=not (dry_run or skip_health_check))
    threading.Thread(target=_run_job, args=(kwargs,), daemon=True).start()
    return job_status()


# ---------------------------------------------------------------- publishing
def pulse_week(week: str) -> str:
    """Resolve the 'all' view to the run that actually holds the pulse."""
    if week != "all":
        return week
    runs = Path(get_settings().data_dir) / "runs"
    for p in sorted(runs.glob("*"), reverse=True):
        if p.is_dir() and (p / "pulse.json").exists():
            return p.name
    return last_completed_week()


def publish_state(week: str) -> dict[str, Any]:
    pw = pulse_week(week)
    s = store()
    state = s.load_publish_state(pw)
    return {
        "week": pw,
        "has_pulse": s.load_pulse(pw) is not None,
        "state": state.model_dump(mode="json") if state else None,
    }


def run_publish(
    week: str,
    *,
    docs: bool = True,
    email: str = "draft",  # off | draft | send
    recipient: str | None = None,
    force_append: bool = False,
    resend: bool = False,
    dry_run: bool = False,
) -> tuple[str, PublishOutcome]:
    """Publish (or dry-run) the pulse. Returns (resolved_week, outcome).

    Raises ValueError for bad input, BusyError if a run/publish is active, and
    PublishError for invalid publish settings. Confirming a live send is the caller's job.
    """
    if not docs and email == "off":
        raise ValueError("Nothing to do: choose the Doc, an email, or both")
    from pulse.publish.pipeline import PublishOptions, publish_pulse

    to = clean_email(recipient) if recipient else None
    pw = pulse_week(week)

    with _job_lock:
        if is_running():
            raise BusyError("A pipeline run is in progress; publish after it finishes")
        if not _publish_lock.acquire(blocking=False):
            raise BusyError("A publish is already in progress")
    try:
        settings = effective_settings(to)
        # publish.email_mode "off" in config must not block an explicit draft/send from the UI
        if email != "off" and settings.app_config.publish.email_mode == "off":
            cfg = settings.app_config
            settings = settings.model_copy()
            settings._app_config = cfg.model_copy(
                update={"publish": cfg.publish.model_copy(update={"email_mode": "draft"})}
            )
        opts = PublishOptions(
            docs=docs,
            email=email != "off",
            send=email == "send",
            force_append=force_append,
            resend=resend,
            dry_run=dry_run,
        )
        outcome = asyncio.run(publish_pulse(settings, store(), pw, opts))
    finally:
        _publish_lock.release()
    logger.info("publish", week=pw, status=outcome.status, docs=docs, email=email, dry_run=dry_run)
    return pw, outcome
