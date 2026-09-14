from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Callable, TypeVar

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult  # noqa: E402
from cdt_solidworks.sketch.models import (  # noqa: E402
    AngularDimension,
    Circle,
    DiameterDimension,
    DistanceDimension,
    HorizontalConstraint,
    LineSegment,
    ParallelConstraint,
    PlaneKind,
    Point2D,
    RadiusDimension,
    SketchDefinition,
    SketchPlane,
)
from cdt_solidworks.sketch.native import (  # noqa: E402
    NativeSketchBinding,
    NativeSketchUnsupportedError,
    SketchNativeRuntime,
)
from cdt_solidworks.sketch.service import SketchService  # noqa: E402

T = TypeVar("T")


class FakeSegment:
    def __init__(self, segment_id: int, select: Callable[[Any, bool], None] | None = None) -> None:
        self.segment_id = segment_id
        self.ConstructionGeometry = False
        self._select = select

    def GetID(self) -> tuple[int, int]:
        return (self.segment_id, 0)

    def Select4(self, append: bool, select_data: Any) -> bool:
        if self._select is not None:
            self._select(self, append)
        return True


class FakePoint:
    def __init__(self, point_id: int, point_type: int) -> None:
        self.point_id = point_id
        self.Type = point_type

    def GetID(self) -> tuple[int, int]:
        return (self.point_id, 1)


class FakeDimension:
    def __init__(self, value: float = 1.0) -> None:
        self.Name = "D1"
        self.DrivenState = 2
        self.value = value

    def SetSystemValue3(self, value: float, configuration: int, names: Any) -> int:
        self.value = value
        return 0

    def GetSystemValue3(self, configuration: int, names: Any) -> float:
        return self.value


class FakeDisplayDimension:
    def __init__(self, dimension: FakeDimension) -> None:
        self.dimension = dimension

    def GetDimension2(self, configuration: int) -> FakeDimension:
        return self.dimension


class FakeRelation:
    def __init__(
        self,
        relation_type: int,
        entities: tuple[Any, ...],
        display_dimension: FakeDisplayDimension | None = None,
    ) -> None:
        self.relation_type = relation_type
        self.entities = entities
        self.display_dimension = display_dimension

    def GetRelationType(self) -> int:
        return self.relation_type

    def GetDefinitionEntities2(self) -> tuple[Any, ...]:
        return self.entities

    def GetDisplayDimension(self) -> FakeDisplayDimension | None:
        return self.display_dimension


class FakeRelationManager:
    def __init__(self) -> None:
        self.relations: list[FakeRelation] = []

    def GetRelationsCount(self, filter_value: int) -> int:
        return len(self.relations)

    def GetRelations(self, filter_value: int) -> tuple[FakeRelation, ...]:
        return tuple(self.relations)

    def AddRelation(self, entities: tuple[Any, ...], relation_type: int) -> FakeRelation:
        relation = FakeRelation(relation_type, tuple(entities))
        self.relations.append(relation)
        return relation

    def DeleteRelation(self, relation: FakeRelation) -> bool:
        self.relations.remove(relation)
        return True


class FakeSketch:
    def __init__(self) -> None:
        self.segments: list[FakeSegment] = []
        self.points: list[Any] = []
        self.RelationManager = FakeRelationManager()

    def GetSketchSegments(self) -> tuple[FakeSegment, ...]:
        return tuple(self.segments)

    def GetUserPoints2(self) -> tuple[Any, ...]:
        return tuple(self.points)

    def GetSketchPoints2(self) -> tuple[Any, ...]:
        return tuple(self.points)

    def GetConstrainedStatus(self) -> int:
        return 2


class FakeFeature:
    def __init__(self, sketch: FakeSketch) -> None:
        self.Name = "Sketch1"
        self._sketch = sketch

    def GetSpecificFeature2(self) -> FakeSketch:
        return self._sketch


class FakeSketchManager:
    def __init__(self, sketch: FakeSketch, select: Callable[[Any, bool], None]) -> None:
        self.ActiveSketch: FakeSketch | None = None
        self.AddToDB = False
        self.DisplayWhenAdded = True
        self._sketch = sketch
        self._select = select
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def InsertSketch(self, update_edit_rebuild: bool) -> None:
        self.ActiveSketch = self._sketch if self.ActiveSketch is None else None

    def CreateLine(self, *args: float) -> FakeSegment:
        self.calls.append(("CreateLine", args))
        segment = FakeSegment(len(self._sketch.segments) + 1, self._select)
        self._sketch.segments.append(segment)
        return segment

    def CreateCircleByRadius(self, *args: float) -> FakeSegment:
        self.calls.append(("CreateCircleByRadius", args))
        segment = FakeSegment(len(self._sketch.segments) + 1, self._select)
        self._sketch.segments.append(segment)
        return segment


