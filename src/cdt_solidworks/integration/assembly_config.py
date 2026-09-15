"""Evidence-gated integration wrappers for assembly and configuration native services."""

from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any, Callable, Mapping, Sequence, TypeVar
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
_COMPONENT_SOURCE_EXTENSIONS = frozenset({".sldprt", ".sldasm"})
_CONFIGURATION_EXTENSIONS = frozenset({".sldprt", ".sldasm"})
_PROMOTED_MATE_KINDS = frozenset(
    {
        MateKind.COINCIDENT,
        MateKind.CONCENTRIC,
        MateKind.DISTANCE,
        MateKind.ANGLE,
        MateKind.PARALLEL,
        MateKind.PERPENDICULAR,
        MateKind.TANGENT,
        MateKind.LOCK,
        MateKind.WIDTH,
        MateKind.SLOT,
    }
)
_PROMOTED_COMPONENT_STATES = {
    "resolved": ComponentLoadState.RESOLVED,
    "suppressed": ComponentLoadState.SUPPRESSED,
}
_CALL_ID_PATTERN = re.compile(r"(?:^|\s)call_id=([^;\s]+)")


def _local_failure(stage: str, message: str) -> NativeCallResult[Any]:
    return _typed_local_failure(stage, "cad_validation_error", message)


