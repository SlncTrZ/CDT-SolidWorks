"""Sketch service with validation, rebuild gating and read-after-write verification."""

from __future__ import annotations

import math

from cdt_solidworks.part.runtime import DocumentTarget, PartSketchRuntime, ResolvedDocument
from cdt_solidworks.sketch.models import (
    AngularDimension,
    Arc,
    CenterLine,
    Circle,
    CoincidentConstraint,
    ConcentricConstraint,
    DefinitionState,
    DiameterDimension,
    DimensionTolerance,
    DistanceDimension,
    Ellipse,
    EqualConstraint,
    FixConstraint,
    HorizontalConstraint,
    LineSegment,
    MidpointConstraint,
    ParallelConstraint,
    PerpendicularConstraint,
    PlaneKind,
    RadiusDimension,
    SketchDefinition,
    SketchDimensionSnapshot,
    SketchEntitySnapshot,
    SketchPlane,
    SketchProfileSnapshot,
    SketchPoint,
    SketchRelationSnapshot,
    SketchSnapshot,
    Spline,
    SymmetricConstraint,
    TangentConstraint,
    UnfixConstraint,
    VerticalConstraint,
)

_DIMENSION_TOLERANCE = 1e-6
_MAX_SPLINE_POINTS = 64
_MIN_SPLINE_DEGREE = 2
_MAX_SPLINE_DEGREE = 5


class SketchError(RuntimeError):
    """Base error for Mechanical-90 sketch behavior."""


class SketchValidationError(SketchError):
    """Input is invalid before any native side effect."""


class SketchContextError(SketchError):
    """Explicit document/context identity is unsafe for mutation."""


class SketchMutationError(SketchError):
    """Mutation dispatched but rebuild/read-back acceptance failed."""


