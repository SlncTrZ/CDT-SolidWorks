"""Agent 4 drawing/BOM interoperability plugin.

Wing: code | Topic: drawing-interop | Updated: 2026-09-15 11:00
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from cdt_solidworks.native.models import NativeCallResult, NativeCallState


PLUGIN_CONTRACT_VERSION = 1
PLUGIN_ID = "agent4.drawing_interop"
PLUGIN_ORDER = 4


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return str(value)


def _error_code(native_code: str) -> str:
    if (
        native_code.startswith("dangling_")
        or native_code.endswith("_readback_missing")
        or native_code.endswith("_readback_mismatch")
    ):
        return "conflict"
    if native_code.startswith("invalid_") or native_code.startswith("unsupported_"):
        return "validation_error"
    if native_code.startswith("path_") or native_code.endswith("_mismatch"):
        return "validation_error"
    if native_code in {
        "bom_not_found",
        "drawing_views_missing",
        "topology_reference_unavailable",
        "stale_topology_reference",
    }:
        return "conflict"
    if native_code.startswith("solidworks_") or native_code == "session_not_connected":
        return "provider_unavailable"
    return "internal_error"


def _result_payload(result: NativeCallResult[Any]) -> dict[str, Any]:
    if result.state is NativeCallState.SUCCESS:
        return {
            "state": "success",
            "call_id": result.call_id,
            "dispatched": result.dispatched,
            "value": _jsonable(result.value),
            "error": None,
        }
    failure = result.failure
    state = (
        "uncertain"
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        else "not_started"
        if result.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
        else "failed"
    )
    native_code = failure.code if failure is not None else "native_call_failed"
    return {
        "state": state,
        "call_id": result.call_id,
        "dispatched": result.dispatched,
        "value": None,
        "error": {
            "code": _error_code(native_code),
            "native_code": native_code,
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _bind_topology(runtime: Any) -> None:
    drawing = getattr(runtime, "drawing_service", None)
    topology = getattr(runtime, "topology_service", None)
    binder = getattr(drawing, "bind_topology_service", None) if drawing is not None else None
    if callable(binder):
        binder(topology)


def register_tools(server: Any, runtime: Any) -> None:
    """Register Agent-4-only public additions without editing the shared registrar."""
    _bind_topology(runtime)
    drawing = getattr(runtime, "drawing_service", None)
    if drawing is None:
        return

    @server.tool(
        name="drawing_dimension_create",
        description=(
            "Create one source-linked drawing dimension from an opaque swref1 topology reference; "
            "source document/configuration identity is checked before mutation."
        ),
    )
    def drawing_dimension_create(
        path: str,
        view_id: str,
        source_model_path: str,
        source_ref: str,
        x_mm: float,
        y_mm: float,
        source_configuration: str | None = None,
    ) -> dict[str, Any]:
        return _result_payload(
            drawing.create_dimension(
                path,
                view_id,
                source_model_path,
                source_configuration,
                source_ref,
                x_mm,
                y_mm,
            )
        )

    @server.tool(
        name="drawing_dimensions_list",
        description="List bounded drawing dimensions and fail closed on dangling/readback-invalid state.",
    )
    def drawing_dimensions_list(
        path: str, view_id: str | None = None
    ) -> dict[str, Any]:
        return _result_payload(drawing.list_dimensions(path, view_id))

    @server.tool(
        name="drawing_bom_create",
        description=(
            "Create a bounded assembly BOM bound to an explicit assembly/configuration and verify table readback."
        ),
    )
    def drawing_bom_create(
        path: str,
        view_id: str,
        source_assembly_path: str,
        source_configuration: str,
    ) -> dict[str, Any]:
        return _result_payload(
            drawing.create_bom(
                path, view_id, source_assembly_path, source_configuration
            )
        )

    @server.tool(
        name="drawing_bom_read",
        description="Read one bounded BOM table by stable table identity, including row/column contents.",
    )
    def drawing_bom_read(path: str, bom_id: str) -> dict[str, Any]:
        return _result_payload(drawing.read_bom(path, bom_id))

    @server.tool(
        name="drawing_update",
        description=(
            "Rebuild a source-bound drawing and verify views, dimensions and BOM tables are current and non-dangling."
        ),
    )
    def drawing_update(
        path: str,
        source_model_path: str,
        source_configuration: str | None = None,
    ) -> dict[str, Any]:
        return _result_payload(
            drawing.update(path, source_model_path, source_configuration)
        )


def capability_descriptors(runtime: Any) -> tuple[dict[str, Any], ...]:
    """Return strict capability mappings consumed by Agent 6 composition."""
    _bind_topology(runtime)
    drawing = getattr(runtime, "drawing_service", None)
    service_available = drawing is not None
    dimension_available = service_available and bool(
        getattr(drawing, "dimension_available", False)
    )
    bom_available = service_available and bool(getattr(drawing, "bom_available", False))
    return (
        {
            "name": "solidworks.drawing.dimension",
            "implemented": True,
            "available": dimension_available,
            "reason": None if dimension_available else "topology reference selector is unavailable",
            "backend": "solidworks_com",
            "dependencies": ("solidworks", "topology.resolve"),
        },
        {
            "name": "solidworks.drawing.bom",
            "implemented": True,
            "available": bom_available,
            "reason": None if bom_available else "BOM template dependency is unavailable",
            "backend": "solidworks_com",
            "dependencies": ("solidworks", "bom_template"),
        },
        {
            "name": "solidworks.drawing.update_propagation",
            "implemented": True,
            "available": service_available,
            "reason": None if service_available else "drawing service is unavailable",
            "backend": "solidworks_com",
            "dependencies": ("solidworks",),
        },
    )
