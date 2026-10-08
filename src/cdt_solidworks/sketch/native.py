"""Bounded native sketch adapter for Mechanical-90 lane A.

The adapter deliberately depends on injected shared-runtime primitives for serialized
execution, document resolution, plane selection, sketch-feature resolution, COM member
access, and rebuild verification. It never exposes caller-selected COM method names.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.part.runtime import DocumentTarget, MutationReceipt, RebuildResult, ResolvedDocument
from cdt_solidworks.sketch.models import (
    AngularDimension,
    Arc,
    CenterLine,
    Circle,
    CoincidentConstraint,
    ConcentricConstraint,
    DefinitionState,
    DiameterDimension,
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
    ProfileState,
    RadiusDimension,
    SketchContourSnapshot,
    SketchDefinition,
    SketchDimensionSnapshot,
    SketchEntitySnapshot,
    SketchPlane,
    SketchPoint,
    SketchProfileSnapshot,
    SketchRelationSnapshot,
    SketchSnapshot,
    Spline,
    SymmetricConstraint,
    TangentConstraint,
    UnfixConstraint,
    VerticalConstraint,
)

T = TypeVar("T")

_SW_DIMENSION_ANGULAR = 3
_SW_DIMENSION_RADIAL = 5
_SW_DIMENSION_DIAMETER = 6
_SW_DIMENSION_HORIZONTAL_LINEAR = 11
_SW_DIMENSION_VERTICAL_LINEAR = 12
_SW_INPUT_DIM_VALUE_ON_CREATE = 10
_SW_ADD_SPECIFIC_DIMENSION_SUCCESS = 0

_RELATION_LABELS = {
    4: "horizontal",
    5: "vertical",
    6: "tangent",
    7: "parallel",
    8: "perpendicular",
    9: "coincident",
    10: "concentric",
    11: "symmetric",
    12: "midpoint",
    14: "equal",
    17: "fix",
}


class NativeSketchUnsupportedError(RuntimeError):
    """Requested sketch behavior has no accepted bounded native implementation yet."""


@dataclass(frozen=True, slots=True)
class NativeSketchBinding:
    """Native model binding resolved inside the serialized COM execution context."""

    model: Any
    document_id: str
    revision: int
    units: str
    document_type: str = "part"
    configuration: str | None = None


class SerializedSketchExecutor(Protocol):
    def __call__(
        self,
        operation: Callable[[Any], T],
        *,
        stage: str,
        mutation: bool,
    ) -> T: ...


BindingResolver = Callable[[Any, DocumentTarget], NativeSketchBinding]
PlaneSelector = Callable[[Any, SketchPlane], None]
SketchFeatureResolver = Callable[[Any, Any, str], Any]
SketchPlaneResolver = Callable[[Any, Any, str], SketchPlane]
MemberAccessor = Callable[..., Any]
RebuildVerifier = Callable[[Any], RebuildResult]


class SketchNativeRuntime:
    """Finite native sketch runtime; unsupported semantics fail explicitly."""

    def __init__(
        self,
        *,
        executor: SerializedSketchExecutor,
        binding_resolver: BindingResolver,
        plane_selector: PlaneSelector,
        sketch_feature_resolver: SketchFeatureResolver,
        member: MemberAccessor,
        rebuild_verifier: RebuildVerifier,
        plane_resolver: SketchPlaneResolver | None = None,
    ) -> None:
        self._execute = executor
        self._resolve_binding = binding_resolver
        self._select_plane = plane_selector
        self._resolve_sketch_feature = sketch_feature_resolver
        self._member = member
        self._rebuild = rebuild_verifier
        self._resolve_sketch_plane = plane_resolver or self._resolve_plane_from_reference
        self._planes: dict[tuple[str, str], SketchPlane] = {}
        self._logical_counts: dict[tuple[str, str], int] = {}

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        def operation(app: Any) -> ResolvedDocument:
            binding = self._resolve_binding(app, target)
            return ResolvedDocument(
                document_id=binding.document_id,
                revision=binding.revision,
                document_type=binding.document_type,
                units=binding.units,
                configuration=binding.configuration,
            )

        return self._execute(operation, stage="sketch_resolve_document", mutation=False)

    def create_sketch(
        self,
        document: ResolvedDocument,
        definition: SketchDefinition,
    ) -> MutationReceipt:
        if any(isinstance(item, UnfixConstraint) for item in definition.constraints):
            raise NativeSketchUnsupportedError("native unfix relation creation is not promoted in this lane")
        if any(item.tolerance is not None for item in definition.dimensions):
            raise NativeSketchUnsupportedError(
                "native dimension tolerance creation is not promoted without read/write evidence"
            )

        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            self._select_plane(model, definition.plane)
            manager = self._member(model, "SketchManager")
            self._member(manager, "InsertSketch", True)
            active_sketch = self._member(manager, "ActiveSketch")
            if active_sketch is None:
                raise NativeSketchUnsupportedError("SOLIDWORKS did not activate a sketch after InsertSketch")

            previous_add_to_db = bool(getattr(manager, "AddToDB"))
            previous_display = bool(getattr(manager, "DisplayWhenAdded"))
            manager.AddToDB = True
            manager.DisplayWhenAdded = False
            native_entities: list[Any] = []
            try:
                for entity in definition.entities:
                    native_entities.append(self._create_entity(manager, entity))
                relation_manager = self._member(active_sketch, "RelationManager")
                for constraint in definition.constraints:
                    entity_indexes, relation_type = self._constraint_spec(constraint)
                    relation_entities = tuple(native_entities[index] for index in entity_indexes)
                    try:
                        relation = self._member(
                            relation_manager,
                            "AddRelation",
                            self._dispatch_array(relation_entities),
                            relation_type,
                        )
                    except Exception as exc:
                        try:
                            self._member(manager, "InsertSketch", True)
                        except Exception:
                            pass
                        raise NativeSketchUnsupportedError(
                            f"SOLIDWORKS rejected {type(constraint).__name__}; relation may be duplicate or incompatible"
                        ) from exc
                    if relation is None:
                        try:
                            self._member(manager, "InsertSketch", True)
                        except Exception:
                            pass
                        raise NativeSketchUnsupportedError(
                            f"SOLIDWORKS rejected {type(constraint).__name__}; relation may be duplicate or incompatible"
                        )
            finally:
                manager.AddToDB = previous_add_to_db
                manager.DisplayWhenAdded = previous_display

            if definition.dimensions:
                previous_input_dimension = bool(
                    self._member(app, "GetUserPreferenceToggle", _SW_INPUT_DIM_VALUE_ON_CREATE)
                )
                self._member(
                    app,
                    "SetUserPreferenceToggle",
                    _SW_INPUT_DIM_VALUE_ON_CREATE,
                    False,
                )
                try:
                    for index, dimension in enumerate(definition.dimensions):
                        self._create_dimension(model, native_entities, definition, dimension, index)
                finally:
                    self._member(
                        app,
                        "SetUserPreferenceToggle",
                        _SW_INPUT_DIM_VALUE_ON_CREATE,
                        previous_input_dimension,
                    )

            self._member(manager, "InsertSketch", True)
            feature = self._resolve_sketch_feature(model, active_sketch, definition.name)
            if feature is None:
                raise NativeSketchUnsupportedError("created sketch feature could not be resolved")
            try:
                feature.Name = definition.name
            except Exception:
                pass
            feature_name = str(self._member(feature, "Name") or definition.name)
            if not feature_name.strip():
                raise NativeSketchUnsupportedError("created sketch feature returned an empty identity")
            key = (document.document_id, feature_name)
            self._planes[key] = definition.plane
            self._logical_counts[key] = len(definition.entities)
            return MutationReceipt(feature_name)

        return self._execute(operation, stage="sketch_create_native", mutation=True)

    def get_sketch(self, document: ResolvedDocument, sketch_id: str) -> SketchSnapshot | None:
        def operation(app: Any) -> SketchSnapshot | None:
            binding = self._binding_from_document(app, document)
            feature = self._member(binding.model, "FeatureByName", sketch_id)
            if feature is None:
                return None
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                return None
            segments = self._as_tuple(self._member(sketch, "GetSketchSegments"))
            sketch_points = self._as_tuple(self._member(sketch, "GetSketchPoints2"))
            standalone_points = tuple(point for point in sketch_points if self._is_user_sketch_point(point))
            relation_manager = self._member(sketch, "RelationManager")
            relations = self._relation_snapshots(relation_manager)
            dimensions = self._dimension_snapshots(relation_manager)
            status = int(self._member(sketch, "GetConstrainedStatus") or 1)
            key = (document.document_id, sketch_id)
            plane = self._planes.get(key)
            if plane is None:
                plane = self._resolve_sketch_plane(binding.model, sketch, sketch_id)
                if not isinstance(plane, SketchPlane):
                    raise NativeSketchUnsupportedError("sketch-plane resolver returned an invalid plane snapshot")
                self._planes[key] = plane
            logical_count = self._logical_counts.get(key)
            if logical_count is None:
                logical_count = len(segments) + len(standalone_points)
            entity_ids = tuple(self._entity_id(entity) for entity in (*segments, *standalone_points))
            return SketchSnapshot(
                sketch_id=sketch_id,
                plane=plane,
                entity_count=logical_count,
                constraint_count=len(relations),
                dimension_values_mm={item.name: item.value for item in dimensions},
                definition_state=self._definition_state(status),
                entity_ids=entity_ids,
                relations=relations,
                dimensions=dimensions,
            )

        return self._execute(operation, stage="sketch_get_native", mutation=False)

    def list_sketch_entities(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        *,
        max_items: int = 256,
    ) -> tuple[SketchEntitySnapshot, ...]:
        if max_items < 1:
            raise NativeSketchUnsupportedError("sketch entity query max_items must be positive")

        def operation(app: Any) -> tuple[SketchEntitySnapshot, ...]:
            binding = self._binding_from_document(app, document)
            feature = self._member(binding.model, "FeatureByName", sketch_id)
            if feature is None:
                raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
            segments = self._as_tuple(self._member(sketch, "GetSketchSegments"))
            points = tuple(
                point for point in self._as_tuple(self._member(sketch, "GetSketchPoints2"))
                if self._is_user_sketch_point(point)
            )
            if len(segments) + len(points) > max_items:
                raise NativeSketchUnsupportedError(
                    f"sketch entity query exceeds bounded max_items={max_items}; partial results are refused"
                )
            result: list[SketchEntitySnapshot] = []
            labels = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}
            for segment in segments:
                entity_id = self._entity_id(segment)
                if not entity_id:
                    raise NativeSketchUnsupportedError("sketch segment lacks stable entity identity")
                segment_type = int(self._member(segment, "GetType"))
                construction = bool(self._member(segment, "ConstructionGeometry"))
                try:
                    length = float(self._member(segment, "GetLength"))
                except (AttributeError, TypeError, ValueError):
                    length = math.nan
                length_mm = length * 1000.0 if math.isfinite(length) and length >= 0 else None
                result.append(
                    SketchEntitySnapshot(
                        entity_id=entity_id,
                        entity_type=labels.get(segment_type, "unknown"),
                        construction=construction,
                        length_mm=length_mm,
                    )
                )
            for point in points:
                entity_id = self._entity_id(point)
                if not entity_id:
                    raise NativeSketchUnsupportedError("sketch point lacks stable entity identity")
                result.append(SketchEntitySnapshot(entity_id, "point", False, None))
            return tuple(result)

        return self._execute(operation, stage="sketch_entity_query_native", mutation=False)

    def inspect_sketch_profile(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        *,
        max_contours: int = 256,
        max_entities: int = 1024,
    ) -> SketchProfileSnapshot:
        def operation(app: Any) -> SketchProfileSnapshot:
            binding = self._binding_from_document(app, document)
            feature = self._member(binding.model, "FeatureByName", sketch_id)
            if feature is None:
                raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
            native_contours = self._as_tuple(self._member(sketch, "GetSketchContours"))
            if len(native_contours) > max_contours:
                raise NativeSketchUnsupportedError("sketch profile contour bound exceeded; partial results are refused")
            contours: list[SketchContourSnapshot] = []
            total_entities = 0
            for contour in native_contours:
                segments = self._as_tuple(self._member(contour, "GetSketchSegments"))
                total_entities += len(segments)
                if total_entities > max_entities:
                    raise NativeSketchUnsupportedError("sketch profile entity bound exceeded; partial results are refused")
                entity_ids = tuple(self._entity_id(segment) for segment in segments)
                if not entity_ids or any(not value for value in entity_ids):
                    raise NativeSketchUnsupportedError("sketch contour lacks stable entity identity")
                closed = bool(self._member(contour, "IsClosed"))
                digest = hashlib.sha256("|".join(entity_ids).encode("utf-8")).hexdigest()[:16]
                contours.append(SketchContourSnapshot(f"contour:{digest}", closed, entity_ids))
            if not contours:
                state = ProfileState.AMBIGUOUS
            elif all(item.closed for item in contours):
                state = ProfileState.CLOSED
            elif all(not item.closed for item in contours):
                state = ProfileState.OPEN
            else:
                state = ProfileState.AMBIGUOUS
            return SketchProfileSnapshot(sketch_id=sketch_id, state=state, contours=tuple(contours))

        return self._execute(operation, stage="sketch_profile_inspect_native", mutation=False)

    def add_sketch_relation(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        relation_type: str,
        entity_ids: tuple[str, ...],
    ) -> MutationReceipt:
        relation_code = next((code for code, label in _RELATION_LABELS.items() if label == relation_type), None)
        if relation_code is None:
            raise NativeSketchUnsupportedError(f"unsupported native sketch relation: {relation_type}")

        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            feature = self._member(model, "FeatureByName", sketch_id)
            if feature is None:
                raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
            candidates = (
                *self._as_tuple(self._member(sketch, "GetSketchSegments")),
                *self._as_tuple(self._member(sketch, "GetSketchPoints2")),
            )
            by_id = {self._entity_id(entity): entity for entity in candidates if self._entity_id(entity)}
            if any(entity_id not in by_id for entity_id in entity_ids):
                raise NativeSketchUnsupportedError("relation target entity identity was not found in the sketch")
            manager = self._member(model, "SketchManager")
            self._member(model, "ClearSelection2", True)
            if not bool(self._member(feature, "Select2", False, 0)):
                raise NativeSketchUnsupportedError("could not select sketch for relation mutation")
            self._member(manager, "InsertSketch", True)
            try:
                relation_manager = self._member(sketch, "RelationManager")
                relation = self._member(
                    relation_manager,
                    "AddRelation",
                    self._dispatch_array(tuple(by_id[entity_id] for entity_id in entity_ids)),
                    relation_code,
                )
                if relation is None:
                    raise NativeSketchUnsupportedError("SOLIDWORKS refused the requested sketch relation")
                entries = self._relation_entries(relation_manager)
                matching = [
                    snapshot for native_relation, snapshot in entries
                    if native_relation is relation
                ]
                if len(matching) != 1:
                    raise NativeSketchUnsupportedError("added sketch relation could not be identified uniquely")
                return MutationReceipt(matching[0].relation_id)
            finally:
                self._member(manager, "InsertSketch", True)

        return self._execute(operation, stage="sketch_add_relation_native", mutation=True)

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        def operation(app: Any) -> RebuildResult:
            binding = self._binding_from_document(app, document)
            return self._rebuild(binding.model)

        return self._execute(operation, stage="sketch_rebuild_native", mutation=True)

    def list_sketch_relations(
        self,
        document: ResolvedDocument,
        sketch_id: str,
    ) -> tuple[SketchRelationSnapshot, ...]:
        def operation(app: Any) -> tuple[SketchRelationSnapshot, ...]:
            binding = self._binding_from_document(app, document)
            feature = self._member(binding.model, "FeatureByName", sketch_id)
            if feature is None:
                raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
            relation_manager = self._member(sketch, "RelationManager")
            return self._relation_snapshots(relation_manager)

        return self._execute(operation, stage="sketch_list_relations_native", mutation=False)

    def delete_sketch_relation(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        relation_id: str,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            feature = self._member(binding.model, "FeatureByName", sketch_id)
            if feature is None:
                raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
            sketch = self._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
            relation_manager = self._member(sketch, "RelationManager")
            for relation, snapshot in self._relation_entries(relation_manager):
                if snapshot.relation_id != relation_id:
                    continue
                deleted = bool(self._member(relation_manager, "DeleteRelation", relation))
                if not deleted:
                    raise NativeSketchUnsupportedError("SOLIDWORKS refused to delete the requested sketch relation")
                return MutationReceipt(relation_id)
            raise NativeSketchUnsupportedError(f"sketch relation {relation_id!r} was not found")

        return self._execute(operation, stage="sketch_delete_relation_native", mutation=True)

    def set_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
        value: float,
        unit: str,
    ) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            relation_manager = self._relation_manager(binding.model, sketch_id)
            for relation_type, dimension, snapshot in self._dimension_entries(relation_manager):
                if snapshot.name != name:
                    continue
                expected_unit = "deg" if relation_type == 2 else "mm"
                if unit != expected_unit:
                    raise NativeSketchUnsupportedError(
                        f"dimension {name!r} requires unit {expected_unit!r}, not {unit!r}"
                    )
                system_value = math.radians(value) if unit == "deg" else value / 1000.0
                status = int(self._member(dimension, "SetSystemValue3", system_value, 1, ""))
                if status != 0:
                    raise NativeSketchUnsupportedError(
                        f"SOLIDWORKS rejected dimension update for {name!r} with status {status}"
                    )
                return MutationReceipt(name)
            raise NativeSketchUnsupportedError(f"sketch dimension {name!r} was not found")

        return self._execute(operation, stage="sketch_set_dimension_native", mutation=True)

    def get_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
    ) -> SketchDimensionSnapshot | None:
        def operation(app: Any) -> SketchDimensionSnapshot | None:
            binding = self._binding_from_document(app, document)
            relation_manager = self._relation_manager(binding.model, sketch_id)
            return next(
                (snapshot for _, _, snapshot in self._dimension_entries(relation_manager) if snapshot.name == name),
                None,
            )

        return self._execute(operation, stage="sketch_get_dimension_native", mutation=False)

    def _relation_manager(self, model: Any, sketch_id: str) -> Any:
        feature = self._member(model, "FeatureByName", sketch_id)
        if feature is None:
            raise NativeSketchUnsupportedError(f"sketch {sketch_id!r} was not found")
        sketch = self._member(feature, "GetSpecificFeature2")
        if sketch is None:
            raise NativeSketchUnsupportedError(f"feature {sketch_id!r} is not a sketch")
        return self._member(sketch, "RelationManager")

    def _create_dimension(
        self,
        model: Any,
        native_entities: list[Any],
        definition: SketchDefinition,
        dimension: object,
        index: int,
    ) -> None:
        self._member(model, "ClearSelection2", True)
        if isinstance(dimension, AngularDimension):
            indexes = (dimension.first_entity_index, dimension.second_entity_index)
            dimension_type = _SW_DIMENSION_ANGULAR
            system_value = math.radians(float(dimension.value_deg))
        elif isinstance(dimension, RadiusDimension):
            indexes = (dimension.entity_index,)
            dimension_type = _SW_DIMENSION_RADIAL
            system_value = float(dimension.value_mm) / 1000.0
        elif isinstance(dimension, DiameterDimension):
            indexes = (dimension.entity_index,)
            dimension_type = _SW_DIMENSION_DIAMETER
            system_value = float(dimension.value_mm) / 1000.0
        elif isinstance(dimension, DistanceDimension):
            indexes = (dimension.entity_index,)
            target = definition.entities[dimension.entity_index]
            if not isinstance(target, (LineSegment, CenterLine)):
                raise NativeSketchUnsupportedError(
                    "native linear dimensions currently require a line or centerline target"
                )
            dx = float(target.end.x_mm) - float(target.start.x_mm)
            dy = float(target.end.y_mm) - float(target.start.y_mm)
            if math.isclose(dy, 0.0, rel_tol=0.0, abs_tol=1e-9):
                dimension_type = _SW_DIMENSION_HORIZONTAL_LINEAR
            elif math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=1e-9):
                dimension_type = _SW_DIMENSION_VERTICAL_LINEAR
            else:
                raise NativeSketchUnsupportedError(
                    "native linear dimensions currently require a horizontal or vertical line"
                )
            system_value = float(dimension.value_mm) / 1000.0
        else:
            raise NativeSketchUnsupportedError(
                f"unsupported native sketch dimension: {type(dimension).__name__}"
            )

        for selection_index, entity_index in enumerate(indexes):
            selected = bool(
                self._member(
                    native_entities[entity_index],
                    "Select4",
                    selection_index > 0,
                    self._null_dispatch(),
                )
            )
            if not selected:
                raise NativeSketchUnsupportedError(
                    f"could not select typed entity {entity_index} for dimension {dimension.name!r}"
                )

        x_m, y_m = self._dimension_location(definition, index)
        extension = self._member(model, "Extension")
        error = self._byref_int()
        try:
            display = self._member(
                extension,
                "AddSpecificDimension",
                x_m,
                y_m,
                0.0,
                dimension_type,
                error,
            )
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"native dimension {dimension.name!r} failed during AddSpecificDimension: {exc}"
            ) from exc
        error_code = int(getattr(error, "value", 0))
        if display is None or error_code != _SW_ADD_SPECIFIC_DIMENSION_SUCCESS:
            raise NativeSketchUnsupportedError(
                f"SOLIDWORKS did not create dimension {dimension.name!r}; AddSpecificDimension status={error_code}"
            )
        try:
            native_dimension = self._member(display, "GetDimension2", 0)
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"native dimension {dimension.name!r} failed during GetDimension2"
            ) from exc
        if native_dimension is None:
            raise NativeSketchUnsupportedError(
                f"created dimension {dimension.name!r} has no native dimension object"
            )
        try:
            native_dimension.Name = dimension.name
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"native dimension {dimension.name!r} failed while setting its deterministic name"
            ) from exc
        try:
            status = int(self._member(native_dimension, "SetSystemValue3", system_value, 1, ""))
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"native dimension {dimension.name!r} failed during SetSystemValue3"
            ) from exc
        if status != 0:
            raise NativeSketchUnsupportedError(
                f"SOLIDWORKS rejected initial value for dimension {dimension.name!r} with status {status}"
            )
        try:
            native_dimension.DrivenState = 2 if dimension.driving else 1
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"native dimension {dimension.name!r} failed while setting driving/driven state"
            ) from exc
        self._member(model, "ClearSelection2", True)

    @staticmethod
    def _dimension_location(definition: SketchDefinition, index: int) -> tuple[float, float]:
        coordinates: list[tuple[float, float]] = []
        for entity in definition.entities:
            for point_name in ("start", "end", "center", "major_axis_point", "minor_axis_point", "point"):
                point = getattr(entity, point_name, None)
                if point is not None:
                    coordinates.append((float(point.x_mm), float(point.y_mm)))
            for point in getattr(entity, "points", ()):
                coordinates.append((float(point.x_mm), float(point.y_mm)))
        max_x = max((value[0] for value in coordinates), default=0.0)
        max_y = max((value[1] for value in coordinates), default=0.0)
        offset_mm = 10.0 + 5.0 * index
        return (max_x + offset_mm) / 1000.0, (max_y + offset_mm) / 1000.0

    def _dimension_snapshots(self, relation_manager: Any) -> tuple[SketchDimensionSnapshot, ...]:
        return tuple(snapshot for _, _, snapshot in self._dimension_entries(relation_manager))

    def _dimension_entries(
        self,
        relation_manager: Any,
    ) -> tuple[tuple[int, Any, SketchDimensionSnapshot], ...]:
        entries: list[tuple[int, Any, SketchDimensionSnapshot]] = []
        for relation in self._as_tuple(self._member(relation_manager, "GetRelations", 0)):
            if relation is None:
                continue
            relation_type = int(self._member(relation, "GetRelationType") or 0)
            if relation_type not in {1, 2, 3, 15}:
                continue
            display = self._member(relation, "GetDisplayDimension")
            if display is None:
                continue
            dimension = self._member(display, "GetDimension2", 0)
            if dimension is None:
                continue
            name = str(self._member(dimension, "Name") or "").strip()
            if not name:
                raise NativeSketchUnsupportedError("native sketch dimension returned an empty name")
            raw_value = self._member(dimension, "GetSystemValue3", 1, "")
            values = self._as_tuple(raw_value)
            if not values:
                raise NativeSketchUnsupportedError(f"dimension {name!r} returned no system value")
            system_value = float(values[0])
            if not math.isfinite(system_value):
                raise NativeSketchUnsupportedError(f"dimension {name!r} returned a non-finite system value")
            unit = "deg" if relation_type == 2 else "mm"
            value = math.degrees(system_value) if unit == "deg" else system_value * 1000.0
            driven_state = int(self._member(dimension, "DrivenState"))
            entries.append(
                (
                    relation_type,
                    dimension,
                    SketchDimensionSnapshot(
                        name=name,
                        value=value,
                        unit=unit,
                        driving=driven_state == 2,
                    ),
                )
            )
        return tuple(entries)

    @staticmethod
    def _constraint_spec(constraint: object) -> tuple[tuple[int, ...], int]:
        if isinstance(constraint, HorizontalConstraint):
            return (constraint.entity_index,), 4
        if isinstance(constraint, VerticalConstraint):
            return (constraint.entity_index,), 5
        if isinstance(constraint, TangentConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 6
        if isinstance(constraint, ParallelConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 7
        if isinstance(constraint, PerpendicularConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 8
        if isinstance(constraint, CoincidentConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 9
        if isinstance(constraint, ConcentricConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 10
        if isinstance(constraint, SymmetricConstraint):
            return (
                constraint.first_entity_index,
                constraint.second_entity_index,
                constraint.symmetry_entity_index,
            ), 11
        if isinstance(constraint, MidpointConstraint):
            return (constraint.point_entity_index, constraint.target_entity_index), 12
        if isinstance(constraint, EqualConstraint):
            return (constraint.first_entity_index, constraint.second_entity_index), 14
        if isinstance(constraint, FixConstraint):
            return (constraint.entity_index,), 17
        if isinstance(constraint, UnfixConstraint):
            raise NativeSketchUnsupportedError("native unfix relation creation is not promoted in this lane")
        raise NativeSketchUnsupportedError(
            f"unsupported native sketch relation: {type(constraint).__name__}"
        )

    def _relation_snapshots(self, relation_manager: Any) -> tuple[SketchRelationSnapshot, ...]:
        return tuple(snapshot for _, snapshot in self._relation_entries(relation_manager))

    def _relation_entries(self, relation_manager: Any) -> tuple[tuple[Any, SketchRelationSnapshot], ...]:
        relations = self._as_tuple(self._member(relation_manager, "GetRelations", 0))
        entries: list[tuple[Any, SketchRelationSnapshot]] = []
        for relation in relations:
            if relation is None:
                continue
            relation_type = int(self._member(relation, "GetRelationType") or 0)
            label = _RELATION_LABELS.get(relation_type)
            if label is None:
                continue
            try:
                entities = self._as_tuple(self._member(relation, "GetDefinitionEntities2"))
            except Exception:
                entities = self._as_tuple(self._member(relation, "GetDefinitionEntities"))
            entity_ids = tuple(self._entity_id(entity) for entity in entities if entity is not None)
            if not entity_ids or any(not value for value in entity_ids):
                raise NativeSketchUnsupportedError(
                    f"{label} relation lacks stable sketch entity identity"
                )
            digest = hashlib.sha256(
                f"{relation_type}|{'|'.join(entity_ids)}".encode("utf-8")
            ).hexdigest()[:16]
            snapshot = SketchRelationSnapshot(
                relation_id=f"rel:{digest}",
                relation_type=label,
                entity_ids=entity_ids,
            )
            entries.append((relation, snapshot))
        return tuple(entries)

    def _resolve_plane_from_reference(self, model: Any, sketch: Any, sketch_id: str) -> SketchPlane:
        """Resolve a persisted sketch plane without localized feature names or process-local cache."""
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Windows-only native path
            raise NativeSketchUnsupportedError(
                "native sketch-plane read-back requires pywin32 on Windows"
            ) from exc

        try:
            entity_type = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            reference = getattr(sketch, "GetReferenceEntity")(entity_type)
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"could not resolve reference entity for sketch {sketch_id!r}"
            ) from exc
        if reference is None:
            raise NativeSketchUnsupportedError(
                f"sketch {sketch_id!r} has no stable reference entity"
            )

        # swSelDATUMPLANES == 4. Face-backed sketches need persistent face references,
        # which remain integration-gated rather than being guessed from topology order.
        if int(entity_type.value) != 4:
            raise NativeSketchUnsupportedError(
                "face-backed sketch plane read-back requires a persistent face-reference resolver"
            )

        try:
            reference_transform = self._transform_data(reference)
        except Exception as exc:
            raise NativeSketchUnsupportedError(
                f"reference plane transform is unavailable for sketch {sketch_id!r}"
            ) from exc

        feature = self._member(model, "FirstFeature")
        ref_index = 0
        while feature is not None:
            try:
                feature_type = str(self._member(feature, "GetTypeName2") or self._member(feature, "GetTypeName") or "")
            except Exception:
                feature_type = ""
            if feature_type == "RefPlane":
                specific = self._member(feature, "GetSpecificFeature2")
                if specific is not None:
                    try:
                        candidate_transform = self._transform_data(specific)
                    except Exception:
                        candidate_transform = ()
                    if self._transforms_close(reference_transform, candidate_transform):
                        if ref_index == 0:
                            return SketchPlane(PlaneKind.FRONT)
                        if ref_index == 1:
                            return SketchPlane(PlaneKind.TOP)
                        if ref_index == 2:
                            return SketchPlane(PlaneKind.RIGHT)
                        name = str(self._member(feature, "Name") or "").strip()
                        if not name:
                            raise NativeSketchUnsupportedError(
                                "reference-plane sketch resolved to an unnamed feature"
                            )
                        return SketchPlane(PlaneKind.REFERENCE, reference_id=name)
                ref_index += 1
            feature = self._member(feature, "GetNextFeature")

        raise NativeSketchUnsupportedError(
            f"reference plane for sketch {sketch_id!r} was not found in the feature tree"
        )

    def _transform_data(self, ref_plane: Any) -> tuple[float, ...]:
        transform = self._member(ref_plane, "Transform")
        if transform is None:
            return ()
        values = self._as_tuple(self._member(transform, "ArrayData"))
        return tuple(float(value) for value in values)

    @staticmethod
    def _transforms_close(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
        if len(first) != 16 or len(second) != 16:
            return False
        return all(abs(a - b) <= 1e-9 for a, b in zip(first, second, strict=True))

    def _binding_from_document(self, app: Any, document: ResolvedDocument) -> NativeSketchBinding:
        target = DocumentTarget(
            document_id=document.document_id,
            expected_revision=document.revision,
            expected_units=document.units,
        )
        binding = self._resolve_binding(app, target)
        if binding.document_id != document.document_id:
            raise NativeSketchUnsupportedError("native binding identity changed during sketch operation")
        return binding

    def _create_entity(self, manager: Any, entity: object) -> Any:
        if isinstance(entity, LineSegment):
            segment = self._member(
                manager,
                "CreateLine",
                self._m(entity.start.x_mm),
                self._m(entity.start.y_mm),
                0.0,
                self._m(entity.end.x_mm),
                self._m(entity.end.y_mm),
                0.0,
            )
            self._require_segment(segment, "line")
            segment.ConstructionGeometry = bool(entity.construction)
            return segment
        if isinstance(entity, CenterLine):
            segment = self._member(
                manager,
                "CreateCenterLine",
                self._m(entity.start.x_mm),
                self._m(entity.start.y_mm),
                0.0,
                self._m(entity.end.x_mm),
                self._m(entity.end.y_mm),
                0.0,
            )
            self._require_segment(segment, "centerline")
            return segment
        if isinstance(entity, Circle):
            segment = self._member(
                manager,
                "CreateCircleByRadius",
                self._m(entity.center.x_mm),
                self._m(entity.center.y_mm),
                0.0,
                self._m(entity.radius_mm),
            )
            self._require_segment(segment, "circle")
            segment.ConstructionGeometry = bool(entity.construction)
            return segment
        if isinstance(entity, Arc):
            segment = self._member(
                manager,
                "CreateArc",
                self._m(entity.center.x_mm),
                self._m(entity.center.y_mm),
                0.0,
                self._m(entity.start.x_mm),
                self._m(entity.start.y_mm),
                0.0,
                self._m(entity.end.x_mm),
                self._m(entity.end.y_mm),
                0.0,
                int(entity.direction.value),
            )
            self._require_segment(segment, "arc")
            segment.ConstructionGeometry = bool(entity.construction)
            return segment
        if isinstance(entity, Ellipse):
            segment = self._member(
                manager,
                "CreateEllipse",
                self._m(entity.center.x_mm),
                self._m(entity.center.y_mm),
                0.0,
                self._m(entity.major_axis_point.x_mm),
                self._m(entity.major_axis_point.y_mm),
                0.0,
                self._m(entity.minor_axis_point.x_mm),
                self._m(entity.minor_axis_point.y_mm),
                0.0,
            )
            self._require_segment(segment, "ellipse")
            segment.ConstructionGeometry = bool(entity.construction)
            return segment
        if isinstance(entity, SketchPoint):
            point = self._member(
                manager,
                "CreatePoint",
                self._m(entity.point.x_mm),
                self._m(entity.point.y_mm),
                0.0,
            )
            if point is None:
                raise NativeSketchUnsupportedError("SOLIDWORKS did not create the requested sketch point")
            return point
        if isinstance(entity, Spline):
            if entity.degree != 3:
                raise NativeSketchUnsupportedError(
                    "SOLIDWORKS CreateSpline2 does not expose degree control; native lane currently accepts degree=3 only"
                )
            point_data: list[float] = []
            for point in entity.points:
                point_data.extend((self._m(point.x_mm), self._m(point.y_mm), 0.0))
            segment = self._member(
                manager,
                "CreateSpline2",
                self._double_array(point_data),
                bool(entity.simulate_natural_ends),
            )
            self._require_segment(segment, "spline")
            segment.ConstructionGeometry = bool(entity.construction)
            return segment
        raise NativeSketchUnsupportedError(f"unsupported native sketch entity: {type(entity).__name__}")

    @staticmethod
    def _byref_int() -> Any:
        """Return a BYREF I4 for COM out parameters, with a unit-test fallback."""
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError:
            class _IntRef:
                value = 0

            return _IntRef()
        return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

    @staticmethod
    def _null_dispatch() -> Any:
        """Return a typed null IDispatch for late-bound COM optional objects."""
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError:
            return None
        return VARIANT(pythoncom.VT_DISPATCH, None)

    @staticmethod
    def _dispatch_array(values: tuple[Any, ...]) -> Any:
        """Marshal sketch entities as SAFEARRAY(IDispatch) on native Windows."""
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError:
            # Non-Windows unit doubles accept the logical tuple directly.
            return tuple(values)
        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, tuple(values))

    @staticmethod
    def _double_array(values: list[float]) -> Any:
        """Marshal an explicit SAFEARRAY(R8) for pywin32 object-array COM parameters."""
        try:
            import pythoncom  # type: ignore[import-not-found]
            from win32com.client import VARIANT  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Windows-only native path
            raise NativeSketchUnsupportedError(
                "native spline creation requires pywin32 on Windows"
            ) from exc
        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, tuple(float(value) for value in values))

    @staticmethod
    def _m(value_mm: float) -> float:
        return float(value_mm) / 1000.0

    @staticmethod
    def _require_segment(segment: Any, label: str) -> None:
        if segment is None:
            raise NativeSketchUnsupportedError(f"SOLIDWORKS did not create the requested {label}")

    def _is_user_sketch_point(self, point: Any) -> bool:
        try:
            # swSketchPointType_User == 1. Other point types are defining/internal
            # points of segments and must not inflate logical entity count.
            return int(self._member(point, "Type")) == 1
        except Exception:
            return False

    def _entity_id(self, entity: Any) -> str:
        try:
            raw = self._member(entity, "GetID")
        except Exception:
            return ""
        values = self._as_tuple(raw)
        return ":".join(str(value) for value in values) if values else str(raw or "")

    @staticmethod
    def _definition_state(status: int) -> DefinitionState:
        if status == 2:
            return DefinitionState.UNDER_DEFINED
        if status == 3:
            return DefinitionState.FULLY_DEFINED
        if status in {4, 5, 6}:
            return DefinitionState.OVER_DEFINED
        return DefinitionState.UNKNOWN

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)
