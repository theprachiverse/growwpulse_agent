"""Local web API for browsing weekly pulses and triggering runs.

Read endpoints serve the artifacts under ``data/runs/<week>/`` (clean, PII-scrubbed
reviews only). The single write endpoint starts the pipeline in a background thread;
a non-dry run appends to the Doc and creates a Gmail draft. Emails are only actually
sent through the dedicated publish endpoint, which requires an explicit ``confirm_send``.
"""

import hmac
import os
import re
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from pulse import ui_service
from pulse.config import get_settings
from pulse.observability.logging import get_logger
from pulse.publish.errors import PublishError
from pulse.run_store import RunStore
from pulse.utils.weeks import (
    current_week,
    format_week_human,
    last_completed_week,
    week_end_date,
    week_start_date,
)

logger = get_logger("pulse.api")

WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
STEPS = ("ingest", "preprocess", "theming", "compose", "publish")


def _cors_origins() -> list[str]:
    """Extra allowed origins (the Vercel frontend), comma-separated in PULSE_CORS_ORIGINS."""
    raw = os.environ.get("PULSE_CORS_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


app = FastAPI(title="Groww Review Pulse", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", *_cors_origins()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_token(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """When PULSE_API_TOKEN is set, every /api call except health needs a matching X-Pulse-Token."""
    token = os.environ.get("PULSE_API_TOKEN")
    path = request.url.path
    guarded = request.method != "OPTIONS" and path.startswith("/api") and path != "/api/health"
    if (
        token
        and guarded
        and not hmac.compare_digest(request.headers.get("x-pulse-token", ""), token)
    ):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


def _read_only() -> bool:
    """PULSE_READ_ONLY=1 (set on Vercel): browse bundled data only, no runs/publishing/settings."""
    return os.environ.get("PULSE_READ_ONLY", "").lower() in {"1", "true", "yes"}


def _require_writable() -> None:
    if _read_only():
        raise HTTPException(
            403, "This deployment is read-only. Run and publish from the local or Railway app."
        )


def _store() -> RunStore:
    return RunStore(base_dir=get_settings().data_dir)


def _week(week: str) -> str:
    if week == "all":
        return "all"
    if not WEEK_RE.match(week):
        raise HTTPException(400, "week must look like 2026-W42 or 'all'")
    return week


def _week_dir(week: str) -> Path:
    d = Path(get_settings().data_dir) / "runs" / _week(week)
    if not d.is_dir():
        raise HTTPException(404, f"No run found for {week}")
    return d


# ---------------------------------------------------------------- run job state
class RunRequest(BaseModel):
    week: str = "auto"
    window_weeks: int | None = Field(default=None, ge=8, le=12)
    dry_run: bool = True
    from_step: Literal["ingest", "preprocess", "theming", "compose", "publish"] | None = None
    batch_size: int | None = Field(default=None, ge=5, le=100)
    skip_health_check: bool = False


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {
        "ok": True,
        "read_only": _read_only(),
        "llm_key_set": bool(getattr(s, "llm_api_key", None)),
        "mcp_token_set": bool(getattr(s, "mcp_auth_token", None)),
        "default_week": last_completed_week(),
        "current_week": current_week(),
        "last_completed_week": last_completed_week(),
    }


@app.get("/api/weeks")
def list_weeks() -> list[dict[str, Any]]:
    store = _store()
    runs = Path(get_settings().data_dir) / "runs"
    existing_weeks = {p.name for p in runs.glob("*") if p.is_dir() and WEEK_RE.match(p.name)}

    # Also find any weeks with snapshot files
    snapshot_weeks: set[str] = set()
    for s in ["play_store", "app_store"]:
        s_dir = Path(get_settings().data_dir) / "snapshots" / s
        if s_dir.is_dir():
            snapshot_weeks |= {p.stem for p in s_dir.glob("*.jsonl") if WEEK_RE.match(p.stem)}

    candidate_weeks = sorted(existing_weeks | snapshot_weeks, reverse=True)
    weekly_items: list[dict[str, Any]] = []
    latest_pulse = None
    latest_pulse_week = None

    for w in candidate_weeks:
        pulse = store.load_pulse(w)
        manifest = store.load_manifest(w)
        total_revs = None
        avg_rating = None
        if pulse and pulse.total_reviews:
            total_revs = pulse.total_reviews
            avg_rating = pulse.avg_rating
            if latest_pulse is None:
                latest_pulse = pulse
                latest_pulse_week = w
        else:
            clean_in_run = store.load_clean_reviews(w)
            if clean_in_run:
                total_revs = len(clean_in_run)
                avg_rating = round(sum(r.rating for r in clean_in_run) / total_revs, 2)
            else:
                snaps = store.load_snapshot("play_store", w) + store.load_snapshot("app_store", w)
                if snaps:
                    total_revs = len(snaps)
                    avg_rating = round(sum(r.rating for r in snaps) / total_revs, 2)

        # Remove any option that has no reviews and no pulse
        if not pulse and not total_revs:
            continue

        weekly_items.append(
            {
                "week": w,
                "label": format_week_human(w),
                "has_pulse": pulse is not None,
                "total_reviews": total_revs,
                "avg_rating": avg_rating,
                "status": manifest.status if manifest else None,
            }
        )

    # If no pulse found among candidates, try loading from last completed week
    if latest_pulse is None:
        latest_pulse = store.load_pulse(last_completed_week())
        if latest_pulse:
            latest_pulse_week = last_completed_week()

    # Prepend the overarching "All Reviews Till Now" option
    all_label = (
        f"📊 All Reviews Till Now ({latest_pulse.window_start} → {latest_pulse.window_end} · 10 Weeks · {latest_pulse.total_reviews:,} reviews)"
        if latest_pulse
        else "📊 All Reviews Till Now (10-Week Window)"
    )
    all_option = {
        "week": "all",
        "label": "All Reviews Till Now (10-Week Window)",
        "display_label": all_label,
        "has_pulse": latest_pulse is not None,
        "total_reviews": latest_pulse.total_reviews
        if latest_pulse
        else sum(item["total_reviews"] or 0 for item in weekly_items),
        "avg_rating": latest_pulse.avg_rating if latest_pulse else None,
        "status": "success" if latest_pulse else None,
        "is_all": True,
        "pulse_week": latest_pulse_week,
    }

    return [all_option, *weekly_items]


@app.get("/api/weeks/{week}")
def get_week(week: str) -> dict[str, Any]:
    _week(week)
    store = _store()

    if week == "all":
        # Find latest run with pulse
        runs = Path(get_settings().data_dir) / "runs"
        pulse_week = None
        for p in sorted(runs.glob("*"), reverse=True):
            if p.is_dir() and WEEK_RE.match(p.name) and (p / "pulse.json").exists():
                pulse_week = p.name
                break
        if not pulse_week:
            pulse_week = last_completed_week()

        pulse = store.load_pulse(pulse_week)
        themes = store.load_themes(pulse_week)
        manifest = store.load_manifest(pulse_week)
        eval_report = store.load_eval_report(pulse_week)
        clean_reviews = store.load_clean_reviews(pulse_week)
        total_revs = pulse.total_reviews if pulse else len(clean_reviews)
        avg_rat = (
            pulse.avg_rating
            if pulse
            else (
                round(sum(r.rating for r in clean_reviews) / len(clean_reviews), 2)
                if clean_reviews
                else None
            )
        )

        return {
            "week": "all",
            "label": f"All Reviews Till Now ({pulse.window_start} → {pulse.window_end} · 10-Week Window)"
            if pulse
            else "All Reviews Till Now",
            "pulse": pulse.model_dump(mode="json") if pulse else None,
            "themes": [t.model_dump(mode="json") for t in themes] if themes else [],
            "manifest": manifest.model_dump(mode="json") if manifest else None,
            "eval_report": eval_report,
            "is_all": True,
            "pulse_week": pulse_week,
            "week_summary": {
                "total_reviews": total_revs,
                "avg_rating": avg_rat,
                "reviews_by_source": pulse.reviews_by_source
                if pulse
                else dict(Counter(r.source for r in clean_reviews)),
                "avg_rating_by_source": pulse.avg_rating_by_source if pulse else {},
                "window_start": pulse.window_start if pulse else "2026-08-10",
                "window_end": pulse.window_end if pulse else "2026-10-18",
            },
        }

    pulse = store.load_pulse(week)
    themes = store.load_themes(week)
    manifest = store.load_manifest(week)
    eval_report = store.load_eval_report(week)

    # Clean reviews for this specific week
    reviews = store.load_clean_reviews(week)
    if not reviews:
        reviews = store.load_snapshot("play_store", week) + store.load_snapshot("app_store", week)

    total_revs = len(reviews)
    avg_rating = round(sum(r.rating for r in reviews) / total_revs, 2) if total_revs else None
    reviews_by_source = dict(Counter(r.source for r in reviews))
    avg_rating_by_source = {}
    for s in reviews_by_source:
        s_revs = [r for r in reviews if r.source == s]
        avg_rating_by_source[s] = (
            round(sum(r.rating for r in s_revs) / len(s_revs), 2) if s_revs else None
        )

    # Derive theme breakdown if standalone themes.json isn't present for this week
    themes_out: list[dict[str, Any]] = []
    if themes:
        themes_out = [t.model_dump(mode="json") for t in themes]
    else:
        parent_themes = store.load_themes("2026-W42")
        parent_assignments = {a["rid"]: a for a in store.load_assignments("2026-W42")}
        if parent_themes and reviews and parent_assignments:
            theme_counts: dict[str, int] = defaultdict(int)
            theme_ratings: dict[str, list[int]] = defaultdict(list)
            theme_sources: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for r in reviews:
                a = parent_assignments.get(r.rid)
                if a:
                    tid = a["theme_id"]
                    theme_counts[tid] += 1
                    theme_ratings[tid].append(r.rating)
                    theme_sources[tid][r.source] += 1

            for pt in parent_themes:
                cnt = theme_counts.get(pt.theme_id, 0)
                rats = theme_ratings.get(pt.theme_id, [])
                t_avg = round(sum(rats) / len(rats), 2) if rats else pt.avg_rating
                t_low = round(sum(1 for rat in rats if rat <= 2) / len(rats), 4) if rats else 0.0
                themes_out.append(
                    {
                        "theme_id": pt.theme_id,
                        "name": pt.name,
                        "definition": pt.definition,
                        "review_count": cnt,
                        "share": round(cnt / total_revs, 4) if total_revs else 0.0,
                        "avg_rating": t_avg,
                        "low_rating_share": t_low,
                        "wow_delta": None,
                        "by_source": dict(theme_sources.get(pt.theme_id, {})),
                    }
                )

    return {
        "week": week,
        "label": format_week_human(week),
        "pulse": pulse.model_dump(mode="json") if pulse else None,
        "themes": themes_out,
        "manifest": manifest.model_dump(mode="json") if manifest else None,
        "eval_report": eval_report,
        "week_summary": {
            "total_reviews": total_revs,
            "avg_rating": avg_rating,
            "reviews_by_source": reviews_by_source,
            "avg_rating_by_source": avg_rating_by_source,
            "window_start": str(week_start_date(week)),
            "window_end": str(week_end_date(week)),
        },
    }


@app.get("/api/weeks/{week}/reviews")
def get_reviews(
    week: str,
    theme: str | None = None,
    source: Literal["play_store", "app_store"] | None = None,
    max_rating: int | None = Query(None, ge=1, le=5),
    min_rating: int | None = Query(None, ge=1, le=5),
    q: str | None = None,
    sort: Literal["helpful", "rating", "date"] = "helpful",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    _week(week)
    store = _store()

    if week == "all":
        # Load all reviews from overarching run (e.g. 2026-W42 which contains all 10 weeks of clean reviews)
        pulse_week = "2026-W42"
        reviews = store.load_clean_reviews(pulse_week)
        assign = {a["rid"]: a for a in store.load_assignments(pulse_week)}
    else:
        reviews = store.load_clean_reviews(week)
        if not reviews:
            reviews = store.load_snapshot("play_store", week) + store.load_snapshot(
                "app_store", week
            )
        assign = {a["rid"]: a for a in store.load_assignments(week)}
        if not assign:
            assign = {a["rid"]: a for a in store.load_assignments("2026-W42")}

    if not reviews:
        return {"total": 0, "items": []}

    max_rating_val = (
        int(max_rating)
        if isinstance(max_rating, (int, str)) and str(max_rating).isdigit()
        else None
    )
    needle = q.lower() if q else None
    rows = []
    for r in reviews:
        a = assign.get(r.rid)
        tid = a["theme_id"] if a else "other"
        if theme and tid != theme:
            continue
        if source and r.source != source:
            continue
        if max_rating_val is not None and r.rating > max_rating_val:
            continue
        if min_rating is not None and r.rating < int(min_rating):
            continue
        text = f"{r.clean_title + '. ' if r.clean_title else ''}{r.clean_text}"
        if needle and needle not in text.lower():
            continue
        rows.append(
            {
                "rid": r.rid,
                "source": r.source,
                "rating": r.rating,
                "date": r.date.isoformat(),
                "text": text,
                "helpful_votes": r.helpful_votes,
                "theme_id": tid,
                "confidence": a["confidence"] if a else None,
            }
        )
    key = {"helpful": "helpful_votes", "rating": "rating", "date": "date"}[sort]
    rows.sort(key=lambda x: x[key] or 0, reverse=(sort != "rating"))
    limit_val = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else 25
    offset_val = int(offset) if isinstance(offset, (int, str)) and str(offset).isdigit() else 0
    return {"total": len(rows), "items": rows[offset_val : offset_val + limit_val]}


@app.get("/api/run")
def run_status() -> dict[str, Any]:
    return ui_service.job_status()


@app.post("/api/run", status_code=202)
def start_run(req: RunRequest) -> dict[str, Any]:
    _require_writable()
    if req.week != "auto":
        _week(req.week)
    try:
        return ui_service.start_job(**req.model_dump())
    except ui_service.BusyError as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------------------------------------------------------------- publishing (Doc + email)
class SettingsUpdate(BaseModel):
    recipient: str | None = None  # becomes the active recipient and joins the saved list
    remove_recipient: str | None = None
    doc_id: str | None = None  # Doc ID or docs.google.com URL; "" clears the override


@app.get("/api/publish/settings")
def get_publish_settings() -> dict[str, Any]:
    payload = ui_service.settings_payload()
    if _read_only():  # public deployment: don't expose the recipient list or Doc link
        payload.update(recipient=None, recipients=[], doc_id=None, doc_url=None)
    return payload


@app.post("/api/publish/settings")
def update_publish_settings(req: SettingsUpdate) -> dict[str, Any]:
    _require_writable()
    try:
        return ui_service.update_settings(req.recipient, req.remove_recipient, req.doc_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/weeks/{week}/publish")
def get_publish_state(week: str) -> dict[str, Any]:
    if _read_only():
        return {"week": _week(week), "has_pulse": False, "state": None}
    return ui_service.publish_state(_week(week))


class PublishRequest(BaseModel):
    docs: bool = True
    email: Literal["off", "draft", "send"] = "draft"
    recipient: str | None = None
    force_append: bool = False  # append a revised section even if this week was already added
    resend: bool = False  # redo the email even if already drafted/sent
    confirm_send: bool = False  # required when email == "send"
    dry_run: bool = False  # preview the email text; makes no MCP calls


@app.post("/api/weeks/{week}/publish")
def publish_week(week: str, req: PublishRequest) -> dict[str, Any]:
    _require_writable()
    _week(week)
    if req.email == "send" and not req.confirm_send:
        raise HTTPException(400, "Sending requires confirm_send=true")
    try:
        pw, outcome = ui_service.run_publish(
            week,
            docs=req.docs,
            email=req.email,
            recipient=req.recipient,
            force_append=req.force_append,
            resend=req.resend,
            dry_run=req.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ui_service.BusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PublishError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "week": pw,
        "status": outcome.status,
        "message": outcome.message,
        "state": outcome.state.model_dump(mode="json"),
    }
