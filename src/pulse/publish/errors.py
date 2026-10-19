"""Publish-level errors (map to RunManifest status `failed_publish`)."""

from __future__ import annotations

from pulse.publish.mcp_client import AUTH_GUIDANCE, ToolError


class PublishError(Exception):
    """A publish step could not complete; the message tells the operator what to do."""


def from_tool_error(step: str, err: ToolError) -> PublishError:
    if err.is_auth:
        return PublishError(f"{step} failed ({err.code}). {AUTH_GUIDANCE}")
    if err.code == "RESOURCE_NOT_FOUND":
        return PublishError(
            f"{step} failed: the Google Doc was not found or is not visible to the server's "
            "account. Check PULSE_DOC_ID (or delete data/state/publish_state.json to create a new Doc)."
        )
    return PublishError(f"{step} failed ({err.code}): {err.message} [request_id={err.request_id}]")
