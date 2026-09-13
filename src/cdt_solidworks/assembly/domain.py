"""Assembly domain contract for the Mechanical 90 Agent C lane.

The service owns bounded assembly correctness rules while native COM execution is
provided by an injected adapter. Durable identity never depends on remembered
ActiveDoc or selection state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Protocol, Sequence


class AssemblyRefusal(RuntimeError):
    """Typed refusal raised before a successful domain result can be claimed."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        message = reason if detail is None else f"{reason}: {detail}"
        super().__init__(message)


class AssemblyPostconditionError(RuntimeError):
    """Raised when native mutation completed but observable state is unacceptable."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        message = reason if detail is None else f"{reason}: {detail}"
        super().__init__(message)


class ComponentLoadState(str, Enum):
    RESOLVED = "resolved"
    LIGHTWEIGHT = "lightweight"
    SUPPRESSED = "suppressed"


class MateState(str, Enum):
    SOLVED = "solved"
    UNDER_DEFINED = "under_defined"
    OVER_DEFINED = "over_defined"
    DANGLING = "dangling"
    SUPPRESSED = "suppressed"
    UNKNOWN = "unknown"


class MateKind(str, Enum):
    """Bounded mate family exposed by this lane; no dynamic COM method surface."""

    COINCIDENT = "coincident"
    CONCENTRIC = "concentric"
    DISTANCE = "distance"
    ANGLE = "angle"
    PARALLEL = "parallel"
    PERPENDICULAR = "perpendicular"
    TANGENT = "tangent"
    LOCK = "lock"
    WIDTH = "width"
    SLOT = "slot"


_MATE_ALIGNMENT_KINDS = frozenset({
    MateKind.COINCIDENT,
    MateKind.CONCENTRIC,
    MateKind.DISTANCE,
    MateKind.ANGLE,
    MateKind.PARALLEL,
    MateKind.TANGENT,
    MateKind.SLOT,
})

@dataclass(frozen=True)
class RebuildReport:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentSnapshot:
    identity: str
    source_path: str
    configuration: str | None
    transform: tuple[float, ...]
    load_state: ComponentLoadState
    fixed: bool = False


@dataclass(frozen=True)
class MateRequest:
    kind: MateKind | str
    selection_refs: tuple[str, ...]
    value: float | None = None
    alignment: str | None = None


@dataclass(frozen=True)
class MateSnapshot:
    identity: str
    state: MateState
    component_ids: tuple[str, ...]
    degrees_of_freedom: int | None
    rebuild_errors: tuple[str, ...] = ()
    kind: MateKind | None = None
    value: float | None = None


class AssemblyAdapter(Protocol):
    def list_components(
        self, assembly_id: str, recursive: bool
    ) -> tuple[ComponentSnapshot, ...]: ...

    def read_component(self, assembly_id: str, component_id: str) -> ComponentSnapshot: ...

    def insert_component(
        self, assembly_id: str, source_path: str, configuration: str | None
    ) -> str: ...

    def replace_component(
        self,
        assembly_id: str,
        component_id: str,
        source_path: str,
        configuration: str | None,
    ) -> None: ...

    def delete_component(self, assembly_id: str, component_id: str) -> None: ...

    def set_component_transform(
        self, assembly_id: str, component_id: str, transform: tuple[float, ...]
    ) -> None: ...

    def set_component_load_state(
        self, assembly_id: str, component_id: str, state: ComponentLoadState
    ) -> None: ...

    def set_component_fixed(
        self, assembly_id: str, component_id: str, fixed: bool
    ) -> None: ...

    def set_component_configuration(
        self, assembly_id: str, component_id: str, configuration: str
    ) -> None: ...

    def add_mate(self, assembly_id: str, request: MateRequest) -> str: ...

    def list_mates(self, assembly_id: str) -> tuple[MateSnapshot, ...]: ...

    def read_mate(self, assembly_id: str, mate_id: str) -> MateSnapshot: ...

    def set_mate_suppressed(
        self, assembly_id: str, mate_id: str, suppressed: bool
    ) -> None: ...

    def set_mate_value(self, assembly_id: str, mate_id: str, value: float) -> None: ...

    def rebuild_assembly(self, assembly_id: str) -> RebuildReport: ...


class AssemblyService:
    """Enforces assembly identity, rebuild, and read-back invariants."""

    def __init__(self, adapter: AssemblyAdapter) -> None:
        self._adapter = adapter

    def list_components(
        self, assembly_id: str, *, recursive: bool = False
    ) -> tuple[ComponentSnapshot, ...]:
        self._require_identity("assembly_id", assembly_id)
        components = tuple(self._adapter.list_components(assembly_id, recursive))
        self._validate_unique_component_snapshots(components)
        return components

    def insert_component(
        self, assembly_id: str, source_path: str, configuration: str | None = None
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_path(source_path)
        if configuration is not None:
            self._require_identity("configuration", configuration)
        component_id = self._adapter.insert_component(
            assembly_id, source_path, configuration
        )
        self._require_identity("component_id", component_id)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.source_path != source_path:
            raise AssemblyPostconditionError(
                "component_source_readback_mismatch", component.source_path
            )
        if configuration is not None and component.configuration != configuration:
            raise AssemblyPostconditionError(
                "component_configuration_readback_mismatch",
                str(component.configuration),
            )
        return component

    def replace_component(
        self,
        assembly_id: str,
        component_id: str,
        source_path: str,
        configuration: str | None = None,
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        self._require_path(source_path)
        if configuration is not None:
            self._require_identity("configuration", configuration)
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.replace_component(
            assembly_id, component_id, source_path, configuration
        )
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.source_path != source_path:
            raise AssemblyPostconditionError(
                "component_source_readback_mismatch", component.source_path
            )
        if configuration is not None and component.configuration != configuration:
            raise AssemblyPostconditionError(
                "component_configuration_readback_mismatch",
                str(component.configuration),
            )
        return component

    def delete_component(self, assembly_id: str, component_id: str) -> None:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.delete_component(assembly_id, component_id)
        self._require_rebuild(assembly_id)
        if component_id in {
            component.identity for component in self.list_components(assembly_id, recursive=True)
        }:
            raise AssemblyPostconditionError("component_delete_readback_present", component_id)

    def set_component_transform(
        self, assembly_id: str, component_id: str, transform: Sequence[float]
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        normalized = self._validate_transform(transform)
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.set_component_transform(assembly_id, component_id, normalized)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if not self._transforms_match(component.transform, normalized):
            raise AssemblyPostconditionError("transform_readback_mismatch")
        return component

    def set_component_load_state(
        self,
        assembly_id: str,
        component_id: str,
        state: ComponentLoadState,
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        if not isinstance(state, ComponentLoadState):
            raise AssemblyRefusal("invalid_component_load_state")
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.set_component_load_state(assembly_id, component_id, state)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.load_state is not state:
            raise AssemblyPostconditionError(
                "component_state_readback_mismatch", component.load_state.value
            )
        return component

    def set_component_fixed(
        self, assembly_id: str, component_id: str, fixed: bool
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        if not isinstance(fixed, bool):
            raise AssemblyRefusal("invalid_component_fixed_state")
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.set_component_fixed(assembly_id, component_id, fixed)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.fixed is not fixed:
            raise AssemblyPostconditionError(
                "component_fixed_readback_mismatch", str(component.fixed)
            )
        return component

    def set_component_configuration(
        self, assembly_id: str, component_id: str, configuration: str
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("component_id", component_id)
        self._require_identity("configuration", configuration)
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.set_component_configuration(
            assembly_id, component_id, configuration
        )
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.configuration != configuration:
            raise AssemblyPostconditionError(
                "component_configuration_readback_mismatch",
                str(component.configuration),
            )
        return component

    def add_mate(self, assembly_id: str, request: MateRequest) -> MateSnapshot:
        self._require_identity("assembly_id", assembly_id)
        normalized = self._validate_mate_request(request)
        mate_id = self._adapter.add_mate(assembly_id, normalized)
        self._require_identity("mate_id", mate_id)
        self._require_rebuild(assembly_id)
        mate = self._adapter.read_mate(assembly_id, mate_id)
        self._require_solved_mate(mate)
        if mate.kind is not None and mate.kind is not normalized.kind:
            raise AssemblyPostconditionError(
                "mate_kind_readback_mismatch", mate.kind.value
            )
        return mate

    def list_mates(self, assembly_id: str) -> tuple[MateSnapshot, ...]:
        self._require_identity("assembly_id", assembly_id)
        mates = tuple(self._adapter.list_mates(assembly_id))
        identities: list[str] = []
        for mate in mates:
            self._require_identity("mate_id", mate.identity)
            identities.append(mate.identity)
        if len(set(identities)) != len(identities):
            raise AssemblyPostconditionError("duplicate_mate_identity")
        return mates

    def set_mate_suppressed(
        self, assembly_id: str, mate_id: str, suppressed: bool
    ) -> MateSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("mate_id", mate_id)
        if not isinstance(suppressed, bool):
            raise AssemblyRefusal("invalid_mate_suppression_state")
        self._adapter.read_mate(assembly_id, mate_id)
        self._adapter.set_mate_suppressed(assembly_id, mate_id, suppressed)
        self._require_rebuild(assembly_id)
        mate = self._adapter.read_mate(assembly_id, mate_id)
        if suppressed:
            if mate.state is not MateState.SUPPRESSED:
                raise AssemblyPostconditionError(
                    "mate_suppression_readback_mismatch", mate.state.value
                )
            return mate
        self._require_solved_mate(mate)
        return mate

    def set_mate_value(
        self, assembly_id: str, mate_id: str, value: float
    ) -> MateSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("mate_id", mate_id)
        normalized = self._finite_value(value, "invalid_mate_value")
        before = self._adapter.read_mate(assembly_id, mate_id)
        if before.kind not in (MateKind.DISTANCE, MateKind.ANGLE):
            raise AssemblyRefusal("mate_value_not_supported", mate_id)
        self._adapter.set_mate_value(assembly_id, mate_id, normalized)
        self._require_rebuild(assembly_id)
        mate = self._adapter.read_mate(assembly_id, mate_id)
        self._require_solved_mate(mate)
        if mate.value is None or not math.isclose(
            mate.value, normalized, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise AssemblyPostconditionError("mate_value_readback_mismatch")
        return mate

    def _require_rebuild(self, assembly_id: str) -> None:
        report = self._adapter.rebuild_assembly(assembly_id)
        if not report.ok or report.errors:
            raise AssemblyPostconditionError(
                "rebuild_failed", "; ".join(report.errors)
            )

    @classmethod
    def _validate_mate_request(cls, request: MateRequest) -> MateRequest:
        if not isinstance(request, MateRequest):
            raise AssemblyRefusal("invalid_mate_request")
        try:
            kind = request.kind if isinstance(request.kind, MateKind) else MateKind(str(request.kind).strip())
        except ValueError as exc:
            raise AssemblyRefusal("unsupported_mate_kind", str(request.kind)) from exc
        if len(request.selection_refs) < 2 or any(
            not isinstance(ref, str) or not ref.strip() for ref in request.selection_refs
        ):
            raise AssemblyRefusal("invalid_mate_selection")
        value = request.value
        if kind in (MateKind.DISTANCE, MateKind.ANGLE):
            if value is None:
                raise AssemblyRefusal("mate_value_required", kind.value)
            value = cls._finite_value(value, "invalid_mate_value")
        elif value is not None:
            value = cls._finite_value(value, "invalid_mate_value")
        if request.alignment is not None:
            if request.alignment not in {"aligned", "anti_aligned", "closest"}:
                raise AssemblyRefusal("invalid_mate_alignment", request.alignment)
            if kind not in _MATE_ALIGNMENT_KINDS:
                raise AssemblyRefusal("mate_alignment_not_supported", kind.value)
        return replace(request, kind=kind, value=value)

    @staticmethod
    def _require_solved_mate(mate: MateSnapshot) -> None:
        if mate.state is not MateState.SOLVED:
            raise AssemblyPostconditionError("mate_not_solved", mate.state.value)
        if mate.rebuild_errors:
            raise AssemblyPostconditionError(
                "mate_rebuild_error", "; ".join(mate.rebuild_errors)
            )
        if mate.degrees_of_freedom is not None and mate.degrees_of_freedom < 0:
            raise AssemblyPostconditionError("invalid_degrees_of_freedom")

    @classmethod
    def _validate_unique_component_snapshots(
        cls, components: tuple[ComponentSnapshot, ...]
    ) -> None:
        identities: list[str] = []
        for component in components:
            cls._require_identity("component_id", component.identity)
            identities.append(component.identity)
        if len(set(identities)) != len(identities):
            raise AssemblyPostconditionError("duplicate_component_identity")

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise AssemblyRefusal(f"invalid_{label}")

    @staticmethod
    def _require_path(source_path: str) -> None:
        if not isinstance(source_path, str) or not source_path.strip():
            raise AssemblyRefusal("invalid_component_source_path")

    @staticmethod
    def _validate_transform(transform: Sequence[float]) -> tuple[float, ...]:
        try:
            values = tuple(float(value) for value in transform)
        except (TypeError, ValueError) as exc:
            raise AssemblyRefusal("invalid_component_transform") from exc
        if len(values) != 16 or any(not math.isfinite(value) for value in values):
            raise AssemblyRefusal("invalid_component_transform")
        return values

    @staticmethod
    def _transforms_match(actual: Sequence[float], expected: Sequence[float]) -> bool:
        if len(actual) != len(expected):
            return False
        return all(
            math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
            for a, b in zip(actual, expected, strict=True)
        )

    @staticmethod
    def _finite_value(value: float, reason: str) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise AssemblyRefusal(reason) from exc
        if not math.isfinite(normalized):
            raise AssemblyRefusal(reason)
        return normalized
