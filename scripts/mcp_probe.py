"""Probe for the Google Workspace MCP server (Spike A, task 0.7 / task 5.0).

Checks GET /healthz, connects over Streamable HTTP with the bearer token, lists tools and
verifies that every tool in `tool_map` exists with the expected input parameters.
Makes no write calls.

Usage:
    uv run python scripts/mcp_probe.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pulse.config import get_settings
from pulse.publish.mcp_client import MCPPublishClient, ToolError, check_health
from pulse.publish.validate import SERVER_NAME, server_url, validate_publish_settings

EXPECTED_PARAMS = {
    "docs_create": {"title"},
    "docs_append": {"text"},
    "email_draft": {"to", "subject", "body"},
    "email_send": {"to", "subject", "body"},
}


async def main() -> int:
    settings = get_settings()
    entry = settings.mcp_config.servers.get(SERVER_NAME)
    print("Groww Review Pulse - MCP probe")
    if entry is None:
        print(f"[FAIL] no '{SERVER_NAME}' server in {settings.mcp_config_path}")
        return 1

    problems = validate_publish_settings(settings)
    for p in problems:
        print(f"[WARN] {p}")
    url = server_url(settings)
    print(f"Server: {url}")

    print(f"/healthz: {'OK' if await check_health(url) else 'UNREACHABLE'}")
    if not settings.mcp_auth_token:
        print("[FAIL] MCP_AUTH_TOKEN is not set; cannot open an MCP session.")
        return 1

    client = MCPPublishClient(
        url, settings.mcp_auth_token, entry.tool_map, settings.mcp_config.tool_allowlist
    )
    try:
        async with client:
            print(
                f"Server exposes {len(client.server_tools)} tool(s): {sorted(client.server_tools)}"
            )
            ok = True
            for op, tool in entry.tool_map.items():
                if not client.has(op):
                    print(f"  x {op} -> {tool}: NOT FOUND")
                    ok = False
                    continue
                props = set((client.server_tools[tool]["input_schema"] or {}).get("properties", {}))
                missing = EXPECTED_PARAMS.get(op, set()) - props
                if missing:
                    print(f"  x {op} -> {tool}: missing params {sorted(missing)}")
                    ok = False
                else:
                    print(f"  ok {op} -> {tool}: params {sorted(props)}")
            return 0 if ok else 1
    except ToolError as err:
        print(f"[FAIL] {err.message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
