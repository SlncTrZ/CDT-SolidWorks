"""Wire-level request validation for provider-owned MCP tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.context import HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS


_TOOL_ARGUMENTS: dict[str, frozenset[str]] = {
    "help": frozenset(),
    "system_status": frozenset(),
    "system_capabilities": frozenset(),
    "application_probe": frozenset({"version"}),
    "application_connect": frozenset({"policy", "version", "visible"}),
    "application_disconnect": frozenset(),
    "document_open": frozenset({"path", "expected_type", "configuration", "read_only"}),
    "document_info": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_save": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_save_as": frozenset({"target_path", "session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_close": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_reopen": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_list_features": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_list_bodies": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp", "visible_only"}),
    "document_list_components": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp", "top_level_only"}),
    "document_rebuild": frozenset({"session_id", "path", "title", "document_type", "configuration", "update_stamp"}),
    "document_reconcile": frozenset({"call_id", "path", "expected_type", "should_be_open"}),
    "sketch_create_geometry": frozenset({"path", "expected_revision", "name", "plane", "entities"}),
    "sketch_get": frozenset({"path", "expected_revision", "sketch_id"}),
    "sketch_create_rectangle": frozenset({"output_path", "width_mm", "height_mm", "plane", "center_x_mm", "center_y_mm"}),
    "part_create_rect_extrude": frozenset({"output_path", "width_mm", "height_mm", "depth_mm", "plane", "center_x_mm", "center_y_mm"}),
    "part_add_rect_extrude": frozenset({"path", "width_mm", "height_mm", "depth_mm", "plane", "center_x_mm", "center_y_mm", "merge"}),
    "part_combine_all_bodies": frozenset({"path"}),
    "part_split_by_plane": frozenset({"path", "plane"}),
    "sheet_metal_create_base_flange": frozenset({"output_path", "width_mm", "height_mm", "thickness_mm", "bend_radius_mm"}),
    "surface_create_extrude": frozenset({"output_path", "line_length_mm", "depth_mm", "plane"}),
    "assembly_create": frozenset({"output_path", "component_paths", "placements_mm"}),
    "assembly_add_coincident_plane_mate": frozenset({"path", "component_name", "component_plane", "assembly_plane"}),
}



async def strict_platform_tool_inputs(
    ctx: ServerRequestContext[Any, Any],
    call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[HandlerResult]],
) -> HandlerResult:
    """Reject unknown arguments for the current provider-owned MCP tool surface.

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
    allowed = _TOOL_ARGUMENTS.get(str(tool_name))
    if allowed is not None:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise MCPError(
                code=INVALID_PARAMS,
                message="Invalid params: arguments must be an object.",
            )
        unexpected_keys = sorted(str(key) for key in arguments if str(key) not in allowed)
        if unexpected_keys:
            unexpected = ", ".join(unexpected_keys)
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Invalid params: unexpected field(s): {unexpected}",
            )

    return await call_next(ctx)
