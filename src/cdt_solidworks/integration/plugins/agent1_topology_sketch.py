"""Agent-1 plugin: bounded topology query, resolve, and geometry inspection."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Literal

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.topology.native import TopologyNativeAdapter

PLUGIN_CONTRACT_VERSION = 1
PLUGIN_ID = "agent1.topology-sketch"
PLUGIN_ORDER = 1

_TOPOLOGY_KINDS = {"body", "face", "edge", "vertex"}


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
    if native_code.startswith("timeout"):
        return "timeout"
    if native_code in {"topology_component_missing", "topology_reference_lost"}:
        return "not_found"
    if native_code.startswith("invalid_") or native_code.startswith("unsupported_") or native_code in {
        "topology_component_required", "topology_query_limit_exceeded"
    }:
        return "validation_error"
    if native_code.startswith("topology_reference_") or native_code == "stale_topology_reference":
        return "conflict"
    return "internal_error"


def _result_payload(result: NativeCallResult[Any]) -> dict[str, Any]:
    if result.state is NativeCallState.SUCCESS:
        return {
            "state": "success", "call_id": result.call_id, "dispatched": result.dispatched,
            "value": _jsonable(result.value), "error": None,
        }
    failure = result.failure
    state = (
        "uncertain" if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
        else "not_started" if result.state is NativeCallState.TIMEOUT_BEFORE_DISPATCH
        else "failed"
    )
    native_code = failure.code if failure is not None else "native_call_failed"
    return {
        "state": state, "call_id": result.call_id, "dispatched": result.dispatched, "value": None,
        "error": {
            "code": _error_code(native_code), "native_code": native_code,
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _context(
    session_id: str,
    path: str,
    title: str,
    document_type: str,
    configuration: str | None,
    update_stamp: int | None,
) -> DocumentContext:
    mapping = {"part": DocumentType.PART, "assembly": DocumentType.ASSEMBLY}
    normalized = document_type.strip().lower()
    if normalized not in mapping:
        raise ValueError("document_type must be part or assembly")
    return DocumentContext(
        session_id=session_id, path=path, title=title, document_type=mapping[normalized],
        configuration=configuration, update_stamp=update_stamp,
    )


def _service(runtime: Any) -> Any | None:
    service = getattr(runtime, "topology_service", None)
    if service is not None:
        return service
    session = getattr(runtime, "session", None)
    documents = getattr(runtime, "document_service", None)
    if session is None or documents is None or not hasattr(session, "api"):
        return None
    return TopologyNativeAdapter(session, document_service=documents)


def _require_service(runtime: Any) -> Any:
    service = _service(runtime)
    if service is None:
        raise RuntimeError("topology service is unavailable: session/document dependencies are not bound")
    return service


def _kinds(values: list[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized = tuple(str(value).strip().lower() for value in values)
    if not normalized or any(value not in _TOPOLOGY_KINDS for value in normalized):
        raise ValueError("kinds must contain only body, face, edge, or vertex")
    return normalized


def register_tools(server: Any, runtime: Any) -> None:
    """Register only Agent-1's three new public topology tools."""
    service = _service(runtime)
    if service is not None and getattr(runtime, "topology_service", None) is None:
        setattr(runtime, "topology_service", service)

    @server.tool(
        name="topology_query",
        description=(
            "Query bounded persistent body/face/edge/vertex references for an explicit document context; "
            "assembly queries require one component instance identity."
        ),
    )
    def topology_query(
        session_id: str,
        path: str,
        title: str,
        document_type: Literal["part", "assembly"],
        configuration: str | None = None,
        update_stamp: int | None = None,
        kinds: list[Literal["body", "face", "edge", "vertex"]] | None = None,
        component_id: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        service = _require_service(runtime)
        context = _context(session_id, path, title, document_type, configuration, update_stamp)
        return _result_payload(
            service.query(context, kinds=_kinds(kinds), component_id=component_id, timeout=timeout_s)
        )

    @server.tool(
        name="topology_resolve",
        description="Resolve one opaque swref1 topology reference without geometric substitution or guessing.",
    )
    def topology_resolve(
        reference: str,
        session_id: str,
        path: str,
        title: str,
        document_type: Literal["part", "assembly"],
        configuration: str | None = None,
        update_stamp: int | None = None,
        expected_kind: Literal["body", "face", "edge", "vertex"] | None = None,
        component_id: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        service = _require_service(runtime)
        context = _context(session_id, path, title, document_type, configuration, update_stamp)
        return _result_payload(
            service.resolve(
                context, reference, expected_kind=expected_kind,
                component_id=component_id, timeout=timeout_s,
            )
        )

    @server.tool(
        name="topology_inspect",
        description=(
            "Inspect typed finite geometry for one opaque topology reference with explicit millimeter units; "
            "unsupported measurements remain null rather than guessed."
        ),
    )
    def topology_inspect(
        reference: str,
        session_id: str,
        path: str,
        title: str,
        document_type: Literal["part", "assembly"],
        configuration: str | None = None,
        update_stamp: int | None = None,
        expected_kind: Literal["body", "face", "edge", "vertex"] | None = None,
        component_id: str | None = None,
        timeout_s: float = 15.0,
    ) -> dict[str, Any]:
        service = _require_service(runtime)
        context = _context(session_id, path, title, document_type, configuration, update_stamp)
        return _result_payload(
            service.inspect(
                context, reference, expected_kind=expected_kind,
                component_id=component_id, timeout=timeout_s,
            )
        )


def capability_descriptors(runtime: Any) -> tuple[dict[str, Any], ...]:
    """Describe Agent-1 service availability without implying native acceptance."""
    available = _service(runtime) is not None
    reason = None if available else "requires bound SolidWorks session and document service"
    dependencies = ("solidworks", "session", "document_service")
    return tuple(
        {
            "name": name,
            "implemented": True,
            "available": available,
            "reason": reason,
            "backend": "solidworks_native",
            "dependencies": dependencies,
        }
        for name in ("topology.query", "topology.resolve", "topology.inspect")
    )