class SketchService:
    """Coordinates semantic sketch operations against an injected bounded runtime."""

    def __init__(self, runtime: PartSketchRuntime) -> None:
        self._runtime = runtime

    def create(self, target: DocumentTarget, definition: SketchDefinition) -> SketchSnapshot:
        self._validate_target(target)
        self._validate_definition(definition)
        document = self._resolve_part_document(target)

        receipt = self._runtime.create_sketch(document, definition)
        if not receipt.object_id.strip():
            raise SketchMutationError("native sketch mutation returned an empty identity")

        self._require_clean_rebuild(document, "sketch mutation")
        snapshot = self._runtime.get_sketch(document, receipt.object_id)
        if snapshot is None:
            raise SketchMutationError("sketch read-back failed: created sketch was not found")

        self._verify_snapshot(receipt.object_id, definition, snapshot)
        return snapshot

    def get(self, target: DocumentTarget, sketch_id: str) -> SketchSnapshot:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        document = self._resolve_part_document(target)
        snapshot = self._runtime.get_sketch(document, sketch_id)
        if snapshot is None:
            raise SketchMutationError(f"sketch read-back failed: {sketch_id!r} was not found")
        return snapshot

    def query_entities(
        self,
        target: DocumentTarget,
        sketch_id: str,
        *,
        max_items: int = 256,
    ) -> tuple[SketchEntitySnapshot, ...]:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1 or max_items > 4096:
            raise SketchValidationError("max_items must be an integer in [1, 4096]")
        document = self._resolve_part_document(target)
        return self._runtime.list_sketch_entities(document, sketch_id, max_items=max_items)

    def inspect_profile(self, target: DocumentTarget, sketch_id: str) -> SketchProfileSnapshot:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        document = self._resolve_part_document(target)
        return self._runtime.inspect_sketch_profile(document, sketch_id)

    def add_relation(
        self,
        target: DocumentTarget,
        sketch_id: str,
        relation_type: str,
        entity_ids: tuple[str, ...],
    ) -> tuple[SketchRelationSnapshot, ...]:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        normalized = relation_type.strip().lower()
        arity = {
            "horizontal": 1, "vertical": 1, "fix": 1,
            "coincident": 2, "concentric": 2, "tangent": 2, "parallel": 2,
            "perpendicular": 2, "equal": 2, "midpoint": 2,
            "symmetric": 3,
        }
        if normalized not in arity:
            raise SketchValidationError("unsupported sketch relation type")
        if len(entity_ids) != arity[normalized]:
            raise SketchValidationError(f"{normalized} relation requires {arity[normalized]} entity identities")
        if any(not isinstance(value, str) or not value.strip() for value in entity_ids):
            raise SketchValidationError("relation entity identities must be non-empty strings")
        if len(set(entity_ids)) != len(entity_ids):
            raise SketchValidationError("relation requires distinct entity identities")
        document = self._resolve_part_document(target)
        receipt = self._runtime.add_sketch_relation(document, sketch_id, normalized, entity_ids)
        if not receipt.object_id.strip():
            raise SketchMutationError("relation mutation returned an empty identity")
        self._require_clean_rebuild(document, "relation addition")
        relations = self._runtime.list_sketch_relations(document, sketch_id)
        if not any(item.relation_id == receipt.object_id for item in relations):
            raise SketchMutationError("relation addition read-back did not contain the created relation")
        return relations

    def list_relations(self, target: DocumentTarget, sketch_id: str) -> tuple[SketchRelationSnapshot, ...]:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        document = self._resolve_part_document(target)
        return self._runtime.list_sketch_relations(document, sketch_id)

    def delete_relation(
        self,
        target: DocumentTarget,
        sketch_id: str,
        relation_id: str,
    ) -> tuple[SketchRelationSnapshot, ...]:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        self._validate_identity(relation_id, "relation identity")
        document = self._resolve_part_document(target)
        receipt = self._runtime.delete_sketch_relation(document, sketch_id, relation_id)
        if receipt.object_id != relation_id:
            raise SketchMutationError("relation deletion read-back identity mismatch")
        self._require_clean_rebuild(document, "relation deletion")
        relations = self._runtime.list_sketch_relations(document, sketch_id)
        if any(item.relation_id == relation_id for item in relations):
            raise SketchMutationError("relation deletion read-back still contains the deleted relation")
        return relations

    def set_dimension_value(
        self,
        target: DocumentTarget,
        sketch_id: str,
        name: str,
        value: float,
        *,
        unit: str,
    ) -> SketchDimensionSnapshot:
        self._validate_target(target)
        self._validate_identity(sketch_id, "sketch identity")
        self._validate_identity(name, "dimension name")
        normalized_unit = unit.strip().lower()
        if normalized_unit not in {"mm", "deg"}:
            raise SketchValidationError("dimension unit must be 'mm' or 'deg'")
        if not math.isfinite(value) or value <= 0:
            raise SketchValidationError("dimension value must be positive and finite")
        if normalized_unit == "deg" and value >= 180.0:
            raise SketchValidationError("angular dimension value must be in the range (0, 180)")

        document = self._resolve_part_document(target)
        receipt = self._runtime.set_sketch_dimension(document, sketch_id, name, value, normalized_unit)
        if not receipt.object_id.strip():
            raise SketchMutationError("dimension mutation returned an empty identity")
        self._require_clean_rebuild(document, "dimension mutation")
        snapshot = self._runtime.get_sketch_dimension(document, sketch_id, name)
        if snapshot is None:
            raise SketchMutationError("dimension read-back failed after mutation")
        if snapshot.name != name or snapshot.unit != normalized_unit:
            raise SketchMutationError("dimension read-back identity or unit mismatch")
        if not math.isclose(snapshot.value, value, rel_tol=0.0, abs_tol=_DIMENSION_TOLERANCE):
            raise SketchMutationError(
                f"dimension read-back mismatch: expected {value}, got {snapshot.value}"
            )
        return snapshot

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise SketchValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise SketchValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise SketchValidationError("Mechanical-90 sketch dimensions currently require millimeter document units")

    @classmethod
    def _validate_definition(cls, definition: SketchDefinition) -> None:
        if not definition.name.strip():
            raise SketchValidationError("sketch name must not be empty")
        if not definition.entities:
            raise SketchValidationError("sketch requires at least one entity")

        cls._validate_plane(definition.plane)
        for entity in definition.entities:
            cls._validate_entity(entity)

        entity_count = len(definition.entities)
        for constraint in definition.constraints:
            cls._validate_constraint(constraint, definition.entities, entity_count)

        seen_names: set[str] = set()
        for dimension in definition.dimensions:
            if not dimension.name.strip():
                raise SketchValidationError("dimension name must not be empty")
            if dimension.name in seen_names:
                raise SketchValidationError(f"duplicate dimension name: {dimension.name}")
            seen_names.add(dimension.name)
            cls._validate_dimension(dimension, definition.entities, entity_count)

    @staticmethod
    def _validate_plane(plane: SketchPlane) -> None:
        if plane.kind in {PlaneKind.FACE, PlaneKind.REFERENCE} and not (plane.reference_id or "").strip():
            raise SketchValidationError("face/reference sketch plane requires a reference identity")
        if plane.kind in {PlaneKind.FRONT, PlaneKind.TOP, PlaneKind.RIGHT} and plane.reference_id is not None:
            raise SketchValidationError("standard sketch plane must not include a reference identity")

    @classmethod
    def _validate_entity(cls, entity: object) -> None:
        if isinstance(entity, LineSegment):
            cls._validate_point(entity.start)
            cls._validate_point(entity.end)
            if entity.start == entity.end:
                raise SketchValidationError("line segment endpoints must be distinct")
            return
        if isinstance(entity, CenterLine):
            cls._validate_point(entity.start)
            cls._validate_point(entity.end)
            if entity.start == entity.end:
                raise SketchValidationError("centerline endpoints must be distinct")
            return
        if isinstance(entity, Circle):
            cls._validate_point(entity.center)
            cls._require_positive_finite(entity.radius_mm, "circle radius")
            return
        if isinstance(entity, Arc):
            cls._validate_point(entity.center)
            cls._validate_point(entity.start)
            cls._validate_point(entity.end)
            start_radius = cls._distance(entity.center, entity.start)
            end_radius = cls._distance(entity.center, entity.end)
            if start_radius <= 0 or end_radius <= 0:
                raise SketchValidationError("arc endpoints must not equal the arc center")
            if not math.isclose(start_radius, end_radius, rel_tol=0.0, abs_tol=1e-6):
                raise SketchValidationError("arc start and end points must have equal radius from center")
            if entity.start == entity.end:
                raise SketchValidationError("arc start and end points must be distinct")
            return
        if isinstance(entity, Ellipse):
            cls._validate_point(entity.center)
            cls._validate_point(entity.major_axis_point)
            cls._validate_point(entity.minor_axis_point)
            major = (
                entity.major_axis_point.x_mm - entity.center.x_mm,
                entity.major_axis_point.y_mm - entity.center.y_mm,
            )
            minor = (
                entity.minor_axis_point.x_mm - entity.center.x_mm,
                entity.minor_axis_point.y_mm - entity.center.y_mm,
            )
            if math.hypot(*major) <= 0 or math.hypot(*minor) <= 0:
                raise SketchValidationError("ellipse axes must be nonzero")
            cross = major[0] * minor[1] - major[1] * minor[0]
            if math.isclose(cross, 0.0, rel_tol=0.0, abs_tol=1e-9):
                raise SketchValidationError("ellipse axes must be non-collinear")
            return
        if isinstance(entity, SketchPoint):
            cls._validate_point(entity.point)
            return
        if isinstance(entity, Spline):
            if len(entity.points) < 2:
                raise SketchValidationError("spline requires at least two points")
            if len(entity.points) > _MAX_SPLINE_POINTS:
                raise SketchValidationError(f"spline supports at most {_MAX_SPLINE_POINTS} points")
            if entity.degree < _MIN_SPLINE_DEGREE or entity.degree > _MAX_SPLINE_DEGREE:
                raise SketchValidationError(
                    f"spline degree must be in [{_MIN_SPLINE_DEGREE}, {_MAX_SPLINE_DEGREE}]"
                )
            if entity.degree >= len(entity.points):
                raise SketchValidationError("spline degree must be lower than its point count")
            for point in entity.points:
                cls._validate_point(point)
            if len(set(entity.points)) < 2:
                raise SketchValidationError("spline requires at least two distinct points")
            return
        raise SketchValidationError(f"unsupported sketch entity type: {type(entity).__name__}")

    @classmethod
    def _validate_constraint(
        cls,
        constraint: object,
        entities: tuple[object, ...],
        entity_count: int,
    ) -> None:
        line_types = (LineSegment, CenterLine)
        pair_types = (
            CoincidentConstraint,
            ConcentricConstraint,
            TangentConstraint,
            ParallelConstraint,
            PerpendicularConstraint,
            EqualConstraint,
        )
        if isinstance(constraint, (HorizontalConstraint, VerticalConstraint)):
            cls._validate_entity_index(constraint.entity_index, entity_count, "constraint")
            if not isinstance(entities[constraint.entity_index], line_types):
                raise SketchValidationError("horizontal/vertical relation requires a line or centerline")
            return
        if isinstance(constraint, pair_types):
            first = constraint.first_entity_index
            second = constraint.second_entity_index
            cls._validate_entity_index(first, entity_count, "constraint")
            cls._validate_entity_index(second, entity_count, "constraint")
            if first == second:
                raise SketchValidationError("pair relation requires distinct entities")
            if isinstance(constraint, (ParallelConstraint, PerpendicularConstraint)) and not all(
                isinstance(entities[index], line_types) for index in (first, second)
            ):
                raise SketchValidationError("parallel/perpendicular relation requires line entities")
            if isinstance(constraint, ConcentricConstraint) and not all(
                isinstance(entities[index], (Circle, Arc)) for index in (first, second)
            ):
                raise SketchValidationError("concentric relation requires circles/arcs")
            if isinstance(constraint, TangentConstraint) and any(
                isinstance(entities[index], SketchPoint) for index in (first, second)
            ):
                raise SketchValidationError("tangent relation cannot target a sketch point")
            return
        if isinstance(constraint, MidpointConstraint):
            cls._validate_entity_index(constraint.point_entity_index, entity_count, "constraint")
            cls._validate_entity_index(constraint.target_entity_index, entity_count, "constraint")
            if constraint.point_entity_index == constraint.target_entity_index:
                raise SketchValidationError("midpoint relation requires distinct entities")
            if not isinstance(entities[constraint.point_entity_index], SketchPoint):
                raise SketchValidationError("midpoint relation requires a sketch point as its first target")
            if not isinstance(entities[constraint.target_entity_index], line_types):
                raise SketchValidationError("midpoint relation target must be a line or centerline")
            return
        if isinstance(constraint, SymmetricConstraint):
            indexes = (
                constraint.first_entity_index,
                constraint.second_entity_index,
                constraint.symmetry_entity_index,
            )
            for index in indexes:
                cls._validate_entity_index(index, entity_count, "constraint")
            if len(set(indexes)) != 3:
                raise SketchValidationError("symmetric relation requires three distinct entities")
            if not isinstance(entities[constraint.symmetry_entity_index], line_types):
                raise SketchValidationError("symmetric relation requires a line/centerline symmetry reference")
            return
        if isinstance(constraint, (FixConstraint, UnfixConstraint)):
            cls._validate_entity_index(constraint.entity_index, entity_count, "constraint")
            return
        raise SketchValidationError(f"unsupported sketch constraint type: {type(constraint).__name__}")

    @classmethod
    def _validate_dimension(
        cls,
        dimension: object,
        entities: tuple[object, ...],
        entity_count: int,
    ) -> None:
        if isinstance(dimension, AngularDimension):
            cls._validate_entity_index(dimension.first_entity_index, entity_count, "dimension")
            cls._validate_entity_index(dimension.second_entity_index, entity_count, "dimension")
            if dimension.first_entity_index == dimension.second_entity_index:
                raise SketchValidationError("angular dimension requires distinct line targets")
            if not all(
                isinstance(entities[index], (LineSegment, CenterLine))
                for index in (dimension.first_entity_index, dimension.second_entity_index)
            ):
                raise SketchValidationError("angular dimension requires line or centerline targets")
            if not math.isfinite(dimension.value_deg) or dimension.value_deg <= 0 or dimension.value_deg >= 180:
                raise SketchValidationError("angular dimension must be finite and in the range (0, 180)")
            cls._validate_tolerance(dimension.tolerance)
            return

        entity_index = getattr(dimension, "entity_index", -1)
        cls._validate_entity_index(entity_index, entity_count, "dimension")
        value_mm = getattr(dimension, "value_mm", None)
        if not isinstance(value_mm, (int, float)) or not math.isfinite(float(value_mm)) or float(value_mm) <= 0:
            raise SketchValidationError(f"dimension {dimension.name!r} must be positive and finite")

        target = entities[entity_index]
        if isinstance(dimension, DiameterDimension):
            if not isinstance(target, (Circle, Arc)):
                raise SketchValidationError("diameter dimension must target a circle or arc")
        elif isinstance(dimension, RadiusDimension):
            if not isinstance(target, (Circle, Arc)):
                raise SketchValidationError("radius dimension must target a circle or arc")
        elif not isinstance(dimension, DistanceDimension):
            raise SketchValidationError(f"unsupported dimension type: {type(dimension).__name__}")
        cls._validate_tolerance(getattr(dimension, "tolerance", None))

    @staticmethod
    def _validate_tolerance(tolerance: DimensionTolerance | None) -> None:
        if tolerance is None:
            return
        if not math.isfinite(tolerance.lower) or not math.isfinite(tolerance.upper):
            raise SketchValidationError("dimension tolerance values must be finite")
        if tolerance.lower > tolerance.upper:
            raise SketchValidationError("dimension tolerance lower value must not exceed upper value")

    @staticmethod
    def _validate_point(point: object) -> None:
        x_mm = getattr(point, "x_mm", math.nan)
        y_mm = getattr(point, "y_mm", math.nan)
        if not math.isfinite(x_mm) or not math.isfinite(y_mm):
            raise SketchValidationError("sketch coordinates must be finite")

    @staticmethod
    def _distance(first: object, second: object) -> float:
        return math.hypot(second.x_mm - first.x_mm, second.y_mm - first.y_mm)

    @staticmethod
    def _validate_entity_index(index: int, entity_count: int, label: str) -> None:
        if index < 0 or index >= entity_count:
            raise SketchValidationError(f"{label} entity index {index} is out of range")

    @staticmethod
    def _validate_identity(value: str, label: str) -> None:
        if not value.strip():
            raise SketchValidationError(f"{label} must not be empty")

    @staticmethod
    def _require_positive_finite(value: float, label: str) -> None:
        if not math.isfinite(value) or value <= 0:
            raise SketchValidationError(f"{label} must be positive and finite")

    def _resolve_part_document(self, target: DocumentTarget) -> ResolvedDocument:
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise SketchContextError("resolved document identity does not match requested document identity")
        if document.revision != target.expected_revision:
            raise SketchContextError(
                f"stale document context: expected revision {target.expected_revision}, got {document.revision}"
            )
        if document.document_type.lower() != "part":
            raise SketchContextError("sketch mutation requires a part document")
        if document.units != target.expected_units:
            raise SketchContextError(
                f"document units changed from {target.expected_units!r} to {document.units!r}"
            )
        return document

    def _require_clean_rebuild(self, document: ResolvedDocument, label: str) -> None:
        rebuild = self._runtime.rebuild(document)
        if not rebuild.ok:
            detail = rebuild.error_code or rebuild.message or "unknown rebuild error"
            raise SketchMutationError(f"rebuild failed after {label}: {detail}")

    @staticmethod
    def _verify_snapshot(
        sketch_id: str,
        definition: SketchDefinition,
        snapshot: SketchSnapshot,
    ) -> None:
        if snapshot.sketch_id != sketch_id:
            raise SketchMutationError("sketch read-back identity mismatch")
        if snapshot.plane != definition.plane:
            raise SketchMutationError("sketch read-back plane mismatch")
        if snapshot.entity_count != len(definition.entities):
            raise SketchMutationError(
                f"sketch read-back entity count mismatch: expected {len(definition.entities)}, got {snapshot.entity_count}"
            )
        if snapshot.constraint_count != len(definition.constraints):
            raise SketchMutationError(
                "sketch read-back constraint count mismatch: "
                f"expected {len(definition.constraints)}, got {snapshot.constraint_count}"
            )

        for dimension in definition.dimensions:
            actual = snapshot.dimension_values.get(dimension.name)
            expected = dimension.value_for_readback
            if actual is None:
                raise SketchMutationError(f"sketch read-back missing dimension {dimension.name!r}")
            if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=_DIMENSION_TOLERANCE):
                raise SketchMutationError(
                    f"sketch read-back dimension {dimension.name!r} mismatch: expected {expected}, got {actual}"
                )

        if snapshot.definition_state is DefinitionState.OVER_DEFINED:
            raise SketchMutationError("sketch read-back reports over-defined constraint state")
