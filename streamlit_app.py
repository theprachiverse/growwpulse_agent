"""Groww Review Pulse — Streamlit dashboard (Stitch design).

Runs the same code as the FastAPI app (read helpers in ``pulse.api``, background pipeline
job and Doc/email publishing in ``pulse.ui_service``) but in a single Streamlit process.

    streamlit run streamlit_app.py

Set ``APP_PASSWORD`` to put the whole app behind a password (required for any shared deploy:
the app can start LLM runs and send email from your Google account).
"""

from __future__ import annotations

import hmac
import os
import re
from datetime import date, datetime
from html import escape

import streamlit as st
import streamlit.components.v1 as components
from fastapi import HTTPException

from pulse import ui_service
from pulse.api import get_reviews, get_week, health, list_weeks
from pulse.publish.errors import PublishError
from pulse.publish.html_email import render_html
from pulse.publish.plaintext import render_plain

SOURCE = {"play_store": "Google Play Store", "app_store": "Apple App Store"}
SOURCE_SHORT = {"play_store": "Google Play", "app_store": "App Store"}
SOURCE_ICON = {"play_store": "android", "app_store": "desktop_mac"}
NAV = {
    "Pulse": ":material/insights: Pulse",
    "Themes": ":material/scatter_plot: Themes",
    "Reviews": ":material/rate_review: Reviews",
    "Publish": ":material/send: Publish",
    "Runs & Quality": ":material/account_tree: Runs & Quality",
}
STEPS = ["", "ingest", "preprocess", "theming", "compose", "publish"]
THEME_COLORS = ["#3B6CF6", "#00B386", "#D97706", "#8A5CF5", "#D9487A"]
PII_RE = re.compile(r"(\[[A-Z_]{3,}\])")

