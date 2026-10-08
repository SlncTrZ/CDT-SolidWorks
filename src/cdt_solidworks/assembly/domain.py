"""Assembly domain contract for the Mechanical 90 Agent C lane.

The service owns bounded assembly correctness rules while native COM execution is
provided by an injected adapter. Durable identity never depends on remembered
ActiveDoc or selection state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any, Protocol, Sequence


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
    parent_identity: str | None = None


@dataclass(frozen=True)
class ComponentPatternSnapshot:
    identity: str
    seed_component_ids: tuple[str, ...]
    spacing_m: float
    total_instances: int
    direction_resolved: bool


@dataclass(frozen=True)
class MateRequest:
    kind: MateKind | str
    selection_refs: tuple[str, ...]
    value: float | None = None
    alignment: str | None = None
    constraint: str | None = None
    # Production callers pass opaque ``swref1.`` identities and inject the
    # topology port before native dispatch.  The native entities stay internal
    # to this request and are never serialized back to the MCP surface.
    resolved_entities: tuple[Any, ...] = ()
    resolved_reference_component_ids: tuple[str | None, ...] = ()
    topology_resolved: bool = False


@dataclass(frozen=True)
class MateSnapshot:
    identity: str
    state: MateState
    component_ids: tuple[str, ...]
    degrees_of_freedom: int | None
    reference_component_ids: tuple[str | None, ...] = ()
    rebuild_errors: tuple[str, ...] = ()
    kind: MateKind | None = None
    value: float | None = None
    error_status: int | None = None


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
    ) -> str: ...

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

    def create_linear_component_pattern(
        self,
        assembly_id: str,
        seed_component_ids: tuple[str, ...],
        direction_ref: str,
        spacing_m: float,
        total_instances: int,
    ) -> str: ...

    def read_component_pattern(
        self, assembly_id: str, pattern_id: str
    ) -> ComponentPatternSnapshot: ...

    def add_mate(self, assembly_id: str, request: MateRequest) -> str: ...

    def list_mates(self, assembly_id: str) -> tuple[MateSnapshot, ...]: ...

    def read_mate(self, assembly_id: str, mate_id: str) -> MateSnapshot: ...

    def set_mate_suppressed(
        self, assembly_id: str, mate_id: str, suppressed: bool
    ) -> None: ...

    def set_mate_value(self, assembly_id: str, mate_id: str, value: float) -> None: ...

    def delete_mate(self, assembly_id: str, mate_id: str) -> None: ...

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
        self,
        assembly_id: str,
        source_path: str,
        configuration: str | None = None,
        *,
        transform: Sequence[float] | None = None,
    ) -> ComponentSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_path(source_path)
        if configuration is not None:
            self._require_identity("configuration", configuration)
        normalized_transform = (
            None if transform is None else self._validate_transform(transform)
        )
        component_id = self._adapter.insert_component(
            assembly_id, source_path, configuration
        )
        self._require_identity("component_id", component_id)
        if normalized_transform is not None:
            self._adapter.set_component_transform(
                assembly_id, component_id, normalized_transform
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
        if normalized_transform is not None and not self._transforms_match(
            component.transform, normalized_transform
        ):
            raise AssemblyPostconditionError("transform_readback_mismatch")
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
        before = self._adapter.read_component(assembly_id, component_id)
        if before.parent_identity is not None:
            raise AssemblyRefusal("nested_component_replace_not_supported", component_id)
        replacement_id = self._adapter.replace_component(
            assembly_id, component_id, source_path, configuration
        )
        self._require_identity("replacement_component_id", replacement_id)
        self._require_rebuild(assembly_id)
        component = self._adapter.read_component(assembly_id, replacement_id)
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

    def create_linear_component_pattern(
        self,
        assembly_id: str,
        *,
        seed_component_ids: Sequence[str],
        direction_ref: str,
        spacing_m: float,
        total_instances: int,
    ) -> ComponentPatternSnapshot:
        self._require_identity("assembly_id", assembly_id)
        seeds = tuple(seed_component_ids)
        if not seeds or any(not isinstance(seed, str) or not seed.strip() for seed in seeds):
            raise AssemblyRefusal("invalid_pattern_seed_components")
        if len(set(seeds)) != len(seeds):
            raise AssemblyRefusal("duplicate_pattern_seed_component")
        self._require_identity("pattern_direction_ref", direction_ref)
        spacing = self._finite_value(spacing_m, "invalid_pattern_spacing")
        if spacing <= 0:
            raise AssemblyRefusal("invalid_pattern_spacing")
        if (
            not isinstance(total_instances, int)
            or isinstance(total_instances, bool)
            or total_instances < 2
        ):
            raise AssemblyRefusal("invalid_pattern_instance_count")

        before = self.list_components(assembly_id, recursive=True)
        before_count = len(before)
        by_id = {component.identity: component for component in before}
        for seed in seeds:
            component = by_id.get(seed)
            if component is None:
                raise AssemblyRefusal("missing_component", seed)
            if component.parent_identity is not None:
                raise AssemblyRefusal("nested_pattern_seed_not_supported", seed)

        pattern_id = self._adapter.create_linear_component_pattern(
            assembly_id,
            seeds,
            direction_ref,
            spacing,
            total_instances,
        )
        self._require_identity("pattern_id", pattern_id)
        self._require_rebuild(assembly_id)
        pattern = self._adapter.read_component_pattern(assembly_id, pattern_id)
        if pattern.identity != pattern_id:
            raise AssemblyPostconditionError(
                "pattern_identity_readback_mismatch", pattern.identity
            )
        if pattern.seed_component_ids != seeds:
            raise AssemblyPostconditionError("pattern_seed_readback_mismatch")
        if not pattern.direction_resolved:
            raise AssemblyPostconditionError("pattern_direction_readback_missing")
        if pattern.total_instances != total_instances:
            raise AssemblyPostconditionError("pattern_count_readback_mismatch")
        if not math.isclose(
            pattern.spacing_m, spacing, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise AssemblyPostconditionError("pattern_spacing_readback_mismatch")

        after_count = len(self.list_components(assembly_id, recursive=True))
        expected_count = before_count + len(seeds) * (total_instances - 1)
        if after_count != expected_count:
            raise AssemblyPostconditionError(
                "pattern_component_count_readback_mismatch",
                f"expected={expected_count}, actual={after_count}",
            )
        return pattern

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
        self._require_mate_reference_pairing(mate, normalized)
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

    def read_mate(self, assembly_id: str, mate_id: str) -> MateSnapshot:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("mate_id", mate_id)
        mate = self._adapter.read_mate(assembly_id, mate_id)
        if mate.identity != mate_id:
            raise AssemblyPostconditionError(
                "mate_identity_readback_mismatch", mate.identity
            )
        return mate

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

    def delete_mate(self, assembly_id: str, mate_id: str) -> None:
        self._require_identity("assembly_id", assembly_id)
        self._require_identity("mate_id", mate_id)
        self.read_mate(assembly_id, mate_id)
        self._adapter.delete_mate(assembly_id, mate_id)
        self._require_rebuild(assembly_id)
        if mate_id in {mate.identity for mate in self.list_mates(assembly_id)}:
            raise AssemblyPostconditionError("mate_delete_readback_present", mate_id)

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
        refs = tuple(request.selection_refs)
        if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise AssemblyRefusal("invalid_mate_selection")
        if kind is MateKind.WIDTH:
            if len(refs) != 4:
                raise AssemblyRefusal("invalid_width_mate_selection")
        elif len(refs) != 2:
            raise AssemblyRefusal("invalid_mate_selection")
        value = request.value
        if kind in (MateKind.DISTANCE, MateKind.ANGLE):
            if value is None:
                raise AssemblyRefusal("mate_value_required", kind.value)
            value = cls._finite_value(value, "invalid_mate_value")
        elif value is not None:
            raise AssemblyRefusal("mate_value_not_supported", kind.value)
        constraint = request.constraint
        if kind is MateKind.WIDTH:
            constraint = "centered" if constraint is None else constraint
            if constraint not in {"centered", "free"}:
                raise AssemblyRefusal("invalid_width_constraint", str(constraint))
        elif kind is MateKind.SLOT:
            constraint = "centered" if constraint is None else constraint
            if constraint not in {"centered", "free"}:
                raise AssemblyRefusal("invalid_slot_constraint", str(constraint))
        elif constraint is not None:
            raise AssemblyRefusal("mate_constraint_not_supported", kind.value)
        if request.alignment is not None:
            if request.alignment not in {"aligned", "anti_aligned", "closest"}:
                raise AssemblyRefusal("invalid_mate_alignment", request.alignment)
            if kind not in _MATE_ALIGNMENT_KINDS:
                raise AssemblyRefusal("mate_alignment_not_supported", kind.value)
        resolved_entities = tuple(request.resolved_entities)
        if resolved_entities and len(resolved_entities) != len(refs):
            raise AssemblyRefusal("topology_resolution_count_mismatch")
        resolved_component_ids = tuple(request.resolved_reference_component_ids)
        if resolved_component_ids and len(resolved_component_ids) != len(refs):
            raise AssemblyRefusal("topology_component_pairing_count_mismatch")
        if any(
            item is not None and (not isinstance(item, str) or not item.strip())
            for item in resolved_component_ids
        ):
            raise AssemblyRefusal("invalid_topology_component_identity")
        if not isinstance(request.topology_resolved, bool):
            raise AssemblyRefusal("invalid_topology_resolution_state")
        if request.topology_resolved and len(resolved_entities) != len(refs):
            raise AssemblyRefusal("topology_resolution_count_mismatch")
        return replace(
            request,
            kind=kind,
            selection_refs=refs,
            value=value,
            constraint=constraint,
            resolved_entities=resolved_entities,
            resolved_reference_component_ids=resolved_component_ids,
            topology_resolved=request.topology_resolved,
        )

    @staticmethod
    def _require_mate_reference_pairing(mate: MateSnapshot, request: MateRequest) -> None:
        if request.resolved_reference_component_ids:
            expected = request.resolved_reference_component_ids
        elif request.topology_resolved:
            # The native adapter verifies exact persistent-reference pairing.
            # Some topology implementations intentionally do not expose an
            # assembly component identity for an entity; in that case there is
            # no component-id assertion to duplicate at the domain layer.
            return
        else:
            # Legacy test/native fixtures from the frozen base use bounded
            # component/name strings.  Production ``swref1.`` callers are
            # resolved by the injected topology port and never enter this path.
            legacy_expected: list[str | None] = []
            for reference in request.selection_refs:
                if reference.startswith("swref1."):
                    raise AssemblyRefusal("topology_resolution_required")
                if reference.startswith("assembly:"):
                    legacy_expected.append(None)
                elif reference.startswith("component:"):
                    legacy_expected.append(reference.split(":", 1)[1])
                else:
                    legacy_expected.append(reference.split(":", 1)[0])
            expected = tuple(legacy_expected)
        if mate.reference_component_ids != tuple(expected):
            raise AssemblyPostconditionError(
                "mate_reference_pairing_mismatch",
                f"expected={tuple(expected)!r}, actual={mate.reference_component_ids!r}",
            )

    @staticmethod
    def _require_solved_mate(mate: MateSnapshot) -> None:
        if mate.state is not MateState.SOLVED:
            raise AssemblyPostconditionError("mate_not_solved", mate.state.value)
        if mate.error_status not in (0, 1):
            raise AssemblyPostconditionError(
                "mate_error_status", str(mate.error_status)
            )
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
