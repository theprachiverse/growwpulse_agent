"""Phase 5 tests: MCP publishing with a fake tool server (no network)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pulse.config import MCPRegistryConfig, MCPServerEntry, PublishConfig, Settings
from pulse.models import ActionIdea, Pulse, Quote, Theme
from pulse.publish.errors import PublishError
from pulse.publish.mcp_client import (
    MCPPublishClient,
    ToolError,
    ToolResult,
    parse_envelope,
)
from pulse.publish.pipeline import PublishOptions, publish_pulse
from pulse.publish.plaintext import render_plain
from pulse.publish.validate import parse_doc_id, validate_publish_settings
from pulse.run_store import RunStore

WEEK = "2026-W42"
TOKEN = "t" * 40
DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"

TOOL_MAP = {
    "docs_create": "google_docs_create",
    "docs_append": "google_docs_append_text",
    "email_draft": "gmail_create_draft",
    "email_send": "gmail_send_email",
}


class FakeMCP:
    """Same four tools and result envelopes as the real server; records every call."""

    def __init__(self, tools: set[str] | None = None) -> None:
        self.tools = set(TOOL_MAP) if tools is None else tools
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, ToolError] = {}

    def has(self, op: str) -> bool:
        return op in self.tools

    async def call(self, op: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((op, arguments))
        if op in self.fail:
            raise self.fail[op]
        n = len(self.calls)
        data: dict[str, Any] = {"success": True, "message": "ok", "request_id": f"req{n}"}
        if op == "docs_create":
            data.update(
                document_id=DOC_ID, document_url=f"https://docs.google.com/document/d/{DOC_ID}/edit"
            )
        elif op == "email_draft":
            data.update(draft_id="dr1", message_id="m1")
        elif op == "email_send":
            data.update(message_id="m2", thread_id="th2")
        return ToolResult(data=data, request_id=f"req{n}", tool=TOOL_MAP[op])

    def ops(self) -> list[str]:
        return [c[0] for c in self.calls]


def make_pulse(week: str = WEEK, quote_text: str = "Withdrawal took 3 days") -> Pulse:
    theme = Theme(
        theme_id="t1",
        name="Withdrawals",
        definition="Payout delay issues.",
        review_count=50,
        share=0.25,
        avg_rating=2.1,
        low_rating_share=0.6,
        by_source={"play_store": 40, "app_store": 10},
    )
    quote = Quote(
        rid="rid_1", source="play_store", theme_id="t1", text=quote_text, rating=2, month="Oct 2026"
    )
    action = ActionIdea(
        title="Speed up IMPS",
        rationale="Integrate direct IMPS gateway.",
        theme_id="t1",
        owner_hint="Engineering",
    )
    return Pulse(
        week=week,
        window_start=date(2026, 8, 10),
        window_end=date(2026, 10, 18),
        total_reviews=200,
        reviews_by_source={"play_store": 160, "app_store": 40},
        avg_rating_by_source={"play_store": 3.6, "app_store": 4.0},
        avg_rating=3.68,
        top_themes=[theme, theme, theme],
        quotes=[quote, quote, quote],
        actions=[action, action, action],
        markdown="# md",
        word_count=5,
    )


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "MCP_AUTH_TOKEN": TOKEN,
        "PULSE_RECIPIENT": "me@example.com",
        "PULSE_DATA_DIR": str(tmp_path),
        **overrides,
    }
    s = Settings(_env_file=None, **values)
    s._mcp_config = MCPRegistryConfig(
        servers={
            "google_workspace": MCPServerEntry(
                transport="streamable_http",
                url="https://mcp.example.com/mcp",
                auth_token_env="MCP_AUTH_TOKEN",
                tool_map=TOOL_MAP,
            )
        },
        tool_allowlist=list(TOOL_MAP.values()),
    )
    return s


def seed(tmp_path: Path, *, passed: bool = True, pulse: Pulse | None = None) -> RunStore:
    store = RunStore(base_dir=tmp_path)
    store.save_pulse(WEEK, pulse or make_pulse())
    store.save_eval_report(WEEK, {"passed": passed})
    return store


async def run(settings: Settings, store: RunStore, fake: FakeMCP, **opts: Any) -> Any:
    return await publish_pulse(settings, store, WEEK, PublishOptions(**opts), client=fake)  # type: ignore[arg-type]


# ---------------------------------------------------------------- plain text
def test_plaintext_has_no_markdown_and_labels_sources() -> None:
    text = render_plain(make_pulse())
    assert not re.search(r"(\*\*|^#|^>|`)", text, re.M)
    assert "“Withdrawal took 3 days”" in text
    assert "TOP THEMES" in text and "WHAT USERS SAID" in text and "ACTION IDEAS" in text
    assert "Play Store, 2 stars" in text
    assert len(text) <= 50_000


# ---------------------------------------------------------------- config checks
def test_parse_doc_id() -> None:
    assert parse_doc_id(None) is None
    assert parse_doc_id(DOC_ID) == DOC_ID
    assert parse_doc_id(f"https://docs.google.com/document/d/{DOC_ID}/edit") == DOC_ID
    with pytest.raises(ValueError):
        parse_doc_id("not a doc!")


def test_validation_rejects_bad_config(tmp_path: Path) -> None:
    assert validate_publish_settings(make_settings(tmp_path)) == []
    bad_tok = Settings(_env_file=None, MCP_AUTH_TOKEN="short", PULSE_RECIPIENT="me@example.com")
    assert any("MCP_AUTH_TOKEN" in p for p in validate_publish_settings(bad_tok))
    bad_rcpt = make_settings(tmp_path, PULSE_RECIPIENT="Name <a@b.com>")
    assert any("PULSE_RECIPIENT" in p for p in validate_publish_settings(bad_rcpt))
    http_url = make_settings(tmp_path, MCP_SERVER_URL="http://x.example.com/mcp")
    assert any("https" in p for p in validate_publish_settings(http_url))


def test_missing_doc_id_ok_when_auto_create(tmp_path: Path) -> None:
    assert validate_publish_settings(make_settings(tmp_path)) == []
    s = make_settings(tmp_path)
    s._app_config = s.app_config.model_copy(
        update={"publish": PublishConfig(auto_create_doc=False)}
    )
    assert any("PULSE_DOC_ID" in p for p in validate_publish_settings(s))


# ---------------------------------------------------------------- envelope parsing
def test_parse_envelope_success_and_error() -> None:
    ok = parse_envelope(
        {"success": True, "message": "m", "request_id": "r", "draft_id": "d"}, "", "t"
    )
    assert ok.data["draft_id"] == "d" and ok.request_id == "r"
    with pytest.raises(ToolError) as e:
        parse_envelope(
            {
                "success": False,
                "request_id": "r",
                "error": {"code": "AUTH_EXPIRED", "message": "x", "retryable": False},
            },
            "",
            "t",
        )
    assert e.value.is_auth and e.value.request_id == "r"
    # falls back to the JSON line in the text content
    fb = parse_envelope(None, 'Done.\n{"success": true, "request_id": "q"}', "t")
    assert fb.request_id == "q"


# ---------------------------------------------------------------- docs
async def test_first_run_creates_doc_appends_and_drafts(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    out = await run(make_settings(tmp_path), store, fake)
    assert out.status == "success"
    assert fake.ops() == ["docs_create", "docs_append", "email_draft"]
    append_args = fake.calls[1][1]
    assert append_args["document_id"] == DOC_ID
    assert append_args["heading"].startswith("Groww Weekly Review Pulse")
    assert not re.search(r"(\*\*|^#|`)", append_args["text"], re.M)
    draft_args = fake.calls[2][1]
    assert draft_args["to"] == "me@example.com"
    assert "cc" not in draft_args and "bcc" not in draft_args
    assert out.state.doc_url in draft_args["body"]
    assert store.load_doc_ref()["doc_id"] == DOC_ID  # type: ignore[index]


async def test_rerun_same_week_does_nothing(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    s = make_settings(tmp_path)
    await run(s, store, fake)
    fake.calls.clear()
    out = await run(s, store, fake)
    assert out.status == "success" and fake.calls == []


async def test_doc_created_once_across_weeks(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    s = make_settings(tmp_path)
    await run(s, store, fake)
    store.save_pulse("2026-W43", make_pulse("2026-W43"))
    store.save_eval_report("2026-W43", {"passed": True})
    await publish_pulse(s, store, "2026-W43", PublishOptions(), client=fake)  # type: ignore[arg-type]
    assert fake.ops().count("docs_create") == 1
    assert fake.ops().count("docs_append") == 2


async def test_pulse_doc_id_override_skips_create(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    await run(make_settings(tmp_path, PULSE_DOC_ID=DOC_ID), store, fake)
    assert "docs_create" not in fake.ops()


async def test_changed_content_needs_force_append(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    s = make_settings(tmp_path)
    await run(s, store, fake)
    store.save_pulse(WEEK, make_pulse(quote_text="Different words entirely here"))
    fake.calls.clear()
    out = await run(s, store, fake)
    assert out.status == "failed_publish" and "--force-append" in out.message
    assert fake.calls == []
    out = await run(s, store, fake, force_append=True, email=False)
    assert out.status == "success"
    assert fake.calls[0][1]["heading"].endswith("UTC)")  # "(revised ...)"
    assert "revised" in fake.calls[0][1]["heading"]


async def test_create_unknown_outcome_blocks_second_create(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    fake.fail["docs_create"] = ToolError("OPERATION_OUTCOME_UNKNOWN", "maybe", request_id="r9")
    s = make_settings(tmp_path)
    out = await run(s, store, fake)
    assert out.status == "failed_publish"
    fake.fail.clear()
    fake.calls.clear()
    out = await run(s, store, fake)
    assert out.status == "failed_publish" and "PULSE_DOC_ID" in out.message
    assert "docs_create" not in fake.ops()


async def test_append_unknown_outcome_requires_flag(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    fake.fail["docs_append"] = ToolError("OPERATION_OUTCOME_UNKNOWN", "maybe", request_id="r1")
    s = make_settings(tmp_path, PULSE_DOC_ID=DOC_ID)
    out = await run(s, store, fake)
    assert out.status == "failed_publish"
    assert store.load_publish_state(WEEK).doc_append.status == "unknown"  # type: ignore[union-attr]
    assert fake.ops() == ["docs_append"]  # not retried, email not attempted
    fake.fail.clear()
    fake.calls.clear()
    out = await run(s, store, fake)
    assert out.status == "failed_publish" and fake.calls == []
    out = await run(s, store, fake, doc_confirmed=True)
    assert out.status == "success" and fake.ops() == ["email_draft"]


# ---------------------------------------------------------------- email
async def test_send_mode_sends_not_drafts(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    await run(make_settings(tmp_path, PULSE_DOC_ID=DOC_ID), store, fake, send=True)
    assert "email_send" in fake.ops() and "email_draft" not in fake.ops()
    send_args = next(a for o, a in fake.calls if o == "email_send")
    assert send_args["to"] == "me@example.com" and "cc" not in send_args


async def test_draft_mode_never_sends(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    await run(make_settings(tmp_path, PULSE_DOC_ID=DOC_ID), store, fake)
    assert "email_send" not in fake.ops()


async def test_email_off_and_dry_run_make_no_email_calls(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    s = make_settings(tmp_path, PULSE_DOC_ID=DOC_ID)
    s._app_config = s.app_config.model_copy(update={"publish": PublishConfig(email_mode="off")})
    await run(s, store, fake)
    assert fake.ops() == ["docs_append"]
    fake2 = FakeMCP()
    out = await run(
        make_settings(tmp_path / "x", PULSE_DOC_ID=DOC_ID),
        seed(tmp_path / "x"),
        fake2,
        dry_run=True,
    )
    assert out.status == "dry_run" and fake2.calls == []


async def test_failed_validation_pulse_is_never_published(tmp_path: Path) -> None:
    store, fake = seed(tmp_path, passed=False), FakeMCP()
    out = await run(make_settings(tmp_path), store, fake, send=True)
    assert out.status == "failed_validation" and fake.calls == []


async def test_email_resend_and_unknown_outcome(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    s = make_settings(tmp_path, PULSE_DOC_ID=DOC_ID)
    await run(s, store, fake)
    fake.calls.clear()
    await run(s, store, fake, resend=True)
    assert fake.ops() == ["email_draft"]  # docs skipped, email redone

    store2, fake2 = seed(tmp_path / "u"), FakeMCP()
    fake2.fail["email_draft"] = ToolError("OPERATION_OUTCOME_UNKNOWN", "maybe", request_id="e1")
    s2 = make_settings(tmp_path / "u", PULSE_DOC_ID=DOC_ID)
    out = await run(s2, store2, fake2)
    assert out.status == "failed_publish"
    assert store2.load_publish_state(WEEK).email.status == "unknown"  # type: ignore[union-attr]
    fake2.fail.clear()
    fake2.calls.clear()
    out = await run(s2, store2, fake2)
    assert out.status == "failed_publish" and fake2.calls == []


async def test_partial_failure_retry_only_does_email(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    fake.fail["email_draft"] = ToolError("PERMISSION_DENIED", "no", request_id="e2")
    s = make_settings(tmp_path, PULSE_DOC_ID=DOC_ID)
    out = await run(s, store, fake)
    assert out.status == "failed_publish" and "npm run auth" in out.message
    assert out.state.doc_append.status == "done"
    fake.fail.clear()
    fake.calls.clear()
    out = await run(s, store, fake)
    assert out.status == "success" and fake.ops() == ["email_draft"]


async def test_missing_tool_fails_fast_without_downgrade(tmp_path: Path) -> None:
    store = seed(tmp_path)
    fake = FakeMCP(tools={"docs_create", "docs_append", "email_send"})  # no draft tool
    with pytest.raises(PublishError, match="email_draft"):
        await run(make_settings(tmp_path), store, fake)
    assert fake.calls == []


# ---------------------------------------------------------------- client
class _StubSession:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.n = 0

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        r = self.results[self.n]
        self.n += 1
        if isinstance(r, Exception):
            raise r
        return r


class _Res:
    def __init__(self, structured: dict[str, Any]) -> None:
        self.structured_content = structured
        self.content: list[Any] = []


def _client(session: _StubSession) -> MCPPublishClient:
    async def no_sleep(_: float) -> None:
        return None

    c = MCPPublishClient(
        "https://x/mcp", TOKEN, TOOL_MAP, ["google_docs_append_text"], sleep=no_sleep
    )
    c._session = session
    c.server_tools = {t: {"input_schema": {}} for t in TOOL_MAP.values()}
    return c


async def test_client_allowlist_blocks_other_tools() -> None:
    c = _client(_StubSession([]))
    with pytest.raises(ToolError, match="allow-list"):
        await c.call("email_send", {})


async def test_client_retries_rate_limit_but_not_unknown() -> None:
    limited = _Res(
        {
            "success": False,
            "request_id": "a",
            "error": {"code": "RATE_LIMITED", "message": "x", "retryable": True},
        }
    )
    ok = _Res({"success": True, "request_id": "b", "message": "ok"})
    s = _StubSession([limited, ok])
    assert (await _client(s).call("docs_append", {})).request_id == "b"
    assert s.n == 2

    unknown = _Res(
        {
            "success": False,
            "request_id": "c",
            "error": {"code": "OPERATION_OUTCOME_UNKNOWN", "message": "x", "retryable": False},
        }
    )
    s2 = _StubSession([unknown, ok])
    with pytest.raises(ToolError) as e:
        await _client(s2).call("docs_append", {})
    assert e.value.is_unknown_outcome and s2.n == 1


async def test_client_transport_failure_is_unknown_outcome() -> None:
    s = _StubSession([TimeoutError()])
    with pytest.raises(ToolError) as e:
        await _client(s).call("docs_append", {})
    assert e.value.is_unknown_outcome and s.n == 1


def test_client_require_names_missing_tool() -> None:
    c = _client(_StubSession([]))
    del c.server_tools["gmail_create_draft"]
    with pytest.raises(ToolError, match="gmail_create_draft"):
        c.require(["email_draft"])


def test_no_google_client_code_in_repo() -> None:
    root = Path(__file__).resolve().parents[2] / "src"
    banned = ("googleapiclient", "google.oauth2", "google-auth", "google_auth")
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert not any(b in text for b in banned), f


# ---------------------------------------------------------------- html email
def test_html_email_is_styled_and_escapes_content() -> None:
    from pulse.publish.html_email import render_html

    pulse = make_pulse(quote_text="Fees <b>too</b> high & unclear")
    html = render_html(pulse, "https://docs.google.com/document/d/abc/edit")
    assert html.startswith("<!doctype html>")
    assert "Fees &lt;b&gt;too&lt;/b&gt; high &amp; unclear" in html
    assert "<b>too</b>" not in html
    assert "Open full history in Google Docs" in html
    assert "https://docs.google.com/document/d/abc/edit" in html


async def test_email_sent_as_html_by_default_and_text_when_configured(tmp_path: Path) -> None:
    store, fake = seed(tmp_path), FakeMCP()
    await run(make_settings(tmp_path), store, fake)
    draft = fake.calls[-1][1]
    assert draft["body_type"] == "html" and draft["body"].startswith("<!doctype html>")
    assert out_doc_url(store) in draft["body"]

    store2, fake2 = seed(tmp_path / "t"), FakeMCP()
    settings = make_settings(tmp_path / "t")
    settings.app_config.publish.email_format = "text"
    await run(settings, store2, fake2)
    assert fake2.calls[-1][1]["body_type"] == "text"


def out_doc_url(store: RunStore) -> str:
    ref = store.load_doc_ref()
    return str(ref["doc_url"]) if ref else ""
