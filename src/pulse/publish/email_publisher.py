"""Email delivery through MCP: Gmail draft (default) or send. Always to one recipient."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pulse.models import PublishState, Pulse
from pulse.observability.logging import get_logger
from pulse.publish.errors import PublishError, from_tool_error
from pulse.publish.html_email import render_html
from pulse.publish.mcp_client import ToolCaller, ToolError
from pulse.publish.plaintext import render_plain
from pulse.run_store import RunStore

logger = get_logger("pulse.publish.email")

MAX_SUBJECT_CHARS = 500
MAX_BODY_BYTES = 1_000_000

EmailMode = Literal["draft", "send", "off"]
EmailFormat = Literal["html", "text"]


def build_email(
    pulse: Pulse, doc_url: str | None, fmt: EmailFormat = "html"
) -> tuple[str, str, str]:
    """Returns (subject, body, body_type) for the requested format."""
    subject = f"Groww Weekly Review Pulse — {pulse.week}"
    if fmt == "html":
        body, body_type = render_html(pulse, doc_url), "html"
    else:
        parts = [render_plain(pulse)]
        if doc_url:
            parts += ["", f"Full history (Google Doc): {doc_url}"]
        body, body_type = "\n".join(parts), "text"
    if "\n" in subject or len(subject) > MAX_SUBJECT_CHARS:
        raise PublishError("Email subject must be a single line of at most 500 characters.")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise PublishError("Email body exceeds 1 MB.")
    return subject, body, body_type


def build_message(pulse: Pulse, doc_url: str | None) -> tuple[str, str]:
    """Plain-text (subject, body); used for previews and the PII output scan."""
    subject, body, _ = build_email(pulse, doc_url, "text")
    return subject, body


class EmailPublisher:
    def __init__(
        self,
        client: ToolCaller,
        run_store: RunStore,
        recipient: str,
        email_format: EmailFormat = "html",
    ) -> None:
        self._client = client
        self._store = run_store
        self._recipient = recipient.strip()
        self._format = email_format

    async def deliver(
        self,
        pulse: Pulse,
        state: PublishState,
        mode: EmailMode,
        *,
        resend: bool = False,
        email_confirmed: bool = False,
    ) -> PublishState:
        rec = state.email
        if mode == "off":
            return state

        if rec.status == "done" and not resend:
            logger.info("email_skipped_already_done", week=pulse.week, mode=rec.mode)
            return state
        if rec.status == "unknown" and not resend:
            if email_confirmed:
                rec.status, rec.at = "done", datetime.now(UTC)
                self._store.save_publish_state(state)
                return state
            raise PublishError(
                "The last email action had an unknown outcome. Check Drafts/Sent in Gmail, then "
                "re-run with --email-confirmed (it is there) or --resend (do it again)."
            )

        subject, body, body_type = build_email(pulse, state.doc_url, self._format)
        # Exactly one recipient; cc/bcc are never set.
        args = {"to": self._recipient, "subject": subject, "body": body, "body_type": body_type}
        op = "email_draft" if mode == "draft" else "email_send"
        try:
            res = await self._client.call(op, args)
        except ToolError as err:
            rec.status = "unknown" if err.is_unknown_outcome else "failed"
            rec.mode, rec.request_id, rec.error = mode, err.request_id, err.code
            rec.at = datetime.now(UTC)
            self._store.save_publish_state(state)
            raise from_tool_error("Email delivery", err) from err

        rec.status, rec.mode, rec.request_id = "done", mode, res.request_id
        rec.draft_id = res.data.get("draft_id")
        rec.message_id = res.data.get("message_id")
        rec.thread_id = res.data.get("thread_id")
        rec.at, rec.error = datetime.now(UTC), None
        self._store.save_publish_state(state)
        return state