class FakeModel:
    def __init__(self) -> None:
        self.sketch = FakeSketch()
        self.selected: list[Any] = []
        self.SketchManager = FakeSketchManager(self.sketch, self._select)
        self.Extension = self
        self.specific_dimension_types: list[int] = []
        self.feature = FakeFeature(self.sketch)

    def _select(self, entity: Any, append: bool) -> None:
        if not append:
            self.selected.clear()
        self.selected.append(entity)

    def ClearSelection2(self, clear_all: bool) -> None:
        self.selected.clear()

    def _add_dimension(self, relation_type: int) -> FakeDisplayDimension:
        dimension = FakeDimension()
        display = FakeDisplayDimension(dimension)
        self.sketch.RelationManager.relations.append(
            FakeRelation(relation_type, tuple(self.selected), display)
        )
        return display

    def AddSpecificDimension(
        self,
        x: float,
        y: float,
        z: float,
        dimension_type: int,
        error: Any,
    ) -> FakeDisplayDimension:
        self.specific_dimension_types.append(dimension_type)
        error.value = 0
        relation_type = {3: 2, 5: 3, 6: 15, 11: 1, 12: 1}[dimension_type]
        return self._add_dimension(relation_type)

    def FeatureByName(self, name: str) -> FakeFeature | None:
        return self.feature if name == self.feature.Name else None


class FakeApp:
    def __init__(self) -> None:
        self.input_dim_value_on_create = True
        self.preference_writes: list[tuple[int, bool]] = []

    def GetUserPreferenceToggle(self, preference: int) -> bool:
        return self.input_dim_value_on_create

    def SetUserPreferenceToggle(self, preference: int, value: bool) -> bool:
        self.input_dim_value_on_create = bool(value)
        self.preference_writes.append((preference, bool(value)))
        return True


class SketchNativeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = FakeModel()
        self.app = FakeApp()
        self.plane_calls: list[SketchPlane] = []

        def executor(
            operation: Callable[[Any], T],
            *,
            stage: str,
            mutation: bool,
        ) -> T:
            return operation(self.app)

        def resolver(app: Any, target: DocumentTarget) -> NativeSketchBinding:
            return NativeSketchBinding(
                model=self.model,
                document_id=target.document_id,
                revision=target.expected_revision,
                units=target.expected_units,
            )

        def plane_selector(model: Any, plane: SketchPlane) -> None:
            self.plane_calls.append(plane)

        def feature_resolver(model: Any, sketch: Any, requested_name: str) -> FakeFeature:
            return self.model.feature

        def plane_resolver(model: Any, sketch: Any, sketch_id: str) -> SketchPlane:
            return SketchPlane(PlaneKind.FRONT)

        def member(obj: Any, name: str, *args: object) -> Any:
            value = getattr(obj, name)
            if args:
                return value(*args)
            return value() if callable(value) else value

        self.runtime = SketchNativeRuntime(
            executor=executor,
            binding_resolver=resolver,
            plane_selector=plane_selector,
            sketch_feature_resolver=feature_resolver,
            plane_resolver=plane_resolver,
            member=member,
            rebuild_verifier=lambda model: RebuildResult(ok=True),
        )
        self.target = DocumentTarget("part-native", expected_revision=1, expected_units="mm")

    def test_geometry_creation_converts_mm_to_m_and_reads_native_segments(self) -> None:
        definition = SketchDefinition(
            name="NativeSketch",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(25.0, 10.0), construction=True),
                Circle(Point2D(5.0, 5.0), 2.5),
            ),
        )

        snapshot = SketchService(self.runtime).create(self.target, definition)

        line_call = self.model.SketchManager.calls[0]
        circle_call = self.model.SketchManager.calls[1]
        self.assertEqual("CreateLine", line_call[0])
        self.assertEqual((0.0, 0.0, 0.0, 0.025, 0.01, 0.0), line_call[1])
        self.assertEqual("CreateCircleByRadius", circle_call[0])
        self.assertEqual((0.005, 0.005, 0.0, 0.0025), circle_call[1])
        self.assertTrue(self.model.sketch.segments[0].ConstructionGeometry)
        self.assertEqual(2, snapshot.entity_count)
        self.assertEqual(("1:0", "2:0"), snapshot.entity_ids)
        self.assertEqual([SketchPlane(PlaneKind.FRONT)], self.plane_calls)

    def test_sketch_query_uses_persistent_plane_resolver_without_create_cache(self) -> None:
        self.model.feature.Name = "PersistedSketch"
        self.model.sketch.segments.append(FakeSegment(7))
        self.model.sketch.points.extend((FakePoint(8, 1), FakePoint(9, 2), FakePoint(10, 9)))
        document = self.runtime.resolve_document(self.target)

        snapshot = self.runtime.get_sketch(document, "PersistedSketch")

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(SketchPlane(PlaneKind.FRONT), snapshot.plane)
        self.assertEqual(2, snapshot.entity_count)
        self.assertEqual(("7:0", "8:1"), snapshot.entity_ids)

    def test_native_relation_create_query_delete_uses_typed_entity_identity(self) -> None:
        definition = SketchDefinition(
            name="Relations",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),
                LineSegment(Point2D(0.0, 5.0), Point2D(10.0, 5.0)),
            ),
            constraints=(HorizontalConstraint(0), ParallelConstraint(0, 1)),
        )

        snapshot = SketchService(self.runtime).create(self.target, definition)
        relations = SketchService(self.runtime).list_relations(self.target, snapshot.sketch_id)

        self.assertEqual(2, snapshot.constraint_count)
        self.assertEqual(("horizontal", "parallel"), tuple(item.relation_type for item in relations))
        self.assertEqual(("1:0",), relations[0].entity_ids)
        self.assertEqual(("1:0", "2:0"), relations[1].entity_ids)
        self.assertTrue(all(item.relation_id for item in relations))

        remaining = SketchService(self.runtime).delete_relation(
            self.target,
            snapshot.sketch_id,
            relations[0].relation_id,
        )
        self.assertEqual(("parallel",), tuple(item.relation_type for item in remaining))

    def test_native_relation_failure_is_normalized_and_exits_sketch_edit_mode(self) -> None:
        def reject_relation(entities: tuple[Any, ...], relation_type: int) -> FakeRelation:
            raise RuntimeError("simulated COM relation rejection")

        self.model.sketch.RelationManager.AddRelation = reject_relation  # type: ignore[method-assign]
        definition = SketchDefinition(
            name="BadRelation",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 3.0)),),
            constraints=(HorizontalConstraint(0),),
        )
        document = self.runtime.resolve_document(self.target)

        with self.assertRaisesRegex(NativeSketchUnsupportedError, "duplicate or incompatible"):
            self.runtime.create_sketch(document, definition)

        self.assertIsNone(self.model.SketchManager.ActiveSketch)

    def test_native_dimensions_create_query_and_edit_in_system_units(self) -> None:
        definition = SketchDefinition(
            name="Dimensions",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                LineSegment(Point2D(0.0, 0.0), Point2D(0.0, 20.0)),
                Circle(Point2D(10.0, 10.0), 4.0),
            ),
            dimensions=(
                DistanceDimension("width", 0, 20.0),
                RadiusDimension("radius", 2, 4.0),
                DiameterDimension("diameter", 2, 8.0, driving=False),
                AngularDimension("angle", 0, 1, 90.0),
            ),
        )

        snapshot = SketchService(self.runtime).create(self.target, definition)

        self.assertEqual(
            {"width": 20.0, "radius": 4.0, "diameter": 8.0, "angle": 90.0},
            dict(snapshot.dimension_values),
        )
        self.assertEqual(("mm", "mm", "mm", "deg"), tuple(item.unit for item in snapshot.dimensions))
        self.assertEqual((True, True, False, True), tuple(item.driving for item in snapshot.dimensions))
        self.assertEqual([11, 5, 6, 3], self.model.specific_dimension_types)
        self.assertTrue(self.app.input_dim_value_on_create)
        self.assertEqual([(10, False), (10, True)], self.app.preference_writes)

        updated = SketchService(self.runtime).set_dimension_value(
            self.target,
            snapshot.sketch_id,
            "width",
            25.0,
            unit="mm",
        )
        self.assertEqual(25.0, updated.value)
        self.assertAlmostEqual(0.025, self._dimension("width").value)

        angle = SketchService(self.runtime).set_dimension_value(
            self.target,
            snapshot.sketch_id,
            "angle",
            45.0,
            unit="deg",
        )
        self.assertEqual(45.0, angle.value)
        self.assertAlmostEqual(0.7853981633974483, self._dimension("angle").value)

    def test_native_linear_dimension_rejects_diagonal_line_before_com_dimension_dispatch(self) -> None:
        definition = SketchDefinition(
            name="DiagonalDimension",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 5.0)),),
            dimensions=(DistanceDimension("diagonal", 0, 11.18),),
        )
        document = self.runtime.resolve_document(self.target)

        with self.assertRaisesRegex(NativeSketchUnsupportedError, "horizontal or vertical"):
            self.runtime.create_sketch(document, definition)

        self.assertEqual([], self.model.specific_dimension_types)

    def _dimension(self, name: str) -> FakeDimension:
        for relation in self.model.sketch.RelationManager.relations:
            if relation.display_dimension is None:
                continue
            dimension = relation.display_dimension.dimension
            if dimension.Name == name:
                return dimension
        raise AssertionError(f"dimension {name!r} not found")

    def test_native_adapter_fails_closed_for_unpromoted_unfix_relation(self) -> None:
        from cdt_solidworks.sketch.models import UnfixConstraint

        definition = SketchDefinition(
            name="Unfix",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),),
            constraints=(UnfixConstraint(0),),
        )
        document = self.runtime.resolve_document(self.target)

        with self.assertRaisesRegex(NativeSketchUnsupportedError, "unfix"):
            self.runtime.create_sketch(document, definition)


if __name__ == "__main__":
    unittest.main()
