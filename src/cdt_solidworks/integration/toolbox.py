"""Evidence-gated integration surface for the Agent-2 Toolbox workflow."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Any, Callable, TypeVar
import uuid

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure
from cdt_solidworks.toolbox.domain import (
    ToolboxPostconditionError,
    ToolboxRefusal,
    ToolboxService,
)
from cdt_solidworks.toolbox.native import ToolboxNativeAdapter


T = TypeVar("T")
_CALL_ID_PATTERN = re.compile(r"(?:^|\s)call_id=([^;\s]+)")
_COMPONENT_EXTENSIONS = frozenset({".sldprt"})


class IntegratedToolboxService:
    """Expose Toolbox discovery/copy while preserving provider call envelopes."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        assembly_service: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.path_policy = path_policy
        if service is None:
            if session is None:
                raise ValueError("session is required when toolbox service is not injected")
            service = ToolboxService(ToolboxNativeAdapter(session, timeout=timeout))
        self.service = service
        self.assembly_service = assembly_service

    def probe(self) -> NativeCallResult[Any]:
        return self._call("toolbox_probe", False, self.service.probe)

    def catalog_query(
        self,
        *,
        standard: str | None = None,
        family: str | None = None,
        size: str | None = None,
        limit: int = 25,
    ) -> NativeCallResult[Any]:
        return self._call(
            "toolbox_catalog_query",
            False,
            lambda: self.service.catalog_query(
                standard=standard, family=family, size=size, limit=limit
            ),
        )

    def resolve_component(
        self,
        *,
        standard: str,
        family: str,
        size: str,
        project_directory: str,
        filename: str | None = None,
    ) -> NativeCallResult[Any]:
        stage = "toolbox_component_resolve"
        try:
            project = Path(project_directory).expanduser()
            if not project.is_absolute():
                raise ToolboxRefusal("project_directory_not_absolute")
            placeholder = project / "_cdt_toolbox_path_check_.SLDPRT"
            canonical = Path(self.path_policy.validate_save(placeholder)).parent
        except ToolboxRefusal as exc:
            return self._failed(stage, exc.reason, str(exc), dispatched=False)
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        return self._call(
            stage,
            True,
            lambda: self.service.resolve_component(
                standard=standard,
                family=family,
                size=size,
                project_directory=str(canonical),
                filename=filename,
            ),
        )

    def component_properties(
        self, path: str, configuration: str
    ) -> NativeCallResult[Any]:
        stage = "toolbox_component_properties"
        try:
            target = self.path_policy.validate_open(path)
            if Path(target).suffix.lower() not in _COMPONENT_EXTENSIONS:
                raise ToolboxRefusal("document_type_mismatch")
        except ToolboxRefusal as exc:
            return self._failed(stage, exc.reason, str(exc), dispatched=False)
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        return self._call(
            stage,
            False,
            lambda: self.service.component_properties(target, configuration),
        )

    def insert_component(
        self,
        assembly_path: str,
        *,
        standard: str,
        family: str,
        size: str,
        project_directory: str,
        filename: str | None = None,
        transform: list[float] | tuple[float, ...] | None = None,
    ) -> NativeCallResult[Any]:
        if self.assembly_service is None or not hasattr(
            self.assembly_service, "insert_component"
        ):
            return self._failed(
                "toolbox_component_insert",
                "assembly_service_unavailable",
                "Assembly component insertion service is unavailable.",
                dispatched=False,
            )
        resolved = self.resolve_component(
            standard=standard,
            family=family,
            size=size,
            project_directory=project_directory,
            filename=filename,
        )
        if resolved.state is not NativeCallState.SUCCESS or resolved.value is None:
            return resolved
        item = resolved.value
        inserted = self.assembly_service.insert_component(
            assembly_path,
            item.project_path,
            item.configuration,
            transform,
        )
        if inserted.state is not NativeCallState.SUCCESS:
            return inserted
        return NativeCallResult.success(
            {
                "resolved_component": asdict(item),
                "assembly_component": inserted.value,
            },
            call_id=inserted.call_id,
            dispatched=inserted.dispatched,
        )

    def _call(
        self, stage: str, mutation: bool, operation: Callable[[], T]
    ) -> NativeCallResult[T]:
        call_id = uuid.uuid4().hex
        try:
            value = operation()
        except ToolboxPostconditionError as exc:
            if exc.reason == "native_state_uncertain":
                match = _CALL_ID_PATTERN.search(exc.detail or "")
                native_call_id = match.group(1) if match is not None else call_id
                return NativeCallResult(
                    state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                    call_id=native_call_id,
                    failure=NativeFailure(
                        code=exc.reason,
                        stage=stage,
                        message=str(exc),
                        retryable=False,
                    ),
                    dispatched=True,
                )
            return self._failed(stage, exc.reason, str(exc), dispatched=True)
        except ToolboxRefusal as exc:
            # Once the integration operation starts, real Toolbox services may
            # already have dispatched read-only COM probes/catalog reads.
            return self._failed(stage, exc.reason, str(exc), dispatched=True)
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=mutation,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)

    @staticmethod
    def _failed(
        stage: str, code: str, message: str, *, dispatched: bool
    ) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            NativeFailure(
                code=code,
                stage=stage,
                message=message,
                retryable=False,
            ),
            call_id=uuid.uuid4().hex,
            dispatched=dispatched,
        )
