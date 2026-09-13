"""Evidence-gated integration wrappers for assembly and configuration native services."""

from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any, Callable, Sequence, TypeVar
import uuid

from cdt_solidworks.assembly.domain import (
    AssemblyPostconditionError,
    AssemblyRefusal,
    AssemblyService,
    ComponentLoadState,
    MateKind,
    MateRequest,
)
from cdt_solidworks.assembly.native import AssemblyNativeAdapter
from cdt_solidworks.configuration.domain import (
    ConfigurationPostconditionError,
    ConfigurationRefusal,
    ConfigurationService,
)
from cdt_solidworks.configuration.native import ConfigurationNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure


T = TypeVar("T")

_ASSEMBLY_EXTENSIONS = frozenset({".sldasm"})
_CONFIGURATION_EXTENSIONS = frozenset({".sldprt", ".sldasm"})
_PROMOTED_MATE_KINDS = frozenset(
    {
        MateKind.COINCIDENT,
        MateKind.PARALLEL,
        MateKind.PERPENDICULAR,
        MateKind.DISTANCE,
        MateKind.ANGLE,
    }
)
_PROMOTED_COMPONENT_STATES = {
    "resolved": ComponentLoadState.RESOLVED,
    "suppressed": ComponentLoadState.SUPPRESSED,
}
_CALL_ID_PATTERN = re.compile(r"(?:^|\s)call_id=([^;\s]+)")


def _local_failure(stage: str, message: str) -> NativeCallResult[Any]:
    call_id = uuid.uuid4().hex
    return NativeCallResult.failed(
        NativeFailure(
            code="cad_validation_error",
            stage=stage,
            message=message,
            retryable=False,
        ),
        call_id=call_id,
        dispatched=False,
    )


class _EvidenceGatedService:
    def __init__(self, *, path_policy: DocumentPathPolicy) -> None:
        self.path_policy = path_policy

    def _call(
        self,
        *,
        stage: str,
        path: str,
        extensions: frozenset[str],
        mutation: bool,
        operation: Callable[[str], T],
    ) -> NativeCallResult[T]:
        call_id = uuid.uuid4().hex
        try:
            target = self.path_policy.validate_open(path)
            if Path(target).suffix.lower() not in extensions:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "The requested native operation does not support this document type.",
                )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=False,
            )

        try:
            value = operation(target)
        except (AssemblyPostconditionError, ConfigurationPostconditionError) as exc:
            reason = exc.reason
            if reason == "native_state_uncertain":
                detail = exc.detail or "Native state is uncertain after dispatch."
                match = _CALL_ID_PATTERN.search(detail)
                native_call_id = match.group(1) if match is not None else call_id
                return NativeCallResult(
                    state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
                    call_id=native_call_id,
                    failure=NativeFailure(
                        code="native_state_uncertain",
                        stage=stage,
                        message=str(exc),
                        retryable=False,
                    ),
                    dispatched=True,
                )
            return NativeCallResult.failed(
                NativeFailure(
                    code=reason,
                    stage=stage,
                    message=str(exc),
                    retryable=False,
                ),
                call_id=call_id,
                dispatched=True,
            )
        except (AssemblyRefusal, ConfigurationRefusal) as exc:
            return NativeCallResult.failed(
                NativeFailure(
                    code=exc.reason,
                    stage=stage,
                    message=str(exc),
                    retryable=False,
                ),
                call_id=call_id,
                # The lane adapters currently normalize native dispatch metadata
                # into typed domain refusals. Conservatively report dispatch once
                # the call crossed the evidence-gated integration boundary.
                dispatched=True,
            )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=True,
            )
        return NativeCallResult.success(value, call_id=call_id, dispatched=True)


