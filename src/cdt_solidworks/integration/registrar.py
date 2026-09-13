"""MCP registrar for the integrated application/document native surface."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Literal

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.native.session import AttachPolicy
from cdt_solidworks.platform.errors import ErrorCode


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
        return ErrorCode.TIMEOUT.value
    if native_code in {"document_not_found", "document_not_open"}:
        return ErrorCode.NOT_FOUND.value
    if native_code in {"stale_document_context", "document_context_mismatch", "uncertain_state"}:
        return ErrorCode.CONFLICT.value
    if native_code.startswith("path_") or native_code in {
        "document_type_mismatch",
        "document_extension_mismatch",
        "document_type_unknown",
    }:
        return ErrorCode.VALIDATION_ERROR.value
    if native_code.startswith("solidworks_") or native_code == "session_not_connected":
        return ErrorCode.PROVIDER_UNAVAILABLE.value
    return ErrorCode.INTERNAL_ERROR.value


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
    return {
        "state": state,
        "call_id": result.call_id,
        "dispatched": result.dispatched,
        "value": None,
        "error": {
            "code": _error_code(failure.code if failure is not None else "native_call_failed"),
            "native_code": failure.code if failure is not None else "native_call_failed",
            "message": failure.message if failure is not None else "Native operation failed.",
            "retryable": failure.retryable if failure is not None else False,
        },
    }


def _document_type(value: str) -> DocumentType:
    normalized = value.strip().lower()
    mapping = {
        "part": DocumentType.PART,
        "assembly": DocumentType.ASSEMBLY,
        "drawing": DocumentType.DRAWING,
    }
    if normalized not in mapping:
        raise ValueError("document_type must be part, assembly, or drawing")
    return mapping[normalized]


def _context(
    session_id: str,
    path: str,
    title: str,
    document_type: Literal["part", "assembly", "drawing"],
    configuration: str | None,
    update_stamp: int | None,
) -> DocumentContext:
    return DocumentContext(
        session_id=session_id,
        path=path,
        title=title,
        document_type=_document_type(document_type),
        configuration=configuration,
        update_stamp=update_stamp,
    )


def register_runtime_tools(server: Any, runtime: Any) -> None:
    """Register only the native/document tools backed by the accepted B-lane runtime."""

    @server.tool(name="application_probe", description="Probe SolidWorks registration/running state without mutation.")
    def application_probe(version: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.session.probe(version=version, timeout=3.0))

    @server.tool(name="application_connect", description="Attach to or start an explicit SolidWorks application session.")
    def application_connect(
        policy: Literal["attach_only", "attach_or_start", "start_new"] = "attach_or_start",
        version: int | None = None,
        visible: bool = True,
    ) -> dict[str, Any]:
        try:
            attach_policy = AttachPolicy(policy)
        except ValueError:
            return {
                "state": "not_started",
                "call_id": "validation",
                "dispatched": False,
                "value": None,
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "native_code": "invalid_attach_policy",
                    "message": "policy must be attach_only, attach_or_start, or start_new",
                    "retryable": False,
                },
            }
        return _result_payload(
            runtime.session.connect(policy=attach_policy, version=version, visible=visible)
        )

    @server.tool(name="application_disconnect", description="Disconnect the provider session; only provider-owned applications may be exited.")
    def application_disconnect() -> dict[str, Any]:
        return _result_payload(runtime.session.disconnect())

    @server.tool(name="document_open", description="Open a SolidWorks document within configured path roots and return explicit identity context.")
    def document_open(
        path: str,
        expected_type: Literal["part", "assembly", "drawing"] | None = None,
        configuration: str = "",
        read_only: bool = False,
    ) -> dict[str, Any]:
        doc_type = _document_type(expected_type) if expected_type is not None else None
        return _result_payload(
            runtime.document_service.open(
                path,
                expected_type=doc_type,
                configuration=configuration,
                read_only=read_only,
            )
        )

    def ctx(
        session_id: str,
        path: str,
        title: str,
        document_type: Literal["part", "assembly", "drawing"],
        configuration: str | None,
        update_stamp: int | None,
    ) -> DocumentContext:
        return _context(session_id, path, title, document_type, configuration, update_stamp)

    @server.tool(name="document_info", description="Read document information using explicit identity/revision context.")
    def document_info(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.info(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_save", description="Save an explicitly identified document and verify postconditions.")
    def document_save(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.save(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_save_as", description="Save-as within configured path roots and verify native identity moved to the target.")
    def document_save_as(target_path: str, session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.save_as(ctx(session_id, path, title, document_type, configuration, update_stamp), target_path))

    @server.tool(name="document_close", description="Close an explicitly identified document and verify it is no longer open.")
    def document_close(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.close(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_reopen", description="Close/reopen an explicit document and return refreshed identity context.")
    def document_reopen(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.reopen(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_list_features", description="List bounded feature state for an explicit document.")
    def document_list_features(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_features(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_list_bodies", description="List bounded part bodies for an explicit document.")
    def document_list_bodies(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None, visible_only: bool = False) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_bodies(ctx(session_id, path, title, document_type, configuration, update_stamp), visible_only=visible_only))

    @server.tool(name="document_list_components", description="List bounded assembly components for an explicit document.")
    def document_list_components(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None, top_level_only: bool = False) -> dict[str, Any]:
        return _result_payload(runtime.document_service.list_components(ctx(session_id, path, title, document_type, configuration, update_stamp), top_level_only=top_level_only))

    @server.tool(name="document_rebuild", description="Rebuild and reject success when SolidWorks feature/error state is not clean.")
    def document_rebuild(session_id: str, path: str, title: str, document_type: Literal["part", "assembly", "drawing"], configuration: str | None = None, update_stamp: int | None = None) -> dict[str, Any]:
        return _result_payload(runtime.document_service.rebuild(ctx(session_id, path, title, document_type, configuration, update_stamp)))

    @server.tool(name="document_reconcile", description="Reconcile an uncertain document mutation before dependent writes continue.")
    def document_reconcile(call_id: str, path: str, expected_type: Literal["part", "assembly", "drawing"], should_be_open: bool) -> dict[str, Any]:
        return _result_payload(runtime.document_service.reconcile_document_state(call_id, path=path, expected_type=_document_type(expected_type), should_be_open=should_be_open))
