"""In-process fake of our MCP server (same four tools and result envelopes). No network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pulse.config import MCPRegistryConfig, MCPServerEntry, Settings
from pulse.publish.mcp_client import ToolError, ToolResult

TOOL_MAP = {
    "docs_create": "google_docs_create",
    "docs_append": "google_docs_append_text",
    "email_draft": "gmail_create_draft",
    "email_send": "gmail_send_email",
}
DOC_ID = "1EvalSandboxDocIdAbcdefghijklmn"
RECIPIENT = "owner@example.com"


class FakeMCP:
    def __init__(self, tools: set[str] | None = None) -> None:
        self.tools = set(TOOL_MAP) if tools is None else tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def has(self, op: str) -> bool:
        return op in self.tools

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    async def call(self, op: str, arguments: dict[str, Any]) -> ToolResult:
        if op not in TOOL_MAP.keys() & self.tools:
            raise ToolError("INVALID_ARGUMENT", f"unknown tool {op}")
        self.calls.append((op, arguments))
        n = len(self.calls)
        data: dict[str, Any] = {"success": True, "message": "ok", "request_id": f"req{n}"}
        if op == "docs_create":
            data.update(
                document_id=DOC_ID,
                document_url=f"https://docs.google.com/document/d/{DOC_ID}/edit",
            )
        elif op == "email_draft":
            data.update(draft_id="draft1", message_id="msg1")
        elif op == "email_send":
            data.update(message_id="msg2", thread_id="thr2")
        return ToolResult(data=data, request_id=f"req{n}", tool=TOOL_MAP[op])


def make_settings(data_dir: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "MCP_AUTH_TOKEN": "e" * 40,
        "PULSE_RECIPIENT": RECIPIENT,
        "PULSE_DATA_DIR": str(data_dir),
        **overrides,
    }
    s = Settings(_env_file=None, **values)  # type: ignore[call-arg]
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
