"""MCP client for our deployed Google Workspace server (Streamable HTTP + Bearer auth).

The server owns Google OAuth; this module only ever sees the MCP bearer token. All tool
calls go through `MCPPublishClient.call`, which enforces the tool allow-list and maps
the server's result envelope into `ToolResult` / `ToolError`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from pulse.observability.logging import get_logger

logger = get_logger("pulse.publish.mcp_client")

# Errors that are safe to retry (nothing was applied server-side).
_RETRYABLE_CODES = {"RATE_LIMITED", "NETWORK_ERROR"}
_NO_RETRY_AUTH = {"AUTH_REQUIRED", "AUTH_EXPIRED", "PERMISSION_DENIED"}

AUTH_GUIDANCE = (
    "Google authorisation on the MCP server is missing or expired. On the server repo run "
    "`npm run auth`, update GOOGLE_TOKEN_B64 in Railway and REDEPLOY (a restart is not enough). "
    "While the OAuth consent screen is in Testing mode, tokens expire every 7 days."
)


class ToolError(Exception):
    """A failed MCP tool call, mapped from the server's error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id

    @property
    def is_auth(self) -> bool:
        return self.code in _NO_RETRY_AUTH

    @property
    def is_unknown_outcome(self) -> bool:
        return self.code == "OPERATION_OUTCOME_UNKNOWN"


@dataclass
class ToolResult:
    data: dict[str, Any]
    request_id: str | None = None
    message: str = ""
    tool: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ToolCaller(Protocol):
    """What the publishers need. The real client and the test fake both implement this."""

    def has(self, op: str) -> bool: ...

    async def call(self, op: str, arguments: dict[str, Any]) -> ToolResult: ...


def parse_envelope(structured: dict[str, Any] | None, text: str, tool: str) -> ToolResult:
    """Turn a server result (structuredContent, or the JSON line in the text) into a ToolResult."""
    data = structured
    if data is None:
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
    if not isinstance(data, dict) or "success" not in data:
        raise ToolError("INTERNAL_ERROR", f"Unrecognised response from {tool}.")
    request_id = data.get("request_id")
    if data.get("success"):
        return ToolResult(
            data=data,
            request_id=request_id,
            message=str(data.get("message", "")),
            tool=tool,
        )
    err = data.get("error") or {}
    raise ToolError(
        str(err.get("code", "INTERNAL_ERROR")),
        str(err.get("message", "Tool call failed.")),
        retryable=bool(err.get("retryable", False)),
        request_id=request_id,
    )


