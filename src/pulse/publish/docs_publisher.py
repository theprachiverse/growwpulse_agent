"""Google Docs publishing: create the Doc once, then append one dated section per week."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pulse.config import PublishConfig
from pulse.models import PublishState, Pulse
from pulse.observability.logging import get_logger
from pulse.publish.errors import PublishError, from_tool_error
from pulse.publish.mcp_client import ToolCaller, ToolError
from pulse.publish.plaintext import render_plain
from pulse.publish.validate import parse_doc_id
from pulse.run_store import RunStore

logger = get_logger("pulse.publish.docs")


def doc_url_for(doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit"


class DocsPublisher:
    def __init__(
        self,
        client: ToolCaller,
        run_store: RunStore,
        cfg: PublishConfig,
        doc_id_override: str | None = None,
    ) -> None:
        self._client = client
        self._store = run_store
        self._cfg = cfg
        self._override = parse_doc_id(doc_id_override)

    # ---- Doc resolution -------------------------------------------------
    async def ensure_doc(self) -> tuple[str, str]:
        """Returns (doc_id, doc_url): override -> remembered ref -> create once."""
        if self._override:
            return self._override, doc_url_for(self._override)

        ref = self._store.load_doc_ref()
        if ref:
            if ref.get("status") == "unknown":
                raise PublishError(
                    "A previous Doc creation had an unknown outcome. Look in Google Drive for "
                    f"'{self._cfg.doc_title}', set PULSE_DOC_ID to its ID (or delete "
                    "data/state/publish_state.json to allow a new Doc), then re-run."
                )
            if ref.get("doc_id"):
                return ref["doc_id"], ref.get("doc_url") or doc_url_for(ref["doc_id"])

        if not self._cfg.auto_create_doc:
            raise PublishError(
                "No Doc configured: set PULSE_DOC_ID or enable publish.auto_create_doc."
            )

        try:
            res = await self._client.call("docs_create", {"title": self._cfg.doc_title})
        except ToolError as err:
            if err.is_unknown_outcome:
                self._store.save_doc_ref({"status": "unknown", "request_id": err.request_id})
            raise from_tool_error("Creating the Google Doc", err) from err

        doc_id = str(res.data["document_id"])
        doc_url = str(res.data.get("document_url") or doc_url_for(doc_id))
        # Persist before any append so a later failure can never cause a second Doc.
        self._store.save_doc_ref(
            {
                "status": "done",
                "doc_id": doc_id,
                "doc_url": doc_url,
                "request_id": res.request_id,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        logger.info("doc_created", request_id=res.request_id)
        return doc_id, doc_url

    # ---- Append ---------------------------------------------------------
    async def append(
        self,
        pulse: Pulse,
        state: PublishState,
        *,
        force_append: bool = False,
        doc_confirmed: bool = False,
    ) -> PublishState:
        text = render_plain(pulse)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rec = state.doc_append

        if rec.status == "unknown":
            if doc_confirmed:
                rec.status, rec.content_sha256, rec.at = "done", sha, datetime.now(UTC)
                self._store.save_publish_state(state)
                return state
            if not force_append:
                raise PublishError(
                    "The last Doc append had an unknown outcome. Check the Doc, then re-run with "
                    "--doc-confirmed (it is there) or --force-append (append again)."
                )
        elif rec.status == "done":
            if rec.content_sha256 == sha:
                logger.info("doc_append_skipped_already_done", week=pulse.week)
                return state
            if not force_append:
                raise PublishError(
                    f"{pulse.week} was already appended with different content. The server cannot "
                    "edit the Doc; re-run with --force-append to add a revised section."
                )

        doc_id, doc_url = await self.ensure_doc()
        state.doc_id, state.doc_url = doc_id, doc_url

        revised = rec.status in ("done", "unknown")
        heading = (
            f"Groww Weekly Review Pulse — {pulse.week} ({pulse.window_start} to {pulse.window_end})"
        )
        if revised:
            heading += f" (revised {datetime.now(UTC):%Y-%m-%d %H:%M} UTC)"

        try:
            res = await self._client.call(
                "docs_append",
                {
                    "document_id": doc_id,
                    "heading": heading,
                    "text": text,
                    "add_separator_newline": True,
                    "insert_timestamp": True,
                },
            )
        except ToolError as err:
            rec.status = "unknown" if err.is_unknown_outcome else "failed"
            rec.request_id, rec.error, rec.at = err.request_id, err.code, datetime.now(UTC)
            self._store.save_publish_state(state)
            raise from_tool_error("Appending to the Google Doc", err) from err

        rec.status, rec.request_id = "done", res.request_id
        rec.content_sha256, rec.at, rec.error = sha, datetime.now(UTC), None
        self._store.save_publish_state(state)
        return state
