"""Bounded native sketch adapter for Mechanical-90 lane A.

The adapter deliberately depends on injected shared-runtime primitives for serialized
execution, document resolution, plane selection, sketch-feature resolution, COM member
access, and rebuild verification. It never exposes caller-selected COM method names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.part.runtime import DocumentTarget, MutationReceipt, RebuildResult, ResolvedDocument
from cdt_solidworks.sketch.models import (
    Arc,
    CenterLine,
    Circle,
    DefinitionState,
    Ellipse,
    LineSegment,
    PlaneKind,
    SketchDefinition,
    SketchDimensionSnapshot,
    SketchPlane,
    SketchPoint,
    SketchRelationSnapshot,
    SketchSnapshot,
    Spline,
)

T = TypeVar("T")


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
        if definition.constraints:
            raise NativeSketchUnsupportedError(
                "native relation creation remains integration-gated; use implemented domain semantics only"
            )
        if definition.dimensions:
            raise NativeSketchUnsupportedError(
                "native dimension creation remains integration-gated; use implemented domain semantics only"
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
            try:
                for entity in definition.entities:
                    self._create_entity(manager, entity)
            finally:
                manager.AddToDB = previous_add_to_db
                manager.DisplayWhenAdded = previous_display

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
            relation_count = int(self._member(relation_manager, "GetRelationsCount", 0) or 0)
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
                constraint_count=relation_count,
                dimension_values_mm={},
                definition_state=self._definition_state(status),
                entity_ids=entity_ids,
            )

        return self._execute(operation, stage="sketch_get_native", mutation=False)

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
        raise NativeSketchUnsupportedError("stable native relation identities are not promoted in this lane")

    def delete_sketch_relation(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        relation_id: str,
    ) -> MutationReceipt:
        raise NativeSketchUnsupportedError("stable native relation deletion is not promoted in this lane")

    def set_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
        value: float,
        unit: str,
    ) -> MutationReceipt:
        raise NativeSketchUnsupportedError("native dimension editing is not promoted in this lane")

    def get_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
    ) -> SketchDimensionSnapshot | None:
        raise NativeSketchUnsupportedError("native dimension querying is not promoted in this lane")

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

    def _create_entity(self, manager: Any, entity: object) -> None:
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
            return
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
            return
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
            return
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
            return
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
            return
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
            return
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
            return
        raise NativeSketchUnsupportedError(f"unsupported native sketch entity: {type(entity).__name__}")

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
