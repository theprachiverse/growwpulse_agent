"""Phase 5 orchestration: validated pulse -> Google Doc (create/append) -> email (draft/send)."""

from __future__ import annotations

from dataclasses import dataclass

from pulse.config import Settings
from pulse.models import PublishState
from pulse.observability.logging import get_logger
from pulse.publish.docs_publisher import DocsPublisher
from pulse.publish.email_publisher import EmailMode, EmailPublisher, build_message
from pulse.publish.errors import PublishError
from pulse.publish.mcp_client import MCPPublishClient, ToolCaller, ToolError
from pulse.publish.plaintext import render_plain
from pulse.publish.validate import (
    SERVER_NAME,
    parse_doc_id,
    server_url,
    validate_publish_settings,
)
from pulse.run_store import RunStore

logger = get_logger("pulse.publish.pipeline")


@dataclass
class PublishOptions:
    docs: bool = True
    email: bool = True
    send: bool = False  # force email_mode=send for this run
    force_append: bool = False
    resend: bool = False
    doc_confirmed: bool = False
    email_confirmed: bool = False
    dry_run: bool = False


@dataclass
class PublishOutcome:
    status: str  # success | failed_publish | failed_validation | dry_run
    state: PublishState
    message: str = ""


def effective_email_mode(settings: Settings, opts: PublishOptions) -> EmailMode:
    return "send" if opts.send else settings.app_config.publish.email_mode


def _needed_ops(opts: PublishOptions, mode: EmailMode, need_create: bool) -> list[str]:
    ops: list[str] = []
    if opts.docs:
        ops.append("docs_append")
        if need_create:
            ops.append("docs_create")
    if opts.email and mode == "draft":
        ops.append("email_draft")
    if opts.email and mode == "send":
        ops.append("email_send")
    return ops


def _guardrails_passed(run_store: RunStore, week: str) -> bool:
    report = run_store.load_eval_report(week)
    return bool(report and report.get("passed"))


async def publish_pulse(
    settings: Settings,
    run_store: RunStore,
    week: str,
    opts: PublishOptions,
    *,
    client: ToolCaller | None = None,
) -> PublishOutcome:
    """Publish the saved pulse for `week`. `client` is injectable for tests."""
    pulse = run_store.load_pulse(week)
    state = run_store.load_publish_state(week) or PublishState(week=week)
    if pulse is None or not _guardrails_passed(run_store, week):
        return PublishOutcome(
            "failed_validation",
            state,
            f"No guardrail-approved pulse for {week}. Run `pulse compose --week {week}` first.",
        )

    mode = effective_email_mode(settings, opts)

    if opts.dry_run:
        subject, _ = build_message(pulse, state.doc_url)
        return PublishOutcome(
            "dry_run",
            state,
            f"DRY RUN: no MCP calls. Email mode would be '{mode}'.\n"
            f"Subject: {subject}\n\n{render_plain(pulse)}",
        )

    problems = validate_publish_settings(settings, email_needed=opts.email and mode != "off")
    if problems:
        raise PublishError("Publish settings invalid:\n- " + "\n- ".join(problems))

    mcp: MCPPublishClient | None = None
    if client is None:
        entry = settings.mcp_config.servers[SERVER_NAME]
        mcp = MCPPublishClient(
            server_url(settings),
            settings.mcp_auth_token or "",
            entry.tool_map,
            settings.mcp_config.tool_allowlist,
        )
        try:
            await mcp.__aenter__()
        except ToolError as err:
            raise PublishError(err.message) from err
        client = mcp

    try:
        doc_known = bool(parse_doc_id(settings.pulse_doc_id) or run_store.load_doc_ref())
        missing = [op for op in _needed_ops(opts, mode, not doc_known) if not client.has(op)]
        if missing:
            raise PublishError(
                "MCP server is missing required tool(s) for this run: "
                f"{', '.join(missing)}. No fallback is attempted."
            )

        try:
            if opts.docs:
                docs = DocsPublisher(
                    client, run_store, settings.app_config.publish, settings.pulse_doc_id
                )
                state = await docs.append(
                    pulse,
                    state,
                    force_append=opts.force_append,
                    doc_confirmed=opts.doc_confirmed,
                )
            if opts.email and mode != "off":
                if state.doc_append.status != "done":
                    raise PublishError(
                        "Email is only drafted/sent after the Doc append succeeds "
                        f"(Doc append status: {state.doc_append.status})."
                    )
                emailer = EmailPublisher(
                    client,
                    run_store,
                    settings.pulse_recipient,
                    settings.app_config.publish.email_format,
                )
                state = await emailer.deliver(
                    pulse,
                    state,
                    mode,
                    resend=opts.resend,
                    email_confirmed=opts.email_confirmed,
                )
        except PublishError as exc:
            run_store.save_publish_state(state)
            return PublishOutcome("failed_publish", state, str(exc))
        return PublishOutcome("success", state, "Published.")
    finally:
        if mcp is not None:
            await mcp.__aexit__(None, None, None)
