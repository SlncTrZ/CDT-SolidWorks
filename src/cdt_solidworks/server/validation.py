"""Wire-level request validation for provider-owned MCP tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.context import HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS


_NO_ARGUMENT_TOOLS = frozenset({"help", "system_status", "system_capabilities"})


async def strict_platform_tool_inputs(
    ctx: ServerRequestContext[Any, Any],
    call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
) -> HandlerResult:
    """Reject unknown arguments for provider-owned zero-argument platform tools.

    MCP SDK argument models are permissive toward unknown keys by default. This
    pre-validation middleware makes the provider contract fail loud instead of
    silently discarding client mistakes.
    """

    if ctx.method != "tools/call":
        return await call_next(ctx)

    params = ctx.params
    if not isinstance(params, dict):
        return await call_next(ctx)

    tool_name = params.get("name")
    arguments = params.get("arguments")
    if tool_name in _NO_ARGUMENT_TOOLS and arguments not in (None, {}):
        if not isinstance(arguments, dict):
            raise MCPError(
                code=INVALID_PARAMS,
                message="Invalid params: arguments must be an empty object.",
            )
        unexpected = ", ".join(sorted(str(key) for key in arguments))
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"Invalid params: unexpected field(s): {unexpected}",
        )

    return await call_next(ctx)
