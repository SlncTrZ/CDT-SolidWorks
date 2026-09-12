"""Sketch service with validation, rebuild gating and read-after-write verification."""

from __future__ import annotations

import math

from cdt_solidworks.part.runtime import DocumentTarget, PartSketchRuntime, ResolvedDocument
from cdt_solidworks.sketch.models import (
    Circle,
    CoincidentConstraint,
    DefinitionState,
    DiameterDimension,
    DistanceDimension,
    HorizontalConstraint,
    LineSegment,
    PlaneKind,
    SketchDefinition,
    SketchSnapshot,
    VerticalConstraint,
)

_DIMENSION_TOLERANCE_MM = 1e-6


class SketchError(RuntimeError):
    """Base error for lane-C sketch behavior."""


class SketchValidationError(SketchError):
    """Input is invalid before any native side effect."""


class SketchContextError(SketchError):
    """Explicit document/context identity is unsafe for mutation."""


class SketchMutationError(SketchError):
    """Mutation dispatched but rebuild/read-back acceptance failed."""


class SketchService:
    """Coordinates one semantic sketch mutation against an injected runtime."""

    def __init__(self, runtime: PartSketchRuntime) -> None:
        self._runtime = runtime

    def create(self, target: DocumentTarget, definition: SketchDefinition) -> SketchSnapshot:
        self._validate_target(target)
        self._validate_definition(definition)
        document = self._resolve_part_document(target)

        receipt = self._runtime.create_sketch(document, definition)
        if not receipt.object_id.strip():
            raise SketchMutationError("native sketch mutation returned an empty identity")

        rebuild = self._runtime.rebuild(document)
        if not rebuild.ok:
            detail = rebuild.error_code or rebuild.message or "unknown rebuild error"
            raise SketchMutationError(f"rebuild failed after sketch mutation: {detail}")

        snapshot = self._runtime.get_sketch(document, receipt.object_id)
        if snapshot is None:
            raise SketchMutationError("sketch read-back failed: created sketch was not found")

        self._verify_snapshot(receipt.object_id, definition, snapshot)
        return snapshot

    def get(self, target: DocumentTarget, sketch_id: str) -> SketchSnapshot:
        self._validate_target(target)
        if not sketch_id.strip():
            raise SketchValidationError("sketch identity must not be empty")
        document = self._resolve_part_document(target)
        snapshot = self._runtime.get_sketch(document, sketch_id)
        if snapshot is None:
            raise SketchMutationError(f"sketch read-back failed: {sketch_id!r} was not found")
        return snapshot

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise SketchValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise SketchValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise SketchValidationError("W1 sketch dimensions currently require millimeter document units")

    @classmethod
    def _validate_definition(cls, definition: SketchDefinition) -> None:
        if not definition.name.strip():
            raise SketchValidationError("sketch name must not be empty")
        if not definition.entities:
            raise SketchValidationError("sketch requires at least one entity")

        plane = definition.plane
        if plane.kind in {PlaneKind.FACE, PlaneKind.REFERENCE} and not (plane.reference_id or "").strip():
            raise SketchValidationError("face/reference sketch plane requires a reference identity")
        if plane.kind in {PlaneKind.FRONT, PlaneKind.TOP, PlaneKind.RIGHT} and plane.reference_id is not None:
            raise SketchValidationError("standard sketch plane must not include a reference identity")

        entity_count = len(definition.entities)
        for entity in definition.entities:
            if isinstance(entity, LineSegment):
                cls._validate_point(entity.start.x_mm, entity.start.y_mm)
                cls._validate_point(entity.end.x_mm, entity.end.y_mm)
                if entity.start == entity.end:
                    raise SketchValidationError("line segment endpoints must be distinct")
            elif isinstance(entity, Circle):
                cls._validate_point(entity.center.x_mm, entity.center.y_mm)
                if not math.isfinite(entity.radius_mm) or entity.radius_mm <= 0:
                    raise SketchValidationError("circle radius must be positive and finite")
            else:  # pragma: no cover - closed union protects normal callers
                raise SketchValidationError(f"unsupported sketch entity type: {type(entity).__name__}")

        for constraint in definition.constraints:
            if isinstance(constraint, (HorizontalConstraint, VerticalConstraint)):
                cls._validate_entity_index(constraint.entity_index, entity_count, "constraint")
            elif isinstance(constraint, CoincidentConstraint):
                cls._validate_entity_index(constraint.first_entity_index, entity_count, "constraint")
                cls._validate_entity_index(constraint.second_entity_index, entity_count, "constraint")
            else:  # pragma: no cover - closed union protects normal callers
                raise SketchValidationError(f"unsupported sketch constraint type: {type(constraint).__name__}")

        seen_names: set[str] = set()
        for dimension in definition.dimensions:
            if not dimension.name.strip():
                raise SketchValidationError("dimension name must not be empty")
            if dimension.name in seen_names:
                raise SketchValidationError(f"duplicate dimension name: {dimension.name}")
            seen_names.add(dimension.name)
            cls._validate_entity_index(dimension.entity_index, entity_count, "dimension")
            if not math.isfinite(dimension.value_mm) or dimension.value_mm <= 0:
                raise SketchValidationError(f"dimension {dimension.name!r} must be positive and finite")
            if isinstance(dimension, DiameterDimension):
                if not isinstance(definition.entities[dimension.entity_index], Circle):
                    raise SketchValidationError("diameter dimension must target a circle")
            elif not isinstance(dimension, DistanceDimension):  # pragma: no cover
                raise SketchValidationError(f"unsupported dimension type: {type(dimension).__name__}")

    @staticmethod
    def _validate_point(x_mm: float, y_mm: float) -> None:
        if not math.isfinite(x_mm) or not math.isfinite(y_mm):
            raise SketchValidationError("sketch coordinates must be finite")

    @staticmethod
    def _validate_entity_index(index: int, entity_count: int, label: str) -> None:
        if index < 0 or index >= entity_count:
            raise SketchValidationError(f"{label} entity index {index} is out of range")

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
            actual = snapshot.dimension_values_mm.get(dimension.name)
            if actual is None:
                raise SketchMutationError(f"sketch read-back missing dimension {dimension.name!r}")
            if not math.isclose(actual, dimension.value_mm, rel_tol=0.0, abs_tol=_DIMENSION_TOLERANCE_MM):
                raise SketchMutationError(
                    f"sketch read-back dimension {dimension.name!r} mismatch: "
                    f"expected {dimension.value_mm}, got {actual}"
                )

        if snapshot.definition_state is DefinitionState.OVER_DEFINED:
            raise SketchMutationError("sketch read-back reports over-defined constraint state")