class IntegratedAssemblyService(_EvidenceGatedService):
    """Expose only Agent-C assembly behaviors with direct native acceptance evidence."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(path_policy=path_policy)
        if service is None:
            if session is None:
                raise ValueError("session is required when assembly service is not injected")
            service = AssemblyService(AssemblyNativeAdapter(session, timeout=timeout))
        self.service = service

    def list_components(self, path: str, *, recursive: bool = False) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_components_list",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=False,
            operation=lambda target: self.service.list_components(target, recursive=recursive),
        )

    def set_component_fixed(
        self, path: str, component_id: str, fixed: bool
    ) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_component_set_fixed",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.set_component_fixed(
                target, component_id, fixed
            ),
        )

    def set_component_load_state(
        self, path: str, component_id: str, state: str
    ) -> NativeCallResult[Any]:
        normalized = str(state).strip().lower()
        native_state = _PROMOTED_COMPONENT_STATES.get(normalized)
        if native_state is None:
            return _local_failure(
                "assembly_component_set_load_state",
                "state must be resolved or suppressed; lightweight is not native-accepted for this tool.",
            )
        return self._call(
            stage="assembly_component_set_load_state",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.set_component_load_state(
                target, component_id, native_state
            ),
        )

    def set_component_configuration(
        self, path: str, component_id: str, configuration: str
    ) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_component_set_configuration",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.set_component_configuration(
                target, component_id, configuration
            ),
        )

    def create_mate(
        self,
        path: str,
        *,
        kind: str,
        selection_refs: Sequence[str],
        value: float | None = None,
        alignment: str | None = None,
    ) -> NativeCallResult[Any]:
        try:
            mate_kind = MateKind(str(kind).strip().lower())
        except ValueError:
            return _local_failure(
                "assembly_mate_create",
                "kind must be coincident, parallel, perpendicular, distance, or angle.",
            )
        if mate_kind not in _PROMOTED_MATE_KINDS:
            return _local_failure(
                "assembly_mate_create",
                "This mate family is implemented but not native-accepted for the public tool surface.",
            )
        if isinstance(selection_refs, (str, bytes)):
            return _local_failure(
                "assembly_mate_create",
                "selection_refs must contain exactly two explicit selection references.",
            )
        refs = tuple(selection_refs)
        if len(refs) != 2 or any(
            not isinstance(ref, str) or not ref.strip() for ref in refs
        ):
            return _local_failure(
                "assembly_mate_create",
                "selection_refs must contain exactly two non-empty selection references.",
            )
        if mate_kind in {MateKind.DISTANCE, MateKind.ANGLE}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return _local_failure(
                    "assembly_mate_create",
                    "distance and angle mates require a finite numeric value.",
                )
            value = float(value)
            if not math.isfinite(value):
                return _local_failure(
                    "assembly_mate_create",
                    "distance and angle mates require a finite numeric value.",
                )
        elif value is not None:
            return _local_failure(
                "assembly_mate_create",
                "value is only valid for distance or angle mates.",
            )
        if alignment is not None:
            if alignment not in {"aligned", "anti_aligned", "closest"}:
                return _local_failure(
                    "assembly_mate_create",
                    "alignment must be aligned, anti_aligned, or closest.",
                )
            if mate_kind is MateKind.PERPENDICULAR:
                return _local_failure(
                    "assembly_mate_create",
                    "perpendicular mates do not expose MateAlignment in the accepted native API.",
                )
        request = MateRequest(
            kind=mate_kind,
            selection_refs=refs,
            value=value,
            alignment=alignment,
        )
        return self._call(
            stage="assembly_mate_create",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.add_mate(target, request),
        )

    def list_mates(self, path: str) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_mates_list",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=False,
            operation=lambda target: self.service.list_mates(target),
        )

    def set_coincident_mate_suppressed(
        self, path: str, mate_id: str, suppressed: bool
    ) -> NativeCallResult[Any]:
        def operation(target: str) -> Any:
            mate = self.service.read_mate(target, mate_id)
            if mate.kind is not MateKind.COINCIDENT:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_coincident_mate_set_suppressed",
                    "Only a native-accepted coincident mate can be changed by this tool.",
                )
            return self.service.set_mate_suppressed(target, mate_id, suppressed)

        return self._call(
            stage="assembly_coincident_mate_set_suppressed",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=operation,
        )

    def set_distance_mate_value(
        self, path: str, mate_id: str, value: float
    ) -> NativeCallResult[Any]:
        def operation(target: str) -> Any:
            mate = self.service.read_mate(target, mate_id)
            if mate.kind is not MateKind.DISTANCE:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_distance_mate_set_value",
                    "Only a native-accepted distance mate can be changed by this tool.",
                )
            return self.service.set_mate_value(target, mate_id, value)

        return self._call(
            stage="assembly_distance_mate_set_value",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=operation,
        )


class IntegratedConfigurationService(_EvidenceGatedService):
    """Expose evidence-backed configuration, property, and equation operations."""

    def __init__(
        self,
        session: Any | None = None,
        *,
        path_policy: DocumentPathPolicy,
        service: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(path_policy=path_policy)
        if service is None:
            if session is None:
                raise ValueError("session is required when configuration service is not injected")
            service = ConfigurationService(
                ConfigurationNativeAdapter(session, timeout=timeout)
            )
        self.service = service

    def _configuration_call(
        self,
        stage: str,
        path: str,
        operation: Callable[[str], T],
        *,
        mutation: bool,
    ) -> NativeCallResult[T]:
        return self._call(
            stage=stage,
            path=path,
            extensions=_CONFIGURATION_EXTENSIONS,
            mutation=mutation,
            operation=operation,
        )

    def list(self, path: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_list", path, self.service.list, mutation=False
        )

    def create(
        self, path: str, name: str, parent: str | None = None
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_create",
            path,
            lambda target: self.service.create(target, name, parent=parent),
            mutation=True,
        )

    def rename(self, path: str, old_name: str, new_name: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_rename",
            path,
            lambda target: self.service.rename(target, old_name, new_name),
            mutation=True,
        )

    def delete(self, path: str, name: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_delete",
            path,
            lambda target: self.service.delete(target, name),
            mutation=True,
        )

    def activate(self, path: str, name: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_activate",
            path,
            lambda target: self.service.activate(target, name),
            mutation=True,
        )

    def set_dimension(
        self,
        path: str,
        configuration: str,
        dimension_name: str,
        value: float,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_set_dimension",
            path,
            lambda target: self.service.set_dimension(
                target, configuration, dimension_name, value
            ),
            mutation=True,
        )

    def set_property(
        self,
        path: str,
        configuration: str | None,
        property_name: str,
        value: str,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_set_property",
            path,
            lambda target: self.service.set_property(
                target, configuration, property_name, value
            ),
            mutation=True,
        )

    def delete_property(
        self,
        path: str,
        configuration: str | None,
        property_name: str,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_delete_property",
            path,
            lambda target: self.service.delete_property(
                target, configuration, property_name
            ),
            mutation=True,
        )

    def set_feature_suppressed(
        self,
        path: str,
        configuration: str,
        feature_id: str,
        suppressed: bool,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_set_feature_suppressed",
            path,
            lambda target: self.service.set_feature_suppressed(
                target, configuration, feature_id, suppressed
            ),
            mutation=True,
        )

    def list_equations(self, path: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_equations_list",
            path,
            self.service.list_equations,
            mutation=False,
        )

    def add_equation(self, path: str, expression: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_equation_add",
            path,
            lambda target: self.service.add_equation(target, expression),
            mutation=True,
        )

    def set_equation(
        self, path: str, identity: str, expression: str
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_equation_set",
            path,
            lambda target: self.service.set_equation(target, identity, expression),
            mutation=True,
        )

    def delete_equation(self, path: str, identity: str) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_equation_delete",
            path,
            lambda target: self.service.delete_equation(target, identity),
            mutation=True,
        )