async def check_health(server_url: str, timeout: float = 30.0) -> bool:
    """GET /healthz (no credentials). `server_url` is the /mcp endpoint URL."""
    base = server_url.rsplit("/mcp", 1)[0].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base}/healthz")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class MCPPublishClient:
    """Async context manager wrapping one MCP session to the Google Workspace server."""

    def __init__(
        self,
        url: str,
        token: str,
        tool_map: dict[str, str],
        allowlist: list[str],
        *,
        connect_timeout: float = 60.0,
        call_timeout: float = 120.0,
        connect_attempts: int = 3,
        call_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._url = url
        self._token = token
        self._tool_map = dict(tool_map)
        self._allowlist = set(allowlist)
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout
        self._connect_attempts = connect_attempts
        self._call_attempts = call_attempts
        self._sleep = sleep
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self.server_tools: dict[str, dict[str, Any]] = {}

    # ---- lifecycle -------------------------------------------------------
    async def __aenter__(self) -> MCPPublishClient:
        last: Exception | None = None
        for attempt in range(1, self._connect_attempts + 1):
            try:
                await self._connect()
                return self
            except Exception as exc:  # connect failures only; tool calls are never retried here
                last = exc
                await self._close()
                logger.warning("mcp_connect_failed", attempt=attempt, error=type(exc).__name__)
                if attempt < self._connect_attempts:
                    await self._sleep(2.0 * attempt)
        raise ToolError(
            "NETWORK_ERROR",
            f"Could not connect to the MCP server after {self._connect_attempts} attempts "
            f"({type(last).__name__}). Check the server URL, MCP_AUTH_TOKEN and /healthz.",
            retryable=True,
        )

    async def __aexit__(self, *exc_info: object) -> None:
        await self._close()

    async def _connect(self) -> None:
        # Imported lazily so the rest of the package works without the MCP SDK internals.
        import httpx2
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        stack = AsyncExitStack()
        self._stack = stack
        http_client = await stack.enter_async_context(
            create_mcp_http_client(
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=httpx2.Timeout(self._call_timeout, connect=self._connect_timeout),
            )
        )
        streams = await stack.enter_async_context(
            streamable_http_client(self._url, http_client=http_client)
        )
        read_stream, write_stream = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await asyncio.wait_for(session.initialize(), timeout=self._connect_timeout)
        listed = await asyncio.wait_for(session.list_tools(), timeout=self._connect_timeout)
        self._session = session
        self.server_tools = {
            t.name: {"description": t.description, "input_schema": t.input_schema}
            for t in listed.tools
        }
        logger.info("mcp_connected", tools=sorted(self.server_tools))

    async def _close(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception as exc:  # pragma: no cover - best-effort teardown
                logger.debug("mcp_close_error", error=type(exc).__name__)

    # ---- capability checks ----------------------------------------------
    def has(self, op: str) -> bool:
        name = self._tool_map.get(op)
        return bool(name) and name in self.server_tools

    def require(self, ops: list[str]) -> None:
        """Fail fast, naming the missing tool(s)."""
        missing = [self._tool_map.get(op, op) for op in ops if not self.has(op)]
        if missing:
            raise ToolError(
                "INVALID_ARGUMENT",
                f"MCP server does not expose required tool(s): {', '.join(missing)}. "
                f"Available: {', '.join(sorted(self.server_tools)) or 'none'}.",
            )

    # ---- tool calls ------------------------------------------------------
    async def call(self, op: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tool_map.get(op)
        if tool is None or tool not in self._allowlist:
            raise ToolError("INVALID_ARGUMENT", f"Tool for '{op}' is not on the allow-list.")
        if self._session is None:
            raise ToolError("INTERNAL_ERROR", "MCP session is not open.")

        for attempt in range(1, self._call_attempts + 1):
            try:
                return await self._call_once(tool, arguments)
            except ToolError as err:
                if (
                    err.is_unknown_outcome
                    or err.is_auth
                    or not (err.retryable and err.code in _RETRYABLE_CODES)
                ):
                    raise
                if attempt == self._call_attempts:
                    raise
                logger.warning(
                    "mcp_call_retry", tool=tool, code=err.code, request_id=err.request_id
                )
                await self._sleep(min(2.0**attempt, 20.0))
        raise ToolError("INTERNAL_ERROR", "unreachable")  # pragma: no cover

    async def _call_once(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        from mcp import MCPError

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(tool, arguments), timeout=self._call_timeout
            )
        except MCPError as exc:
            # The server answered with a protocol error (e.g. schema validation): not applied.
            raise ToolError("INVALID_ARGUMENT", f"Server rejected '{tool}': {exc}") from exc
        except Exception as exc:
            # The request may have reached Google; for a non-idempotent write that is ambiguous.
            raise ToolError(
                "OPERATION_OUTCOME_UNKNOWN",
                f"No response from '{tool}' ({type(exc).__name__}). It may have been applied: "
                "check the Doc / Drafts / Sent folder before retrying.",
            ) from exc
        text = "\n".join(getattr(c, "text", "") for c in (getattr(result, "content", None) or []))
        structured = getattr(result, "structured_content", None)
        res = parse_envelope(structured, text, tool)  # raises ToolError on failure envelopes
        logger.info("mcp_call_ok", tool=tool, request_id=res.request_id)
        return res