def _typed_local_failure(
    stage: str, code: str, message: str
) -> NativeCallResult[Any]:
    call_id = uuid.uuid4().hex
    return NativeCallResult.failed(
        NativeFailure(
            code=code,
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
        topology_service: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(path_policy=path_policy)
        if service is None:
            if session is None:
                raise ValueError("session is required when assembly service is not injected")
            service = AssemblyService(AssemblyNativeAdapter(session, timeout=timeout))
        self.service = service
        self.topology_service = topology_service
        self._topology_required = topology_service is not None

    def bind_topology_service(
        self, topology_service: Any | None, *, required: bool = True
    ) -> None:
        """Bind Agent-1's opaque topology port without importing its implementation."""
        self.topology_service = topology_service
        self._topology_required = bool(required)

    def list_components(self, path: str, *, recursive: bool = False) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_components_list",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=False,
            operation=lambda target: self.service.list_components(target, recursive=recursive),
        )

    def insert_component(
        self,
        path: str,
        source_path: str,
        configuration: str | None = None,
        transform: Sequence[float] | None = None,
    ) -> NativeCallResult[Any]:
        stage = "assembly_component_insert"
        try:
            source = self.path_policy.validate_open(source_path)
            if Path(source).suffix.lower() not in _COMPONENT_SOURCE_EXTENSIONS:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "Component source must be a SOLIDWORKS part or assembly.",
                )
        except Exception as exc:
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=uuid.uuid4().hex,
                dispatched=False,
            )
        normalized_transform: tuple[float, ...] | None = None
        if transform is not None:
            if isinstance(transform, (str, bytes)):
                return _local_failure(stage, "transform must contain exactly 16 finite numeric values.")
            values = tuple(transform)
            if len(values) != 16 or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in values
            ):
                return _local_failure(stage, "transform must contain exactly 16 finite numeric values.")
            normalized_transform = tuple(float(value) for value in values)
        return self._call(
            stage=stage,
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.insert_component(
                target,
                source,
                configuration,
                transform=normalized_transform,
            ),
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

    def delete_component(
        self, path: str, component_id: str
    ) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_component_delete",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.delete_component(target, component_id),
        )

    def replace_component(
        self,
        path: str,
        component_id: str,
        source_path: str,
        configuration: str | None = None,
    ) -> NativeCallResult[Any]:
        stage = "assembly_component_replace"
        try:
            source = self.path_policy.validate_open(source_path)
            if Path(source).suffix.lower() not in _COMPONENT_SOURCE_EXTENSIONS:
                raise NativeRuntimeError(
                    "document_type_mismatch",
                    stage,
                    "Replacement source must be a SOLIDWORKS part or assembly.",
                )
        except Exception as exc:
            call_id = uuid.uuid4().hex
            return NativeCallResult.failed(
                failure_from_exception(exc, stage),
                call_id=call_id,
                dispatched=False,
            )
        return self._call(
            stage=stage,
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.replace_component(
                target, component_id, source, configuration
            ),
        )

    def set_component_transform(
        self, path: str, component_id: str, transform: Sequence[float]
    ) -> NativeCallResult[Any]:
        if isinstance(transform, (str, bytes)):
            return _local_failure(
                "assembly_component_set_transform",
                "transform must contain exactly 16 finite numeric values.",
            )
        values = tuple(transform)
        if len(values) != 16 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            return _local_failure(
                "assembly_component_set_transform",
                "transform must contain exactly 16 finite numeric values.",
            )
        normalized = tuple(float(value) for value in values)
        return self._call(
            stage="assembly_component_set_transform",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.set_component_transform(
                target, component_id, normalized
            ),
        )

    def create_linear_component_pattern(
        self,
        path: str,
        *,
        seed_component_ids: Sequence[str],
        direction_ref: str,
        spacing_m: float,
        total_instances: int,
    ) -> NativeCallResult[Any]:
        if isinstance(seed_component_ids, (str, bytes)):
            return _local_failure(
                "assembly_component_pattern_create",
                "seed_component_ids must contain one or more explicit component identities.",
            )
        seeds = tuple(seed_component_ids)
        if not seeds or any(not isinstance(item, str) or not item.strip() for item in seeds):
            return _local_failure(
                "assembly_component_pattern_create",
                "seed_component_ids must contain one or more explicit component identities.",
            )
        if not isinstance(direction_ref, str) or not direction_ref.strip():
            return _local_failure(
                "assembly_component_pattern_create",
                "direction_ref must be a non-empty stable selection reference.",
            )
        if (
            isinstance(spacing_m, bool)
            or not isinstance(spacing_m, (int, float))
            or not math.isfinite(float(spacing_m))
            or float(spacing_m) <= 0
            or isinstance(total_instances, bool)
            or not isinstance(total_instances, int)
            or total_instances < 2
        ):
            return _local_failure(
                "assembly_component_pattern_create",
                "spacing_m must be positive and total_instances must be an integer >= 2.",
            )
        return self._call(
            stage="assembly_component_pattern_create",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.create_linear_component_pattern(
                target,
                seed_component_ids=seeds,
                direction_ref=direction_ref,
                spacing_m=float(spacing_m),
                total_instances=total_instances,
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
        constraint: str | None = None,
    ) -> NativeCallResult[Any]:
        try:
            mate_kind = MateKind(str(kind).strip().lower())
        except ValueError:
            return _local_failure(
                "assembly_mate_create",
                "kind is not a native-accepted common mate family.",
            )
        if mate_kind not in _PROMOTED_MATE_KINDS:
            return _local_failure(
                "assembly_mate_create",
                "This mate family is not native-accepted for the public tool surface.",
            )
        if isinstance(selection_refs, (str, bytes)):
            return _local_failure(
                "assembly_mate_create",
                "selection_refs must contain explicit stable selection references.",
            )
        refs = tuple(selection_refs)
        expected_count = 4 if mate_kind is MateKind.WIDTH else 2
        if len(refs) != expected_count or any(
            not isinstance(ref, str) or not ref.strip() for ref in refs
        ):
            return _local_failure(
                "assembly_mate_create",
                f"{mate_kind.value} requires exactly {expected_count} non-empty selection references.",
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
        alignment_kinds = {
            MateKind.COINCIDENT,
            MateKind.CONCENTRIC,
            MateKind.DISTANCE,
            MateKind.ANGLE,
            MateKind.PARALLEL,
            MateKind.TANGENT,
            MateKind.SLOT,
        }
        if alignment is not None:
            if alignment not in {"aligned", "anti_aligned", "closest"}:
                return _local_failure(
                    "assembly_mate_create",
                    "alignment must be aligned, anti_aligned, or closest.",
                )
            if mate_kind not in alignment_kinds:
                return _local_failure(
                    "assembly_mate_create",
                    f"{mate_kind.value} does not expose MateAlignment in the accepted native API.",
                )
        if mate_kind is MateKind.WIDTH:
            constraint = "centered" if constraint is None else constraint
            if constraint not in {"centered", "free"}:
                return _local_failure(
                    "assembly_mate_create",
                    "width constraint must be centered or free.",
                )
        elif mate_kind is MateKind.SLOT:
            constraint = "centered" if constraint is None else constraint
            if constraint not in {"centered", "free"}:
                return _local_failure(
                    "assembly_mate_create",
                    "slot constraint must be centered or free.",
                )
        elif constraint is not None:
            return _local_failure(
                "assembly_mate_create",
                "constraint is only valid for width or slot mates.",
            )
        resolved_entities: tuple[Any, ...] = ()
        resolved_component_ids: tuple[str | None, ...] = ()
        topology_resolved = False
        if self._topology_required:
            if any(not ref.startswith("swref1.") for ref in refs):
                return _local_failure(
                    "assembly_mate_create",
                    "topology-driven mates require opaque swref1 references.",
                )
            if self.topology_service is None:
                return _typed_local_failure(
                    "assembly_mate_create",
                    "topology_service_unavailable",
                    "topology.resolve is required for production mate creation.",
                )
            try:
                topology_target = self.path_policy.validate_open(path)
                if Path(topology_target).suffix.lower() not in _ASSEMBLY_EXTENSIONS:
                    raise NativeRuntimeError(
                        "document_type_mismatch",
                        "assembly_mate_create",
                        "Mate target must be a SOLIDWORKS assembly.",
                    )
                resolved = tuple(
                    self._resolve_topology_reference(topology_target, ref) for ref in refs
                )
            except (AssemblyRefusal, NativeRuntimeError) as exc:
                code = exc.reason if isinstance(exc, AssemblyRefusal) else exc.code
                return _typed_local_failure(
                    "assembly_mate_create", code, str(exc)
                )
            except Exception as exc:
                return _typed_local_failure(
                    "assembly_mate_create",
                    "topology_resolution_failed",
                    f"topology.resolve failed: {type(exc).__name__}: {exc}",
                )
            resolved_entities = tuple(item[0] for item in resolved)
            resolved_component_ids = tuple(item[1] for item in resolved)
            topology_resolved = True
        request = MateRequest(
            kind=mate_kind,
            selection_refs=refs,
            value=value,
            alignment=alignment,
            constraint=constraint,
            resolved_entities=resolved_entities,
            resolved_reference_component_ids=resolved_component_ids,
            topology_resolved=topology_resolved,
        )
        return self._call(
            stage="assembly_mate_create",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.add_mate(target, request),
        )

    def _resolve_topology_reference(
        self, document_id: str, reference: str
    ) -> tuple[Any, str | None]:
        resolver = getattr(self.topology_service, "resolve", None)
        if not callable(resolver):
            raise AssemblyRefusal("topology_service_unavailable")
        result = resolver(document_id, reference)
        if isinstance(result, NativeCallResult):
            if result.state is not NativeCallState.SUCCESS:
                failure = result.failure
                raise AssemblyRefusal(
                    failure.code if failure is not None else "topology_resolution_failed",
                    failure.message if failure is not None else result.state.value,
                )
            result = result.value
        elif isinstance(result, Mapping) and "state" in result:
            if result.get("state") != "success":
                error = result.get("error")
                if isinstance(error, Mapping):
                    raise AssemblyRefusal(
                        str(error.get("native_code") or error.get("code") or "topology_resolution_failed"),
                        str(error.get("message") or "topology resolution failed"),
                    )
                raise AssemblyRefusal("topology_resolution_failed")
            result = result.get("value")

        entity: Any = None
        component_id: str | None = None
        if isinstance(result, Mapping):
            entity = result.get("native_entity", result.get("entity", result.get("value")))
            raw_component = result.get(
                "component_id", result.get("reference_component_id")
            )
            component_id = None if raw_component is None else str(raw_component)
        elif isinstance(result, tuple) and len(result) == 2:
            entity, raw_component = result
            component_id = None if raw_component is None else str(raw_component)
        else:
            entity = getattr(
                result,
                "native_entity",
                getattr(result, "entity", result),
            )
            raw_component = getattr(
                result,
                "component_id",
                getattr(result, "reference_component_id", None),
            )
            component_id = None if raw_component is None else str(raw_component)
        if entity is None:
            raise AssemblyRefusal("topology_resolution_missing_entity", reference)
        if component_id is not None and not component_id.strip():
            raise AssemblyRefusal("invalid_topology_component_identity", reference)
        return entity, component_id

    def list_mates(self, path: str) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_mates_list",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=False,
            operation=lambda target: self.service.list_mates(target),
        )

    def read_mate(self, path: str, mate_id: str) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_mate_get",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=False,
            operation=lambda target: self.service.read_mate(target, mate_id),
        )

    def delete_mate(self, path: str, mate_id: str) -> NativeCallResult[Any]:
        return self._call(
            stage="assembly_mate_delete",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=lambda target: self.service.delete_mate(target, mate_id),
        )

    def set_mate_suppressed(
        self, path: str, mate_id: str, suppressed: bool
    ) -> NativeCallResult[Any]:
        def operation(target: str) -> Any:
            mate = self.service.read_mate(target, mate_id)
            if mate.kind not in _PROMOTED_MATE_KINDS:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_mate_set_suppressed",
                    "Only a native-accepted mate can be changed by this tool.",
                )
            return self.service.set_mate_suppressed(target, mate_id, suppressed)

        return self._call(
            stage="assembly_mate_set_suppressed",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=operation,
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
                    "Only a native-accepted coincident mate can be changed by this compatibility tool.",
                )
            return self.service.set_mate_suppressed(target, mate_id, suppressed)

        return self._call(
            stage="assembly_coincident_mate_set_suppressed",
            path=path,
            extensions=_ASSEMBLY_EXTENSIONS,
            mutation=True,
            operation=operation,
        )

    def set_mate_value(
        self, path: str, mate_id: str, value: float
    ) -> NativeCallResult[Any]:
        def operation(target: str) -> Any:
            mate = self.service.read_mate(target, mate_id)
            if mate.kind not in {MateKind.DISTANCE, MateKind.ANGLE}:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_mate_set_value",
                    "Only native-accepted distance or angle mates expose editable values.",
                )
            return self.service.set_mate_value(target, mate_id, value)

        return self._call(
            stage="assembly_mate_set_value",
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
                    "Only a native-accepted distance mate can be changed by this compatibility tool.",
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

    def set_component_suppressed(
        self,
        path: str,
        configuration: str,
        component_id: str,
        suppressed: bool,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_component_set_suppressed",
            path,
            lambda target: self.service.set_component_suppressed(
                target, configuration, component_id, suppressed
            ),
            mutation=True,
        )

    def set_component_configuration(
        self,
        path: str,
        configuration: str,
        component_id: str,
        referenced_configuration: str,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_component_set_configuration",
            path,
            lambda target: self.service.set_component_configuration(
                target,
                configuration,
                component_id,
                referenced_configuration,
            ),
            mutation=True,
        )

    def query_state(
        self,
        path: str,
        name: str,
        *,
        dimensions: Sequence[str] = (),
        properties: Sequence[str] = (),
        component_ids: Sequence[str] = (),
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_state_query",
            path,
            lambda target: self.service.query_state(
                target,
                name,
                dimensions=tuple(dimensions),
                properties=tuple(properties),
                component_ids=tuple(component_ids),
            ),
            mutation=False,
        )

    def set_material(
        self,
        path: str,
        configuration: str,
        database: str,
        material_name: str,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_set_material",
            path,
            lambda target: self.service.set_material(
                target, configuration, database, material_name
            ),
            mutation=True,
        )

    def list_display_states(
        self, path: str, configuration: str
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_display_states_list",
            path,
            lambda target: self.service.list_display_states(target, configuration),
            mutation=False,
        )

    def create_display_state(
        self, path: str, configuration: str, name: str
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_display_state_create",
            path,
            lambda target: self.service.create_display_state(target, configuration, name),
            mutation=True,
        )

    def rename_display_state(
        self,
        path: str,
        configuration: str,
        old_name: str,
        new_name: str,
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_display_state_rename",
            path,
            lambda target: self.service.rename_display_state(
                target, configuration, old_name, new_name
            ),
            mutation=True,
        )

    def delete_display_state(
        self, path: str, configuration: str, name: str
    ) -> NativeCallResult[Any]:
        return self._configuration_call(
            "configuration_display_state_delete",
            path,
            lambda target: self.service.delete_display_state(target, configuration, name),
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
