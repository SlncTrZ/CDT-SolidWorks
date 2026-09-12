"""Assembly domain contract for lane D.

This module deliberately contains no COM implementation. Runtime/native lanes can satisfy
``AssemblyAdapter`` without duplicating assembly correctness rules.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    """Raised when native mutation completed but observable state is not acceptable."""

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


@dataclass(frozen=True)
class MateRequest:
    kind: str
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


class AssemblyAdapter(Protocol):
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

    def set_component_transform(
        self, assembly_id: str, component_id: str, transform: tuple[float, ...]
    ) -> None: ...

    def set_component_load_state(
        self, assembly_id: str, component_id: str, state: ComponentLoadState
    ) -> None: ...

    def add_mate(self, assembly_id: str, request: MateRequest) -> str: ...

    def read_mate(self, assembly_id: str, mate_id: str) -> MateSnapshot: ...

    def rebuild_assembly(self, assembly_id: str) -> RebuildReport: ...


class AssemblyService:
    """Enforces assembly identity, rebuild, and read-back invariants."""

    def __init__(self, adapter: AssemblyAdapter) -> None:
        self._adapter = adapter

    def insert_component(
        self, assembly_id: str, source_path: str, configuration: str | None = None
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_path(source_path)
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
        if component.configuration != configuration:
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
        if component.configuration != configuration:
            raise AssemblyPostconditionError(
                "component_configuration_readback_mismatch",
                str(component.configuration),
            )
        return component

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
        self._adapter.read_component(assembly_id, component_id)
        self._adapter.set_component_load_state(assembly_id, component_id, state)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, component_id)
        if component.load_state is not state:
            raise AssemblyPostconditionError(
                "component_state_readback_mismatch", component.load_state.value
            )
        return component

    def add_mate(self, assembly_id: str, request: MateRequest) -> MateSnapshot:
        self._require_identity("assembly_id", assembly_id)
        if not request.kind.strip():
            raise AssemblyRefusal("invalid_mate_kind")
        if len(request.selection_refs) < 2 or any(
            not ref.strip() for ref in request.selection_refs
        ):
            raise AssemblyRefusal("invalid_mate_selection")
        if request.value is not None and not math.isfinite(request.value):
            raise AssemblyRefusal("invalid_mate_value")
        mate_id = self._adapter.add_mate(assembly_id, request)
        self._require_identity("mate_id", mate_id)
        self._require_rebuild(assembly_id)
        mate = self._adapter.read_mate(assembly_id, mate_id)
        if mate.state is not MateState.SOLVED:
            raise AssemblyPostconditionError("mate_not_solved", mate.state.value)
        if mate.rebuild_errors:
            raise AssemblyPostconditionError(
                "mate_rebuild_error", "; ".join(mate.rebuild_errors)
            )
        if mate.degrees_of_freedom is not None and mate.degrees_of_freedom < 0:
            raise AssemblyPostconditionError("invalid_degrees_of_freedom")
        return mate

    def _require_rebuild(self, assembly_id: str) -> None:
        report = self._adapter.rebuild_assembly(assembly_id)
        if not report.ok or report.errors:
            raise AssemblyPostconditionError(
                "rebuild_failed", "; ".join(report.errors)
            )

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
        values = tuple(float(value) for value in transform)
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