st.set_page_config(page_title="Groww Review Pulse", page_icon="📈", layout="wide")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200&display=swap');
:root { --ink:#1E2238; --slate:#44475B; --muted:#8C93A8; --line:#E5E9F0; --canvas:#F8F9FB; --green:#00B386; --green-ink:#006C4F; }
html, body, .stApp, [class*="css"] { font-family:'Inter',system-ui,sans-serif !important; font-feature-settings:"tnum" 1,"cv05" 1; }
.stApp { background:var(--canvas); color:var(--ink); }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
header[data-testid="stHeader"] { background:transparent; height:0; }
.block-container { max-width:1520px; padding:1.25rem 2rem 3rem !important; }
.ms { font-family:'Material Symbols Outlined'; font-weight:normal; font-style:normal; font-size:18px; line-height:1;
  letter-spacing:normal; text-transform:none; display:inline-block; white-space:nowrap; vertical-align:middle; font-variation-settings:'FILL' 0; }
.ms.fill { font-variation-settings:'FILL' 1; }
h1 { font-size:28px !important; line-height:36px !important; letter-spacing:-.02em; color:var(--ink); font-weight:700 !important; margin:0 !important; padding:0 !important; }
h2, h3 { letter-spacing:-.01em; color:var(--ink); font-weight:600 !important; }
h3 { font-size:18px !important; line-height:24px !important; }

/* sidebar */
[data-testid="stSidebar"] { background:#fff; border-right:1px solid var(--line); width:260px !important; min-width:260px !important; }
[data-testid="stSidebar"] > div:first-child { padding-top:0; }
[data-testid="stSidebarHeader"] { display:none; }
.brand { height:64px; display:flex; align-items:center; gap:10px; padding:0 8px; margin:0 -8px 8px; border-bottom:1px solid var(--line); }
.brand .logo { width:32px; height:32px; border-radius:8px; background:var(--green); color:#fff; display:grid; place-items:center; }
.brand b { display:block; font-size:18px; line-height:18px; letter-spacing:-.01em; color:var(--ink); }
.brand small { display:block; margin-top:3px; font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); }
.nav-label { font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); padding:10px 4px 6px; }
[data-testid="stSidebar"] [role="radiogroup"] { gap:4px; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] { padding:9px 14px; border-radius:8px; width:100%; cursor:pointer; color:var(--slate); margin:0; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child { display:none !important; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover { background:#F2F4F6; color:var(--ink); }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) { background:rgba(0,179,134,.10); }
[data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) * { color:var(--green-ink) !important; font-weight:600; }
[data-testid="stSidebar"] label[data-testid="stRadioOption"] p { font-size:14px; }
.statusbox { background:var(--canvas); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-top:18px; }
.statusbox h6 { margin:0 0 10px; gap:8px; font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); display:flex; justify-content:space-between; align-items:center; }
.statusrow { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--slate); padding:2px 0; }
.dot { width:6px; height:6px; border-radius:50%; background:var(--muted); display:inline-block; flex:none; }
.dot.ok { background:var(--green); } .dot.bad { background:#DC2626; } .dot.warn { background:#D97706; } .dot.info { background:#3B6CF6; }

/* top bar */
.crumb { font-size:12px; font-weight:500; color:var(--muted); white-space:nowrap; padding-top:8px; }
.pillbar { display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; padding-top:4px; }
.pill { display:inline-flex; align-items:center; gap:8px; padding:5px 11px; border-radius:99px; background:#F2F4F6; font-size:11px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:var(--slate); white-space:nowrap; }
.pill.outline { background:#fff; border:1px solid var(--line); border-radius:8px; }
.topline { border-bottom:1px solid var(--line); margin:6px -2rem 22px; }

/* page head */
.eyebrow { font-size:11px; font-weight:600; letter-spacing:.1em; text-transform:uppercase; color:var(--green); margin-bottom:4px; }
.eyebrow span { color:#BBCAC1; margin:0 6px; } .eyebrow em { font-style:normal; color:#6C7A73; letter-spacing:.05em; }
.lede { color:var(--slate); font-size:14px; line-height:20px; max-width:760px; margin:8px 0 22px; }
.mpill { display:inline-flex; align-items:center; gap:8px; padding:6px 12px; border-radius:99px; background:#fff; border:1px solid var(--line); font-size:12px; color:var(--muted); margin:0 0 8px 8px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
.mpill b { color:var(--ink); font-weight:600; }
.mpill.alt { background:rgba(229,233,240,.6); }

/* cards */
.card { background:#fff; border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin-bottom:12px; }
.card h4 { margin:0 0 2px; font-size:15px; color:var(--ink); font-weight:600; }
.card p { margin:4px 0 10px; color:var(--slate); font-size:14px; }
.stat-label { font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); }
.stat-val { font-size:32px; font-weight:600; letter-spacing:-.02em; color:var(--ink); line-height:38px; }
.stat-note { font-size:12px; color:var(--muted); }
.chip { display:inline-block; font-size:12px; font-weight:500; padding:2px 9px; margin:0 6px 4px 0; border-radius:99px; background:#F2F4F6; border:1px solid var(--line); color:#374151; }
.chip.ok { background:rgba(0,179,134,.12); color:var(--green-ink); border-color:transparent; }
.chip.warn { background:#FEF3E2; color:#D97706; border-color:transparent; }
.chip.bad { background:#FDECEC; color:#DC2626; border-color:transparent; }
.rank { display:inline-grid; place-items:center; width:26px; height:26px; border-radius:50%; background:var(--green); color:#fff; font-size:13px; font-weight:700; margin-right:8px; }
.quote { border-left:3px solid var(--green); padding-left:14px; font-size:15px; line-height:24px; color:#374151; }
.quote .meta { font-size:12px; color:var(--muted); margin-top:8px; }
.bar { display:flex; height:22px; border-radius:6px; overflow:hidden; background:var(--line); }
.legend span { margin-right:14px; font-size:13px; white-space:nowrap; color:var(--slate); }
.legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; }

/* review cards (per mockup) */
.scope { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 16px; border-radius:12px; background:#fff; border:1px solid var(--line); font-size:13px; color:var(--slate); margin:4px 0 14px; }
.scope .ms { color:var(--green); font-size:18px; margin-right:8px; }
.scope mark { background:rgba(0,179,134,.15); color:#003D2C; font-weight:600; padding:1px 6px; border-radius:4px; font-size:12px; }
.rcard { background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 20px; margin-bottom:14px; transition:border-color .15s, box-shadow .15s; }
.rcard:hover { border-color:#BBCAC1; box-shadow:0 1px 4px rgba(0,0,0,.05); }
.rhead { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; padding-bottom:12px; border-bottom:1px solid var(--line); }
.rmeta { display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:12px; color:var(--slate); }
.rating { display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:6px; font-weight:700; font-size:12px; border:1px solid transparent; font-family:ui-monospace,'Cascadia Mono',monospace; }
.rating .ms { font-size:14px; }
.store { display:inline-flex; align-items:center; gap:6px; font-weight:500; color:var(--ink); }
.tag { display:inline-block; padding:1px 7px; border-radius:4px; font-family:ui-monospace,'Cascadia Mono',monospace; font-size:11px; background:#F2F4F6; border:1px solid var(--line); color:var(--slate); }
.sep { color:#BBCAC1; }
.themechip { display:inline-block; padding:2px 9px; border-radius:99px; font-size:11px; font-weight:500; background:#F2F4F6; border:1px solid var(--line); color:#374151; }
.sent { display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:99px; font-size:11px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; border:1px solid transparent; }
.sent i { width:6px; height:6px; border-radius:50%; display:inline-block; }
.rbody { padding:14px 0; font-size:15px; line-height:24px; color:#374151; }
.rbody mark { background:rgba(113,250,200,.5); color:#003D2C; font-weight:600; padding:1px 6px; border-radius:4px; border:1px solid rgba(0,179,134,.3); }
.pii { background:#F2F4F6; border:1px solid var(--line); color:var(--slate); padding:1px 6px; border-radius:4px; font-size:11px; font-family:ui-monospace,'Cascadia Mono',monospace; }
.rfoot { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; padding-top:10px; border-top:1px solid var(--line); font-size:12px; color:#6C7A73; }
.rfoot .ms { color:var(--green); font-size:16px; margin-right:6px; }
.rfoot b { color:#374151; font-weight:600; }
.pagebar { display:flex; align-items:center; background:#fff; border:1px solid var(--line); border-radius:12px; padding:12px 20px; font-size:13px; color:var(--slate); }
.pagebar b { color:var(--ink); font-family:ui-monospace,monospace; }
.zeropii { display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:8px; background:rgba(0,179,134,.10); border:1px solid rgba(0,179,134,.3); color:var(--green-ink); font-size:11px; font-weight:600; }
.zeropii .ms { color:var(--green); font-size:15px; }
.zeropii code { background:#fff; border:1px solid var(--line); color:#6C7A73; font-size:10px; padding:0 4px; border-radius:4px; }

/* streamlit widgets */
.stButton > button, .stDownloadButton > button { border-radius:8px; border:1px solid var(--line); background:#fff; color:var(--ink); font-weight:500; min-height:36px; }
.stButton > button:hover { border-color:#BBCAC1; background:#F2F4F6; color:var(--ink); }
.stButton > button[kind="primary"] { background:var(--green); border-color:var(--green); color:#fff; }
.stButton > button[kind="primary"]:hover { background:#009E75; border-color:#009E75; color:#fff; }
.stTextInput input, .stSelectbox [data-baseweb="select"] > div, .stDateInput input { border-radius:8px; border-color:var(--line); background:#fff; }
.stTextInput input:focus { border-color:var(--green); box-shadow:0 0 0 3px rgba(0,179,134,.18); }
[data-testid="stPills"] button, [data-testid="stSegmentedControl"] button { border-radius:99px; }
div[data-testid="stVerticalBlockBorderWrapper"] { background:#fff; border-radius:12px; border-color:var(--line); }
.label-caps { font-size:11px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:#6C7A73; margin-bottom:2px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
def ms(name: str, fill: bool = False) -> str:
    return f'<span class="ms{" fill" if fill else ""}">{name}</span>'


def pct(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x * 100:.{d}f}%"


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{escape(str(text))}</span>'


def stat(col, label: str, value: str, note: str = "") -> None:
    col.markdown(
        f'<div class="card"><div class="stat-label">{label}</div><div class="stat-val">{value}</div>'
        f'<div class="stat-note">{escape(note) or "&nbsp;"}</div></div>',
        unsafe_allow_html=True,
    )


def head(eyebrow: str, title: str, sub: str = "", crumb: str = "") -> None:
    extra = f"<span>/</span><em>{escape(crumb)}</em>" if crumb else ""
    st.markdown(f'<div class="eyebrow">{escape(eyebrow)}{extra}</div>', unsafe_allow_html=True)
    st.markdown(f"# {title}")
    if sub:
        st.markdown(f'<p class="lede">{escape(sub)}</p>', unsafe_allow_html=True)


def when(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b %Y %H:%M")
    except ValueError:
        return iso


def nice_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except ValueError:
        return iso


def api_error(exc: Exception) -> str:
    return str(exc.detail) if isinstance(exc, HTTPException) else str(exc)


@st.cache_data(ttl=20, show_spinner=False)
def load_weeks() -> list[dict]:
    items = list_weeks()
    return [w for w in items if w.get("has_pulse") or (w.get("total_reviews") or 0) > 0]


@st.cache_data(ttl=20, show_spinner=False)
def load_week(week: str) -> dict:
    return get_week(week)


@st.cache_data(ttl=20, show_spinner=False)
def load_health() -> dict:
    return health()


def refresh() -> None:
    st.cache_data.clear()


# ---------------------------------------------------------------- access gate
def gate() -> None:
    expected = os.environ.get("APP_PASSWORD")
    if not expected or st.session_state.get("authed"):
        return
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown(
            f'<div class="brand" style="border:0;margin-top:80px"><div class="logo">{ms("monitoring")}</div>'
            "<div><b>Groww Pulse</b><small>Intelligence Platform</small></div></div>",
            unsafe_allow_html=True,
        )
        with st.form("login"):
            st.caption("Enter the access password to continue.")
            pw = st.text_input("Password", type="password")
            if st.form_submit_button("Unlock", type="primary", width="stretch"):
                if hmac.compare_digest(pw.encode(), expected.encode()):
                    st.session_state["authed"] = True
                    st.rerun()
                st.error("Wrong password.")
    st.stop()


# ---------------------------------------------------------------- run dialog
@st.dialog("Generate review pulse")
def run_dialog(weeks: list[dict], hl: dict, default_week: str) -> None:
    cur, last = hl["current_week"], hl["last_completed_week"]
    st.caption(
        "Fetches public reviews from Google Play and the App Store for a rolling 8–12 week window, "
        "scrubs PII, clusters into ≤5 themes and writes a one-page pulse. Theming makes ~30 LLM calls "
        "and can take 10+ minutes."
    )
    idx = 1 if default_week == cur else 0 if default_week in ("auto", "all", last) else 2
    mode = st.radio(
        "Target week",
        ["auto", "coming", "custom"],
        index=idx,
        format_func={
            "auto": f"Latest finished ({last})",
            "coming": f"Coming week ({cur}, in progress)",
            "custom": "Custom date / existing week",
        }.get,
    )
    target = "auto" if mode == "auto" else cur
    if mode == "custom":
        existing = [w["week"] for w in weeks if w["week"] != "all"]
        picked = st.date_input("Pick any date in the week", value=date.today())
        iso = picked.isocalendar()
        by_date = f"{iso.year}-W{iso.week:02d}"
        target = st.selectbox(
            "Or choose an existing week",
            [by_date] + [w for w in existing if w != by_date],
            help="Defaults to the week containing the picked date.",
        )
    c1, c2 = st.columns(2)
    window = c1.selectbox(
        "Rolling window", [8, 10, 12], index=1, format_func=lambda n: f"{n} weeks"
    )
    batch = c2.selectbox("Classification batch size", [10, 20, 25, 50, 100], index=2)
    from_step = st.selectbox(
        "Resume from step (optional)", STEPS, format_func=lambda s: s or "Full pipeline"
    )
    dry = st.checkbox("Dry run (no Google Doc / Gmail changes)", value=True)
    if not dry:
        st.warning("Live run will update the Google Doc and create a Gmail draft via MCP.")
    st.caption(f"Target ISO week: **{target}**")
    if st.button(f"Start pipeline for {target}", type="primary", width="stretch"):
        try:
            ui_service.start_job(
                week=target,
                window_weeks=window,
                dry_run=dry,
                from_step=from_step or None,
                batch_size=batch,
            )
        except ui_service.BusyError as exc:
            st.error(str(exc))
            return
        st.session_state["page"] = "Runs & Quality"
        st.rerun()


# ---------------------------------------------------------------- pulse / themes
def theme_card(rank: int, t: dict) -> None:
    low = t.get("low_rating_share") or 0
    chips = [
        chip(f"{pct(t.get('share'))} of reviews", "ok"),
        chip(f"{pct(low)} are 1–2★", "bad" if low > 0.6 else "warn" if low > 0.4 else ""),
        chip(f"{t['avg_rating']:.2f} ★"),
    ]
    chips += [chip(f"{SOURCE_SHORT.get(s, s)}: {n}") for s, n in (t.get("by_source") or {}).items()]
    wow = t.get("wow_delta")
    if wow is not None:
        chips.append(
            chip(f"{'+' if wow > 0 else ''}{wow * 100:.1f} pts WoW", "bad" if wow > 0 else "ok")
        )
    st.markdown(
        f'<div class="card"><h4><span class="rank">{rank}</span>{escape(t["name"])}</h4>'
        f"<p>{escape(t['definition'])}</p>{''.join(chips)}</div>",
        unsafe_allow_html=True,
    )


def page_pulse(data: dict) -> None:
    head(
        "Weekly digest",
        "Pulse",
        "Top themes, verbatim quotes and action ideas distilled from the latest reviews.",
        crumb=data.get("label") or data["week"],
    )
    pulse, summary = data.get("pulse"), data.get("week_summary") or {}
    sources = (pulse or {}).get("reviews_by_source") or summary.get("reviews_by_source") or {}
    avg_src = (pulse or {}).get("avg_rating_by_source") or summary.get("avg_rating_by_source") or {}

    cols = st.columns(2 + len(sources) + 1)
    total = (pulse or {}).get("total_reviews", summary.get("total_reviews") or 0)
    avg = (pulse or {}).get("avg_rating", summary.get("avg_rating"))
    window = (
        f"{pulse['window_start']} → {pulse['window_end']}"
        if pulse
        else (
            f"{summary['window_start']} → {summary['window_end']}"
            if summary.get("window_start")
            else data["week"]
        )
    )
    st.write("")
    stat(cols[0], "Reviews", f"{total:,}", window)
    stat(cols[1], "Avg rating", f"{avg:.2f} ★" if avg is not None else "–")
    for i, (s, n) in enumerate(sources.items()):
        a = avg_src.get(s)
        stat(
            cols[2 + i], SOURCE_SHORT.get(s, s), f"{n:,}", f"{a:.2f} ★ avg" if a is not None else ""
        )
    if pulse:
        stat(cols[-1], "Word count", str(pulse["word_count"]), "limit 250")
    else:
        stat(cols[-1], "Pulse status", "Pending", "not generated yet")

    partial = (data.get("manifest") or {}).get("coverage_start", {}).get("app_store")
    if partial:
        st.info(f"App Store coverage starts {partial}; older weeks come from stored snapshots.")

    if not pulse:
        st.markdown(
            f'<div class="card"><h4>{total:,} reviews available for {escape(data.get("label") or data["week"])}</h4>'
            "<p>The reviews are collected and scrubbed, but the one-page pulse (top 3 themes, 3 quotes, 3 action "
            "ideas) hasn’t been generated for this week yet.</p></div>",
            unsafe_allow_html=True,
        )
        if st.button("Generate pulse for this week", type="primary", icon=":material/bolt:"):
            run_dialog(load_weeks(), load_health(), data["week"])
        for i, t in enumerate(data.get("themes") or [], 1):
            theme_card(i, t)
        return

    names = {t["theme_id"]: t["name"] for t in pulse["top_themes"]}
    st.subheader("Top themes")
    for i, t in enumerate(pulse["top_themes"], 1):
        theme_card(i, t)
    st.subheader("What users said")
    for q in pulse["quotes"]:
        st.markdown(
            f'<div class="card"><div class="quote">“{escape(q["text"])}”<div class="meta">{q["rating"]}★ · '
            f"{escape(SOURCE_SHORT.get(q['source'], q['source']))} · {escape(q['month'])} · "
            f"{escape(names.get(q['theme_id'], q['theme_id']))}</div></div></div>",
            unsafe_allow_html=True,
        )
    st.subheader("Action ideas")
    for a in pulse["actions"]:
        st.markdown(
            f'<div class="card"><h4>{escape(a["title"])}</h4><p>{escape(a["rationale"])}</p>'
            f"{chip(a['owner_hint'], 'ok')}{chip(names.get(a['theme_id'], a['theme_id']))}</div>",
            unsafe_allow_html=True,
        )


def page_themes(data: dict) -> None:
    head(
        "Theme analysis",
        "Themes",
        "How reviews cluster into product themes, with rating and store breakdowns.",
        crumb=data.get("label") or data["week"],
    )
    themes = sorted(data.get("themes") or [], key=lambda t: -t["review_count"])
    if not themes:
        st.info(
            "No themes for this week yet. Use “Run pipeline” to classify reviews into ≤5 themes."
        )
        return
    summary = data.get("week_summary") or {}
    total = (
        (data.get("pulse") or {}).get("total_reviews")
        or summary.get("total_reviews")
        or sum(t["review_count"] for t in themes)
        or 1
    )
    other = max(0, total - sum(t["review_count"] for t in themes))
    segs = [(t["name"], t["review_count"], THEME_COLORS[i % 5]) for i, t in enumerate(themes)] + [
        ("Other", other, "#9AA3B2")
    ]
    bar = "".join(
        f'<span style="width:{n / total * 100}%;background:{c}" title="{escape(name)}: {n}"></span>'
        for name, n, c in segs
    )
    legend = "".join(
        f'<span><i style="background:{c}"></i>{escape(name)} · {pct(n / total)}</span>'
        for name, n, c in segs
    )
    st.write("")
    st.markdown(
        f'<div class="card"><b>Share of {total:,} reviews</b><div class="bar" style="margin-top:10px">{bar}</div>'
        f'<div class="legend" style="margin-top:10px">{legend}</div></div>',
        unsafe_allow_html=True,
    )
    if other / total > 0.2:
        st.warning(f"“Other” is {pct(other / total)} of reviews, above the 20% target.")
    top3 = {t["theme_id"] for t in (data.get("pulse") or {}).get("top_themes", themes[:3])}
    st.subheader("Theme breakdown")
    st.dataframe(
        [
            {
                "Theme": t["name"] + ("  ✓ in pulse" if t["theme_id"] in top3 else ""),
                "Definition": t["definition"],
                "Reviews": t["review_count"],
                "Share": pct(t["share"]),
                "Avg ★": round(t["avg_rating"], 2),
                "1–2★": pct(t["low_rating_share"]),
                SOURCE_SHORT["play_store"]: (t.get("by_source") or {}).get("play_store", 0),
                SOURCE_SHORT["app_store"]: (t.get("by_source") or {}).get("app_store", 0),
            }
            for t in themes
        ],
        hide_index=True,
        width="stretch",
    )


# ---------------------------------------------------------------- reviews explorer
RATING_STYLE = {
    1: ("#FFDAD6", "#93000A", "rgba(186,26,26,.2)", "#BA1A1A", "High friction"),
    2: ("#FFE4E6", "#9F1239", "rgba(225,29,72,.3)", "#E11D48", "Moderate friction"),
    3: ("#FEF3E2", "#B45309", "rgba(217,119,6,.3)", "#D97706", "Neutral"),
    4: ("rgba(0,179,134,.12)", "#006C4F", "rgba(0,179,134,.3)", "#00B386", "Positive"),
    5: ("rgba(0,179,134,.15)", "#006C4F", "rgba(0,179,134,.3)", "#00B386", "Positive delight"),
}


def render_text(text: str, q: str | None) -> str:
    out = []
    pat = re.compile(re.escape(q), re.I) if q else None
    for part in PII_RE.split(text):
        if PII_RE.fullmatch(part):
            out.append(f'<span class="pii">{escape(part)}</span>')
            continue
        seg = escape(part)
        if pat:
            seg = pat.sub(lambda m: f"<mark>{m.group(0)}</mark>", seg)
        out.append(seg)
    return "".join(out)


def review_card(r: dict, names: dict, q: str | None) -> str:
    bg, fg, bd, dot, label = RATING_STYLE[r["rating"]]
    conf = f" · {round(r['confidence'] * 100)}% theme match" if r.get("confidence") else ""
    store = (
        f'<span class="store">{ms(SOURCE_ICON.get(r["source"], "phone_android"))}'
        f"{escape(SOURCE.get(r['source'], r['source']))}</span>"
    )
    helpful = (
        f"<span>{ms('thumb_up')}<b>{r['helpful_votes']:,}</b> people found this helpful</span>"
        if r["helpful_votes"] > 0
        else "<span></span>"
    )
    return (
        '<article class="rcard"><div class="rhead"><div class="rmeta">'
        f'<span class="rating" style="background:{bg};color:{fg};border-color:{bd}">{ms("star", True)}{r["rating"]}.0</span>'
        f'{store}<span class="sep">•</span><span>{nice_date(r["date"])}</span><span class="sep">•</span>'
        f'<span class="themechip">{escape(names.get(r["theme_id"], r["theme_id"]))}</span></div>'
        f'<span class="sent" style="background:{bg};color:{fg};border-color:{bd}"><i style="background:{dot}"></i>{label}{conf}</span></div>'
        f'<div class="rbody">{render_text(r["text"], q)}</div>'
        f'<div class="rfoot">{helpful}<span class="tag">{escape(r["rid"])}</span></div></article>'
    )


def reset_review_filters() -> None:
    for k, v in {
        "rev_q": "",
        "rev_theme": None,
        "rev_store": "All stores",
        "rev_rating": "All",
        "rev_sort": "Most helpful",
    }.items():
        st.session_state[k] = v


def page_numbers(cur: int, total: int) -> list[int | None]:
    if total <= 7:
        return list(range(1, total + 1))
    pages = {1, total, cur - 1, cur, cur + 1}
    out: list[int | None] = []
    prev = 0
    for p in sorted(x for x in pages if 1 <= x <= total):
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def page_reviews(week: str, data: dict) -> None:
    summary = data.get("week_summary") or {}
    themes = data.get("themes") or []
    by_source = summary.get("reviews_by_source") or {}
    total_in_view = summary.get("total_reviews")

    st.session_state.setdefault("rev_per_page", 25)
    for k, v in {
        "rev_store": "All stores",
        "rev_rating": "All",
        "rev_sort": "Most helpful",
    }.items():
        st.session_state.setdefault(k, v)
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            '<div class="eyebrow">Corpus stream · Ingestion pipeline'
            "<span>/</span><em>India Domestic Stores</em></div>",
            unsafe_allow_html=True,
        )
        st.markdown("# Reviews Explorer")
        st.markdown(
            '<p class="lede">Search, filter, and inspect verbatim user feedback ingested from Google Play Store (India) '
            "and Apple App Store. Automated PII scrubbing and semantic LLM tagging applied at ingest.</p>",
            unsafe_allow_html=True,
        )
    pills_slot = right.empty()

    with st.container(border=True):
        q = st.text_input(
            "Search",
            key="rev_q",
            icon=":material/search:",
            label_visibility="collapsed",
            placeholder="Search verbatim feedback, e.g. 'UPI mandate', 'Nifty chart', 'KYC'…",
        )
        theme_labels = {f"{t['name']} · {t['review_count']:,}": t["theme_id"] for t in themes} | {
            "Others": "other"
        }
        st.markdown('<div class="label-caps">Themes</div>', unsafe_allow_html=True)
        theme_pick = st.pills(
            "Theme", list(theme_labels), key="rev_theme", label_visibility="collapsed"
        )
        c1, c2, c3, c4 = st.columns([3, 3.2, 1.6, 2])
        src_labels = {"All stores": ""} | {
            f"{SOURCE_SHORT[s]} ({by_source[s]:,})" if s in by_source else SOURCE_SHORT[s]: s
            for s in SOURCE
        }
        with c1:
            src_pick = st.segmented_control(
                "Store", list(src_labels), key="rev_store", label_visibility="collapsed"
            )
        rating_labels = {"All": None, "1★": 1, "2★": 2, "3★": 3, "4★": 4, "5★": 5}
        with c2:
            rating_pick = st.segmented_control(
                "Rating", list(rating_labels), key="rev_rating", label_visibility="collapsed"
            )
        sort_labels = {"Most helpful": "helpful", "Newest": "date", "Lowest rating": "rating"}
        with c3:
            sort_pick = st.selectbox(
                "Sort", list(sort_labels), key="rev_sort", label_visibility="collapsed"
            )
        c4.markdown(
            f'<div class="zeropii">{ms("verified_user", True)}Zero-PII Active <code>[SCRUBBED]</code></div>',
            unsafe_allow_html=True,
        )

    per_page = st.session_state["rev_per_page"]
    key = (week, q, theme_pick, src_pick, rating_pick, sort_pick, per_page)
    if st.session_state.get("rev_key") != key:
        st.session_state["rev_key"], st.session_state["rev_page"] = key, 1
    page = st.session_state["rev_page"]
    rating = rating_labels.get(rating_pick or "All")

    try:
        res = get_reviews(
            week,
            theme=theme_labels.get(theme_pick),
            source=src_labels.get(src_pick or "All stores") or None,
            max_rating=rating,
            min_rating=rating,
            q=q or None,
            sort=sort_labels[sort_pick or "Most helpful"],
            limit=per_page,
            offset=(page - 1) * per_page,
        )
    except Exception as exc:
        st.error(api_error(exc))
        return
    total = res["total"]

    pills = (
        f'<span class="mpill"><i class="dot ok"></i><b>{total_in_view:,}</b> in this view</span>'
        if total_in_view
        else ""
    )
    pills_slot.markdown(
        f'<div style="text-align:right;padding-top:18px">{pills}<span class="mpill alt">{ms("filter_alt")}'
        f"<b>{total:,}</b> matching criteria</span></div>",
        unsafe_allow_html=True,
    )

    active = [theme_pick, src_pick if src_pick != "All stores" else None, rating, q]
    if any(active):
        bits = []
        if q:
            bits.append(f'matching <mark>"{escape(q)}"</mark>')
        if theme_pick:
            bits.append(f'in <b style="color:var(--ink)">{escape(theme_pick.split(" · ")[0])}</b>')
        if src_pick and src_pick != "All stores":
            bits.append(f'from <b style="color:var(--ink)">{escape(src_pick.split(" (")[0])}</b>')
        if rating:
            bits.append(f'with rating <b style="color:#BA1A1A">{rating}★</b>')
        sc1, sc2 = st.columns([6, 1])
        sc1.markdown(
            f'<div class="scope"><span>{ms("manage_search")}Showing results {" ".join(bits)}</span></div>',
            unsafe_allow_html=True,
        )
        sc2.button("Reset filters", on_click=reset_review_filters, width="stretch")

    if not res["items"]:
        st.info("No reviews match these filters.")
        return
    names = {t["theme_id"]: t["name"] for t in themes} | {"other": "Others"}
    st.markdown(
        "".join(review_card(r, names, q or None) for r in res["items"]), unsafe_allow_html=True
    )

    pages = max(1, -(-total // per_page))
    first, last = (page - 1) * per_page + 1, min(page * per_page, total)
    info, nav, size = st.columns([4, 5, 1.3])
    info.markdown(
        f'<div class="pagebar">Showing&nbsp;<b>{first:,}–{last:,}</b>&nbsp;of&nbsp;<b>{total:,}</b>&nbsp;reviews'
        + (f"&nbsp;(from {total_in_view:,} in this view)" if total_in_view else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    with nav:
        nums = page_numbers(page, pages)
        cols = st.columns([1.6] + [1] * len(nums) + [1.3])
        if cols[0].button("‹ Previous", disabled=page <= 1, key="pg_prev", width="stretch"):
            st.session_state["rev_page"] = page - 1
            st.rerun()
        for c, n in zip(cols[1:-1], nums, strict=False):
            if n is None:
                c.markdown(
                    '<div style="text-align:center;padding-top:6px;color:#9CA3AF">…</div>',
                    unsafe_allow_html=True,
                )
            elif c.button(
                str(n), key=f"pg_{n}", type="primary" if n == page else "secondary", width="stretch"
            ):
                st.session_state["rev_page"] = n
                st.rerun()
        if cols[-1].button("Next ›", disabled=page >= pages, key="pg_next", width="stretch"):
            st.session_state["rev_page"] = page + 1
            st.rerun()
    size.selectbox(
        "Per page",
        [10, 25, 50],
        key="rev_per_page",
        format_func=lambda n: f"{n} per page",
        label_visibility="collapsed",
    )


# ---------------------------------------------------------------- publish
@st.dialog("Send this email now?")
def confirm_send_dialog(args: dict) -> None:
    st.write(
        f"The **{args['week']}** pulse will be emailed to **{args['recipient']}**. This can’t be undone."
        + (" The Google Doc is updated first." if args["docs"] else "")
    )
    c1, c2 = st.columns(2)
    if c1.button("Cancel", width="stretch"):
        st.rerun()
    if c2.button("Send", type="primary", width="stretch"):
        do_publish(args["week"], args)
        st.rerun()


def do_publish(week: str, args: dict, dry_run: bool = False) -> None:
    st.session_state.pop("pub_msg", None)
    st.session_state.pop("pub_preview", None)
    try:
        pw, out = ui_service.run_publish(
            week,
            docs=args["docs"],
            email=args["email"],
            recipient=args["recipient"],
            force_append=args["force_append"],
            resend=args["resend"],
            dry_run=dry_run,
        )
    except (ValueError, ui_service.BusyError, PublishError) as exc:
        st.session_state["pub_msg"] = ("error", str(exc))
        return
    if dry_run:
        if out.status == "dry_run":
            st.session_state["pub_preview"] = pw
        else:
            st.session_state["pub_msg"] = ("error", out.message)
    elif out.status in ("failed_publish", "failed_validation"):
        st.session_state["pub_msg"] = ("error", out.message)
    else:
        bits = (["Google Doc updated"] if args["docs"] else []) + (
            [f"Gmail draft created for {args['recipient']}"]
            if args["email"] == "draft"
            else [f"email sent to {args['recipient']}"]
            if args["email"] == "send"
            else []
        )
        st.session_state["pub_msg"] = ("ok", " · ".join(bits) + ".")
    refresh()


def show_preview(pw: str, doc_url: str | None) -> None:
    pulse = ui_service.store().load_pulse(pw)
    if not pulse:
        return
    st.subheader("Preview")
    t_email, t_doc = st.tabs(["Email (as delivered)", "Google Doc section"])
    with t_email:
        html = render_html(
            pulse, doc_url or "https://docs.google.com/document/d/…"
        )  # every field escaped
        if hasattr(st, "iframe"):
            st.iframe(html, height=900)
        else:
            components.html(html, height=900, scrolling=True)
    with t_doc:
        st.code(render_plain(pulse), language=None, wrap_lines=True)


def page_publish(week: str) -> None:
    head(
        "Delivery · Google Workspace via MCP",
        "Publish",
        "Append the weekly pulse to the shared Google Doc and email it to stakeholders.",
    )
    st.write("")
    settings = ui_service.settings_payload()
    pub = ui_service.publish_state(week)
    state = pub["state"] or {}
    doc_rec, mail_rec = state.get("doc_append") or {}, state.get("email") or {}
    doc_done, mail_done = doc_rec.get("status") == "done", mail_rec.get("status") == "done"
    st.caption(
        f"Publishing **{pub['week']}**"
        + (" (latest pulse from the all-reviews view)" if week == "all" else "")
    )

    kind, msg = st.session_state.get("pub_msg", (None, None))
    if kind == "error":
        st.error(msg)
    elif kind == "ok":
        st.success(msg)
    if not pub["has_pulse"]:
        st.warning(f"No pulse has been generated for {pub['week']} yet. Use “Run pipeline” first.")
    if settings["problems"]:
        st.warning("Publishing isn’t fully configured: " + " · ".join(settings["problems"]))

    left, right = st.columns(2)
    with left, st.container(border=True):
        st.subheader("Destinations")
        doc_input = st.text_input(
            "Google Doc",
            value=settings["doc_url"] or "",
            placeholder="Leave blank to auto-create on first publish"
            if settings["auto_create_doc"]
            else "Doc URL or ID",
        )
        if st.button("Save document"):
            try:
                ui_service.update_settings(doc_id=doc_input.strip())
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.caption(
            f"[Open “{settings['doc_title']}”]({settings['doc_url']})"
            if settings["doc_url"]
            else f"No Doc yet — “{settings['doc_title']}” is created on the first publish."
        )

        st.markdown(
            '<div class="label-caps" style="margin-top:12px">Email recipient</div>',
            unsafe_allow_html=True,
        )
        recipients = settings["recipients"]
        recipient = st.radio(
            "Recipient",
            recipients,
            label_visibility="collapsed",
            index=recipients.index(settings["recipient"])
            if settings["recipient"] in recipients
            else 0,
            format_func=lambda r: r + ("  (default)" if r == settings["recipient"] else ""),
        )
        with st.form("add_rcpt", clear_on_submit=True, border=False):
            new = st.text_input("Add recipient", placeholder="Add recipient, e.g. team@company.com")
            a, b = st.columns(2)
            if a.form_submit_button("Add & make default", icon=":material/add:"):
                try:
                    ui_service.update_settings(recipient=new)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if b.form_submit_button(
                "Remove selected", icon=":material/delete:", disabled=len(recipients) <= 1
            ):
                ui_service.update_settings(remove_recipient=recipient)
                st.rerun()
        st.caption("One address per email (no cc/bcc). Adding makes it the default.")

    with right, st.container(border=True):
        st.subheader("Publish this pulse")
        docs = st.checkbox(
            "Update Google Doc", value=True, help="Appends this week’s section to the document."
        )
        force = bool(
            docs
            and doc_done
            and st.checkbox(
                "Already appended — add a revised section anyway",
                help="Needed if the content changed.",
            )
        )
        email = st.radio(
            "Email",
            ["off", "draft", "send"],
            index=1,
            horizontal=True,
            format_func={"off": "Don’t email", "draft": "Gmail draft", "send": "Send now"}.get,
        )
        if email != "off":
            st.caption(
                (
                    "Creates a styled HTML draft in Gmail — nothing is sent."
                    if email == "draft"
                    else "Sends the styled HTML email immediately."
                )
                + f" To: **{recipient}**"
            )
        resend = bool(
            email != "off"
            and mail_done
            and st.checkbox(
                f"Email already {'sent' if mail_rec.get('mode') == 'send' else 'drafted'} — do it again (e.g. new recipient)"
            )
        )
        blocked = email != "off" and not docs and not doc_done
        if blocked:
            st.warning(
                "Email goes out only after the Doc has been updated — enable “Update Google Doc”."
            )
        if not settings["mcp_token_set"]:
            st.warning(
                "MCP_AUTH_TOKEN isn’t set, so live publishing will fail. Preview still works."
            )
        args = {
            "week": week,
            "docs": docs,
            "email": email,
            "recipient": recipient,
            "force_append": force,
            "resend": resend,
        }
        idle = not pub["has_pulse"] or (not docs and email == "off") or blocked
        b1, b2 = st.columns(2)
        if b1.button("Preview", icon=":material/visibility:", disabled=idle, width="stretch"):
            do_publish(week, args, dry_run=True)
            st.rerun()
        if b2.button(
            "Publish & send" if email == "send" else "Publish",
            type="primary",
            icon=":material/send:" if email == "send" else ":material/cloud_upload:",
            disabled=idle,
            width="stretch",
        ):
            if email == "send":
                confirm_send_dialog(args)
            else:
                do_publish(week, args)
                st.rerun()

    if st.session_state.get("pub_preview") == pub["week"]:
        show_preview(pub["week"], state.get("doc_url") or settings["doc_url"])

    st.subheader(f"Delivery status · {pub['week']}")
    with st.container(border=True):
        for icon, name, rec, detail in [
            (
                "description",
                "Google Doc",
                doc_rec,
                (
                    f'<a href="{escape(state["doc_url"])}" target="_blank">Open document</a>'
                    if state.get("doc_url")
                    else "Not published"
                ),
            ),
            (
                "mail",
                "Email",
                mail_rec,
                (
                    ("Sent" if mail_rec.get("mode") == "send" else "Drafted")
                    if mail_rec.get("mode")
                    else "Not sent"
                ),
            ),
        ]:
            status = rec.get("status") or "pending"
            extra = " · ".join(escape(str(x)) for x in [when(rec.get("at")), rec.get("error")] if x)
            c1, c2 = st.columns([6, 1])
            c1.markdown(
                f'{ms(icon)} **{name}**<br><span style="color:var(--muted);font-size:13px">{detail}'
                + (f" · {extra}" if extra else "")
                + "</span>",
                unsafe_allow_html=True,
            )
            c2.markdown(
                chip(status, {"done": "ok", "failed": "bad", "unknown": "warn"}.get(status, "")),
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------- runs
@st.fragment(run_every=3)
def job_panel() -> None:
    job = ui_service.job_status()
    st.session_state.setdefault("last_job_state", job["state"])
    if st.session_state["last_job_state"] == "running" and job["state"] != "running":
        refresh()
    st.session_state["last_job_state"] = job["state"]
    if job["state"] == "running":
        st.info(
            f"Running {job.get('week')} ({'dry run' if job.get('dry_run') else 'live publish'}) since "
            f"{when(job.get('started_at'))}. Theming makes ~30 LLM calls and can take 10+ minutes."
        )
    elif job["state"] == "done":
        st.success(f"Last run finished for {job.get('week')}: **{job.get('status')}**.")
    elif job["state"] == "failed":
        st.error(f"Last run failed: {job.get('error')}")


def page_runs(week: str, data: dict | None, hl: dict) -> None:
    head(
        "Pipeline",
        "Runs & Quality",
        "Run history, guardrail checks and LLM usage for the selected week.",
    )
    st.write("")
    c1, c2 = st.columns([5, 1])
    c1.caption(
        f"LLM key {'set' if hl['llm_key_set'] else 'missing'} · MCP token "
        f"{'set' if hl['mcp_token_set'] else 'missing (publishing disabled)'}"
    )
    if c2.button("New run", type="primary", disabled=ui_service.is_running(), width="stretch"):
        run_dialog(load_weeks(), hl, week)
    job_panel()
    if not data:
        return
    m, ev = data.get("manifest"), data.get("eval_report")
    st.subheader(f"Run manifest · {week}")
    if not m:
        st.info("No manifest for this week (only a full run writes one).")
    else:
        st.table(
            {
                "Status": m["status"],
                "Started": when(m["started_at"]),
                "Counts": " · ".join(f"{k}: {v}" for k, v in (m.get("counts") or {}).items())
                or "–",
                "Source errors": str(m["source_errors"]) if m.get("source_errors") else "none",
                "LLM usage": " · ".join(f"{k}: {v}" for k, v in (m.get("llm_usage") or {}).items())
                or "–",
                "Google Doc": m.get("doc_url") or "not published",
            }
        )
    st.subheader("Guardrail checks")
    checks = (m or {}).get("eval_summary") or (ev or {}).get("checks") or ev
    if checks:
        st.table(
            {
                k: ("pass" if v else "fail") if isinstance(v, bool) else str(v)
                for k, v in checks.items()
            }
        )
    else:
        st.info("No eval report for this week.")


# ---------------------------------------------------------------- shell
def sidebar(hl: dict) -> str:
    with st.sidebar:
        st.markdown(
            f'<div class="brand"><div class="logo">{ms("monitoring")}</div>'
            "<div><b>Groww Pulse</b><small>Intelligence Platform</small></div></div>"
            '<div class="nav-label">Workspace</div>',
            unsafe_allow_html=True,
        )
        if "page" not in st.session_state and st.query_params.get("page") in NAV:
            st.session_state["page"] = st.query_params["page"]
        page = st.radio(
            "Workspace", list(NAV), key="page", format_func=NAV.get, label_visibility="collapsed"
        )
        if st.query_params.get("page") != page:
            st.query_params["page"] = page
        running = ui_service.is_running()
        st.markdown(
            '<div class="statusbox"><h6>Pipeline status'
            f'<span class="dot {"info" if running else "ok"}"></span></h6>'
            f'<div class="statusrow"><i class="dot {"ok" if hl["llm_key_set"] else "bad"}"></i>LLM key: {"active" if hl["llm_key_set"] else "missing"}</div>'
            f'<div class="statusrow"><i class="dot {"ok" if hl["mcp_token_set"] else "warn"}"></i>MCP server: {"configured" if hl["mcp_token_set"] else "token missing"}</div>'
            f'<div class="statusrow"><i class="dot {"info" if running else "ok"}"></i>Pipeline: {"running…" if running else "idle"}</div></div>',
            unsafe_allow_html=True,
        )
        st.write("")
        if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
            refresh()
            st.rerun()
    return page


def topbar(weeks: list[dict], hl: dict) -> str | None:
    ids = [w["week"] for w in weeks]
    c1, c2, c3 = st.columns([5, 4, 1.6])
    week = None
    with c1:
        a, b = st.columns([1.1, 3])
        a.markdown('<div class="crumb">Weekly Digest &nbsp;›</div>', unsafe_allow_html=True)
        if ids:
            default = ids.index("all") if "all" in ids else 0
            week = b.selectbox(
                "Week",
                ids,
                index=default,
                key="week",
                label_visibility="collapsed",
                format_func=lambda w: next(
                    (
                        x.get("display_label") or (f"{x['label']} · {w}" if x.get("label") else w)
                        for x in weeks
                        if x["week"] == w
                    ),
                    w,
                ),
            )
    running = ui_service.is_running()
    job = ui_service.job_status()
    last = f" · Last run: {when(job.get('finished_at'))}" if job.get("finished_at") else ""
    c2.markdown(
        '<div class="pillbar">'
        f'<span class="pill"><i class="dot {"info" if running else "ok"}"></i>Pipeline {"running" if running else "idle"}{last}</span>'
        f'<span class="pill outline">{ms("verified_user", True)}Tokens {"configured" if hl["mcp_token_set"] and hl["llm_key_set"] else "incomplete"}</span>'
        "</div>",
        unsafe_allow_html=True,
    )
    if c3.button(
        "Run pipeline",
        type="primary",
        icon=":material/play_arrow:",
        width="stretch",
        disabled=running,
    ):
        run_dialog(weeks, hl, "auto" if week in (None, "all") else week)
    st.markdown('<div class="topline"></div>', unsafe_allow_html=True)
    return week


def main() -> None:
    gate()
    try:
        hl = load_health()
        weeks = load_weeks()
    except Exception as exc:
        st.error(f"Cannot read pulse data: {api_error(exc)}")
        st.stop()

    page = sidebar(hl)
    week = topbar(weeks, hl)

    if page == "Runs & Quality":
        data = None
        if week:
            try:
                data = load_week(week)
            except Exception as exc:
                st.error(api_error(exc))
        page_runs(week or "–", data, hl)
        return
    if not week:
        st.info("No runs yet. Use “Run pipeline” to generate one.")
        return
    if page == "Publish":
        page_publish(week)
        return
    try:
        data = load_week(week)
    except Exception as exc:
        st.error(api_error(exc))
        return
    {
        "Pulse": lambda: page_pulse(data),
        "Themes": lambda: page_themes(data),
        "Reviews": lambda: page_reviews(week, data),
    }[page]()


main()
