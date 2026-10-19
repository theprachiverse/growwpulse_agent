"""Pre-flight validation of publish settings. Runs before any MCP call."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pulse.config import Settings

_EMAIL_RE = re.compile(r"^[^\s<>@,;\"']+@[^\s<>@,;\"']+\.[^\s<>@,;\"']+$")
_DOC_URL_RE = re.compile(r"docs\.google\.com/document/d/([A-Za-z0-9_-]+)")
_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")

MIN_TOKEN_CHARS = 32
SERVER_NAME = "google_workspace"


def parse_doc_id(value: str | None) -> str | None:
    """Accepts a bare document ID or a docs.google.com/document/d/<id> URL."""
    if not value or not value.strip():
        return None
    value = value.strip()
    m = _DOC_URL_RE.search(value)
    if m:
        return m.group(1)
    if _DOC_ID_RE.match(value):
        return value
    raise ValueError("PULSE_DOC_ID is neither a Google Doc ID nor a docs.google.com document URL.")


def server_url(settings: Settings) -> str:
    entry = settings.mcp_config.servers.get(SERVER_NAME)
    return (settings.mcp_server_url or (entry.url if entry else "") or "").strip()


def validate_publish_settings(settings: Settings, *, email_needed: bool = True) -> list[str]:
    """Returns a list of human-readable problems (empty list = OK)."""
    problems: list[str] = []
    entry = settings.mcp_config.servers.get(SERVER_NAME)
    if entry is None:
        problems.append(f"config/mcp_servers.yaml has no '{SERVER_NAME}' server.")
    url = server_url(settings)
    if urlparse(url).scheme != "https" or not urlparse(url).netloc:
        problems.append("MCP server URL must be https (set MCP_SERVER_URL or servers.*.url).")
    token = settings.mcp_auth_token or ""
    if len(token) < MIN_TOKEN_CHARS:
        problems.append(f"MCP_AUTH_TOKEN is missing or shorter than {MIN_TOKEN_CHARS} characters.")
    if email_needed and not _EMAIL_RE.match(settings.pulse_recipient.strip()):
        problems.append(
            "PULSE_RECIPIENT must be a plain address like name@example.com "
            "(no display name, no <>, one address)."
        )
    try:
        doc_id = parse_doc_id(settings.pulse_doc_id)
    except ValueError as exc:
        problems.append(str(exc))
        doc_id = None
    if doc_id is None and not settings.app_config.publish.auto_create_doc:
        problems.append("Set PULSE_DOC_ID, or enable publish.auto_create_doc.")
    return problems
