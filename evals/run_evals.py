"""Offline eval suite for the pulse agent (`pulse eval`). See Docs/eval.md.

Default mode is offline and free: it re-checks a week's *recorded* run artifacts
(`data/runs/<week>/`), the frozen fixtures under `evals/`, and a fake MCP server.
`--live` additionally re-runs theming (stability); `--judge` adds LLM-as-judge scores.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import statistics
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.fake_mcp import DOC_ID, RECIPIENT, FakeMCP, make_settings
from evals.metrics import OTHER, macro_f1, pct, recall_by_category, stability_jaccard
from pulse.compose.shorten import count_pulse_body_words
from pulse.config import Settings, get_settings
from pulse.guardrails.validators import validate_pulse
from pulse.models import CleanReview, Pulse, Theme
from pulse.preprocess.cleaner import normalize_text
from pulse.preprocess.pii import scrub_pii, scrub_review_fields
from pulse.publish.email_publisher import build_message
from pulse.publish.errors import PublishError
from pulse.publish.pipeline import PublishOptions, publish_pulse
from pulse.run_store import RunStore

EVALS_DIR = Path(__file__).parent
REPO_ROOT = EVALS_DIR.parent

GATES = ("hard", "quality", "soft", "track")
BANNED_THEME_WORDS = ("positive", "negative", "general", "feedback", "complaints", "misc")
RAW_FIELD_PATTERNS = re.compile(
    r"userName|\"author\"|reviewId|userImage|lh3\.googleusercontent\.com", re.IGNORECASE
)
HEX_ID = re.compile(r"\b[0-9a-f]{16,}\b")
GOOGLE_API_PATTERNS = (
    "googleapiclient",
    "google.oauth2",
    "google_auth_oauthlib",
    "gmail.googleapis.com",
    "docs.googleapis.com",
)


@dataclass
class EvalResult:
    id: str
    name: str
    gate: str
    value: Any
    target: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


class HarnessError(Exception):
    """The harness itself could not run (missing fixture, bad artifact): exit code 2."""


def _res(
    id_: str,
    name: str,
    gate: str,
    value: Any,
    target: str,
    passed: bool,
    **details: Any,
) -> EvalResult:
    return EvalResult(id_, name, gate, value, target, passed, details)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise HarnessError(f"Missing fixture: {path}")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------------------- PII
def _scrub_chain(text: str) -> str:
    _, out, _ = scrub_review_fields(None, normalize_text(text))
    return out


def eval_pii() -> list[EvalResult]:
    cases = _jsonl(EVALS_DIR / "fixtures" / "pii_injection.jsonl")
    negatives = _jsonl(EVALS_DIR / "fixtures" / "pii_negatives.jsonl")
    out: list[EvalResult] = []

    def run_tier(tier: str) -> tuple[dict[str, float], list[str]]:
        results: list[tuple[str, bool]] = []
        failed: list[str] = []
        for c in (c for c in cases if c["tier"] == tier):
            scrubbed = _scrub_chain(c["text"])
            leaked = any(v in scrubbed for v in c["values"])
            typed = all(f"[{t}]" in scrubbed for t in c["expected_types"])
            ok = not leaked and typed
            results.append((c["category"], ok))
            if not ok:
                failed.append(c["id"])
        return recall_by_category(results), failed

    recall_a, failed_a = run_tier("A")
    out.append(
        _res(
            "EV-PII-01",
            "Scrubber recall, tier A",
            "hard",
            min(recall_a.values()) if recall_a else 0.0,
            "== 1.0",
            not failed_a,
            per_category=recall_a,
            failed_cases=failed_a,
        )
    )
    recall_b, failed_b = run_tier("B")
    out.append(
        _res(
            "EV-PII-03",
            "Scrubber recall, tier B (names, obfuscated digits)",
            "track",
            statistics.fmean(recall_b.values()) if recall_b else 0.0,
            "track",
            True,
            per_category=recall_b,
            failed_cases=failed_b,
        )
    )

    changed = [n["id"] for n in negatives if _scrub_chain(n["text"]) != normalize_text(n["text"])]
    fp = pct(len(changed), len(negatives))
    out.append(
        _res(
            "EV-PII-04",
            "False-positive rate on negatives",
            "quality",
            round(fp, 4),
            "<= 0.02",
            fp <= 0.02,
            changed=changed,
        )
    )

    unstable = [
        c["id"] for c in cases if _scrub_chain(_scrub_chain(c["text"])) != _scrub_chain(c["text"])
    ]
    out.append(
        _res("EV-PII-05", "Placeholder idempotence", "hard", len(unstable), "== 0", not unstable)
    )
    return out


def eval_output_leaks(pulse: Pulse, week_dir: Path) -> EvalResult:
    """EV-PII-02: re-scan every output artifact (and the email body) for PII."""
    cases = _jsonl(EVALS_DIR / "fixtures" / "pii_injection.jsonl")
    injected = [v for c in cases for v in c["values"]]
    texts: dict[str, str] = {"email_body": build_message(pulse, None)[1]}
    for name in ("pulse.md", "pulse.json", "themes.json"):
        p = week_dir / name
        if p.exists():
            texts[name] = p.read_text(encoding="utf-8")
    leaks: list[str] = []
    for name, text in texts.items():
        _, hits = scrub_pii(text)
        # pulse.json carries the 'rid' pseudonyms and ISO dates; only real PII types count.
        if hits:
            leaks.append(f"{name}: recognizer hit {hits}")
        if any(v in text for v in injected):
            leaks.append(f"{name}: injected value present")
    return _res("EV-PII-02", "Output PII leaks", "hard", len(leaks), "== 0", not leaks, leaks=leaks)


# --------------------------------------------------------------------------- compliance
def _tokens_to_words(text: str) -> int:
    return len(text.split())


def eval_compliance(
    settings: Settings, store: RunStore, week: str
) -> tuple[list[EvalResult], Pulse, list[Theme], list[CleanReview], list[dict[str, Any]]]:
    pulse = store.load_pulse(week)
    themes = store.load_themes(week)
    reviews = store.load_clean_reviews(week)
    assignments = store.load_assignments(week)
    if pulse is None or themes is None or not reviews or not assignments:
        raise HarnessError(
            f"Run artifacts for {week} are incomplete (need pulse.json, themes.json, "
            "reviews_clean.jsonl, assignments.jsonl). Run `pulse run` first."
        )
    cfg = settings.app_config
    by_rid = {r.rid: r for r in reviews}
    out: list[EvalResult] = []

    # Same code as the per-run guardrails, so the two can never disagree.
    report = validate_pulse(pulse, reviews, themes, cfg)
    out.append(
        _res(
            "EV-CMP-00",
            "Per-run guardrails re-check",
            "hard",
            len(report.hard_failures),
            "== 0",
            report.passed,
            hard_failures=report.hard_failures,
        )
    )

    ids = {t.theme_id for t in themes}
    top_ids = [t.theme_id for t in pulse.top_themes]
    ok = (
        1 <= len(themes) <= cfg.theming.max_themes
        and len(top_ids) == 3
        and OTHER not in top_ids
        and set(top_ids) <= ids
    )
    out.append(_res("EV-CMP-01", "Theme count and Top 3", "hard", len(themes), "1..5, top 3", ok))

    bad_quotes = []
    for q in pulse.quotes:
        review = by_rid.get(q.rid)
        if review is None or q.text not in review.clean_text:
            bad_quotes.append(q.rid)
    out.append(
        _res(
            "EV-CMP-02",
            "Quote faithfulness (exact substring)",
            "hard",
            1 - pct(len(bad_quotes), len(pulse.quotes)),
            "== 1.0",
            not bad_quotes,
            unfaithful=bad_quotes,
        )
    )

    problems: list[str] = []
    if len({q.rid for q in pulse.quotes}) != 3:
        problems.append("quotes do not have 3 distinct rids")
    for q in pulse.quotes:
        review = by_rid.get(q.rid)
        n = _tokens_to_words(q.text)
        if not 8 <= n <= 40:
            problems.append(f"{q.rid}: {n} words")
        if review is not None and review.source != q.source:
            problems.append(f"{q.rid}: source label mismatch")
        if q.theme_id not in top_ids:
            problems.append(f"{q.rid}: theme not in Top 3")
    out.append(
        _res(
            "EV-CMP-03",
            "Quote rules",
            "hard",
            len(problems),
            "== 0",
            not problems,
            problems=problems,
        )
    )

    words = count_pulse_body_words(pulse.markdown)
    out.append(
        _res(
            "EV-CMP-04",
            "Length compliance (words)",
            "hard",
            words,
            f"<= {cfg.compose.max_words}",
            words <= cfg.compose.max_words,
        )
    )

    act_problems = []
    if sorted(a.theme_id for a in pulse.actions) != sorted(top_ids):
        act_problems.append("actions are not one per Top-3 theme")
    out.append(
        _res(
            "EV-CMP-05",
            "Action count and linking",
            "hard",
            len(act_problems),
            "== 0",
            not act_problems,
        )
    )

    ident = re.findall(r"rid_[0-9a-f]+", pulse.markdown) + HEX_ID.findall(pulse.markdown)
    out.append(_res("EV-CMP-06", "No identifiers in output", "hard", len(ident), "== 0", not ident))

    # --- theming
    other = sum(1 for a in assignments if a["theme_id"] == OTHER)
    other_share = pct(other, len(assignments))
    out.append(
        _res(
            "EV-THM-02",
            "Coverage ('other' share)",
            "quality",
            round(other_share, 4),
            "<= 0.20",
            other_share <= 0.20,
        )
    )

    expected_top = _expected_top3(assignments, themes, cfg.ranking.source_weighting)
    out.append(
        _res(
            "EV-THM-04",
            "Ranking correctness (independent recompute)",
            "hard",
            top_ids,
            f"== {expected_top}",
            top_ids == expected_top,
            weighting=cfg.ranking.source_weighting,
        )
    )
    banned = [t.name for t in themes if any(w in t.name.lower() for w in BANNED_THEME_WORDS)]
    out.append(
        _res(
            "EV-THM-06",
            "No generic theme names",
            "quality",
            len(banned),
            "== 0",
            not banned,
            themes=banned,
        )
    )

    # --- ingestion / privacy of stored artifacts
    week_dir = store.run_dir(week)
    hits = []
    for p in [*week_dir.glob("*"), *(EVALS_DIR / "fixtures").glob("*.jsonl")]:
        if p.is_file() and RAW_FIELD_PATTERNS.search(
            p.read_text(encoding="utf-8", errors="ignore")
        ):
            hits.append(p.name)
    out.append(
        _res(
            "EV-ING-02",
            "No raw user fields on disk",
            "hard",
            len(hits),
            "== 0",
            not hits,
            files=hits,
        )
    )
    out.append(eval_output_leaks(pulse, week_dir))
    return out, pulse, themes, reviews, assignments


def _expected_top3(
    assignments: list[dict[str, Any]], themes: list[Theme], weighting: str
) -> list[str]:
    total = len(assignments)
    total_by_src = {
        s: sum(1 for a in assignments if a["source"] == s) for s in ("play_store", "app_store")
    }
    scored = []
    for t in themes:
        mine = [a for a in assignments if a["theme_id"] == t.theme_id]
        low = round(sum(1 for a in mine if a["rating"] <= 2) / len(mine), 4) if mine else 0.0
        helpful = sum(a["helpful_votes"] for a in mine)
        if weighting == "balanced":
            shares = [
                sum(1 for a in mine if a["source"] == s) / total_by_src[s]
                for s in total_by_src
                if total_by_src[s]
            ]
            primary = 0.5 * sum(shares) if len(shares) == 2 else sum(shares)
        else:
            primary = len(mine) / total
        scored.append((-primary, -low, -helpful, t.theme_id, t.theme_id))
    scored.sort()
    return [s[-1] for s in scored[:3]]


# --------------------------------------------------------------------------- quality
GOLDEN_PATH = EVALS_DIR / "golden" / "theme_labels_v1.csv"


def eval_theme_f1(assignments: list[dict[str, Any]]) -> EvalResult:
    if not GOLDEN_PATH.exists():
        raise HarnessError(f"Missing golden set: {GOLDEN_PATH}")
    with GOLDEN_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    pred = {a["rid"]: a["theme_id"] for a in assignments}
    gold = {r["rid"]: r["golden_theme"] for r in rows if r["rid"] in pred}
    verified = sum(1 for r in rows if r["labeller"] != "heuristic-draft")
    f1, per_label = macro_f1(pred, gold)
    # Against unreviewed draft labels the score says little about the system, so it is tracked
    # (not gated) until people have confirmed the labels; then it becomes a quality gate.
    return _res(
        "EV-THM-01",
        "Theme macro-F1 vs golden set",
        "quality" if verified else "track",
        round(f1, 3),
        ">= 0.75",
        f1 >= 0.75 or not verified,
        per_label=per_label,
        golden_rows=len(rows),
        scored_rows=len(gold),
        human_verified_rows=verified,
        warning=(
            "Golden labels are an unreviewed keyword-rule draft; treat this score as indicative "
            "until two people confirm the labels (Docs/eval.md 3.2)."
            if verified == 0
            else ""
        ),
    )


def eval_stability(settings: Settings, week: str, store: RunStore) -> EvalResult:
    """EV-THM-03 (live): re-run theming twice into scratch stores and compare assignments."""
    from pulse.llm.client import get_llm_client
    from pulse.theming.pipeline import run_theming_pipeline

    reviews = store.load_clean_reviews(week)
    runs: list[dict[str, str]] = []
    llm = get_llm_client()
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = RunStore(base_dir=tmp)
            scratch.save_clean_reviews(week, reviews)
            _, assigns = run_theming_pipeline(
                week=week, config=settings.app_config, run_store=scratch, llm_client=llm
            )
            runs.append({a.rid: a.theme_id for a in assigns})
    score = stability_jaccard(runs[0], runs[1])
    return _res(
        "EV-THM-03",
        "Theme stability (Jaccard, live)",
        "quality",
        round(score, 3),
        ">= 0.70",
        score >= 0.70,
    )


def eval_judges(
    pulse: Pulse, reviews: list[CleanReview], assignments: list[dict[str, Any]]
) -> list[EvalResult]:
    from evals import judges
    from pulse.llm.client import get_llm_client

    llm = get_llm_client()
    out: list[EvalResult] = []
    checks: tuple[tuple[str, str, Callable[[], list[judges.JudgeResult]]], ...] = (
        (
            "EV-JDG-01",
            "Theme coherence (1-5)",
            lambda: judges.judge_theme_coherence(llm, pulse, reviews, assignments),
        ),
        (
            "EV-JDG-02",
            "Action groundedness (1-5)",
            lambda: judges.judge_action_groundedness(llm, pulse, reviews, assignments),
        ),
        (
            "EV-JDG-03",
            "Pulse usefulness (1-5)",
            lambda: [judges.judge_pulse_usefulness(llm, pulse)],
        ),
    )
    for id_, name, fn in checks:
        results = fn()
        score = judges.mean_score(results)
        out.append(
            _res(
                id_,
                name,
                "quality",
                round(score, 2),
                ">= 4.0",
                score >= 4.0,
                low_agreement=[r.item for r in results if r.low_agreement],
                judge_prompt_version=judges.JUDGE_PROMPT_VERSION,
            )
        )
    return out


# --------------------------------------------------------------------------- publish (fake MCP)
def eval_publish(pulse: Pulse) -> list[EvalResult]:
    return asyncio.run(_eval_publish(pulse))


async def _publish(
    settings: Settings, store: RunStore, fake: FakeMCP, week: str, **opts: Any
) -> Any:
    return await publish_pulse(settings, store, week, PublishOptions(**opts), client=fake)


def _seed(tmp: str, pulse: Pulse, passed: bool = True) -> RunStore:
    store = RunStore(base_dir=tmp)
    store.save_pulse(pulse.week, pulse)
    store.save_eval_report(pulse.week, {"passed": passed})
    return store


async def _eval_publish(pulse: Pulse) -> list[EvalResult]:
    week = pulse.week
    out: list[EvalResult] = []

    # Draft mode, doc created, two runs for the same week.
    with tempfile.TemporaryDirectory() as tmp:
        store, fake = _seed(tmp, pulse), FakeMCP()
        s = make_settings(Path(tmp))
        await _publish(s, store, fake, week)
        first = list(fake.calls)
        await _publish(s, store, fake, week)
        ops = fake.ops()
        out.append(
            _res(
                "EV-PUB-01",
                "Doc created once, appended once per week",
                "hard",
                ops,
                "create=1, append=1, draft=1",
                ops.count("docs_create") == 1
                and ops.count("docs_append") == 1
                and ops.count("email_draft") == 1,
            )
        )
        append_args = next(a for o, a in first if o == "docs_append")
        text = append_args["text"]
        has_quotes = all(q.text in text for q in pulse.quotes)
        has_heads = all(h in text for h in ("TOP THEMES", "WHAT USERS SAID", "ACTION IDEAS"))
        no_md = not re.search(r"\*\*|^#|`", text, re.M)
        out.append(
            _res(
                "EV-PUB-02",
                "Doc content equals pulse (quotes, headings, no Markdown)",
                "hard",
                has_quotes and has_heads and no_md,
                "True",
                has_quotes and has_heads and no_md,
            )
        )
        draft = next(a for o, a in first if o == "email_draft")
        ok_draft = (
            draft["to"] == RECIPIENT
            and "cc" not in draft
            and "bcc" not in draft
            and draft["subject"].endswith(week)
            and f"docs.google.com/document/d/{DOC_ID}" in draft["body"]
            and str(pulse.window_start) in draft["body"]
            and draft["body_type"] == "html"
        )
        out.append(
            _res(
                "EV-PUB-04",
                "Email content (recipient, link, footer, no cc/bcc)",
                "hard",
                ok_draft,
                "True",
                ok_draft,
            )
        )

    # Draft mode never sends; send mode sends only when asked.
    with tempfile.TemporaryDirectory() as tmp:
        store, fake = _seed(tmp, pulse), FakeMCP()
        await _publish(make_settings(Path(tmp), PULSE_DOC_ID=DOC_ID), store, fake, week)
        sends_in_draft = fake.ops().count("email_send")
    with tempfile.TemporaryDirectory() as tmp:
        store, fake = _seed(tmp, pulse), FakeMCP()
        await _publish(make_settings(Path(tmp), PULSE_DOC_ID=DOC_ID), store, fake, week, send=True)
        send_ops = fake.ops()
    with tempfile.TemporaryDirectory() as tmp:
        store, fake = _seed(tmp, pulse, passed=False), FakeMCP()
        blocked = await _publish(make_settings(Path(tmp)), store, fake, week, send=True)
        blocked_ok = blocked.status == "failed_validation" and not fake.calls
    pub3 = (
        sends_in_draft == 0
        and send_ops.count("email_send") == 1
        and "email_draft" not in send_ops
        and blocked_ok
    )
    out.append(
        _res(
            "EV-PUB-03",
            "Send only when asked; failed-validation pulse never delivered",
            "hard",
            {
                "sends_in_draft_mode": sends_in_draft,
                "sends_in_send_mode": send_ops.count("email_send"),
                "blocked": blocked_ok,
            },
            "0 / 1 / True",
            pub3,
        )
    )

    # Fail fast on a missing tool, with no calls made.
    with tempfile.TemporaryDirectory() as tmp:
        store, fake = _seed(tmp, pulse), FakeMCP(tools={"docs_create", "docs_append", "email_send"})
        try:
            await _publish(make_settings(Path(tmp)), store, fake, week)
            failed_fast = False
        except PublishError:
            failed_fast = not fake.calls
    out.append(
        _res(
            "EV-PUB-06",
            "Fail fast on missing tool (no fallback to send)",
            "hard",
            failed_fast,
            "True",
            failed_fast,
        )
    )

    hits = []
    for p in (REPO_ROOT / "src").rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        hits += [f"{p.name}:{pat}" for pat in GOOGLE_API_PATTERNS if pat in t]
    out.append(
        _res(
            "EV-PUB-05",
            "No Google API code in src/",
            "hard",
            len(hits),
            "== 0",
            not hits,
            hits=hits,
        )
    )
    return out


# --------------------------------------------------------------------------- orchestration
def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def summarize(results: list[EvalResult]) -> dict[str, int]:
    def count(gate: str) -> tuple[int, int]:
        rs = [r for r in results if r.gate == gate]
        return sum(r.passed for r in rs), len(rs)

    hp, ht = count("hard")
    qp, qt = count("quality")
    sp, st = count("soft")
    return {
        "hard_passed": hp,
        "hard_total": ht,
        "quality_passed": qp,
        "quality_total": qt,
        "soft_warnings": st - sp,
    }


def exit_code(results: list[EvalResult]) -> int:
    return 1 if any(not r.passed for r in results if r.gate in ("hard", "quality")) else 0


def run_suite(
    week: str,
    suite: str = "all",
    *,
    live: bool = False,
    judge: bool = False,
    settings: Settings | None = None,
    data_dir: Path | None = None,
) -> tuple[list[EvalResult], dict[str, Any]]:
    """Runs the selected suites and returns (results, report dict)."""
    settings = settings or get_settings()
    store = RunStore(base_dir=data_dir or settings.data_dir)
    results: list[EvalResult] = []
    steps: dict[str, Callable[[], None]] = {}

    ctx: dict[str, Any] = {}

    def compliance() -> None:
        res, pulse, themes, reviews, assigns = eval_compliance(settings, store, week)
        ctx.update(pulse=pulse, themes=themes, reviews=reviews, assignments=assigns)
        results.extend(res)
        results.extend(eval_pii())

    def quality() -> None:
        if "assignments" not in ctx:
            _, pulse, themes, reviews, assigns = eval_compliance(settings, store, week)
            ctx.update(pulse=pulse, themes=themes, reviews=reviews, assignments=assigns)
        results.append(eval_theme_f1(ctx["assignments"]))
        if live:
            results.append(eval_stability(settings, week, store))
        if judge:
            results.extend(eval_judges(ctx["pulse"], ctx["reviews"], ctx["assignments"]))

    def publish() -> None:
        if "pulse" not in ctx:
            _, pulse, *_ = eval_compliance(settings, store, week)
            ctx["pulse"] = pulse
        results.extend(eval_publish(ctx["pulse"]))

    steps = {"compliance": compliance, "quality": quality, "publish": publish}
    for name in steps if suite == "all" else [suite]:
        steps[name]()

    cfg_blob = json.dumps(settings.app_config.model_dump(), sort_keys=True, default=str)
    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "suite": suite,
        "week": week,
        "offline": not (live or judge),
        "config_hash": "sha256:" + hashlib.sha256(cfg_blob.encode()).hexdigest()[:16],
        "versions": {
            "chat_model": settings.app_config.llm.model_chat,
            "embed_model": settings.app_config.llm.model_embed,
            "golden_set": GOLDEN_PATH.stem,
        },
        "results": [asdict(r) for r in results],
        "summary": summarize(results),
        "exit_code": exit_code(results),
    }
    return results, report


def write_report(report: dict[str, Any], reports_dir: Path | None = None) -> Path:
    d = reports_dir or EVALS_DIR / "reports"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
