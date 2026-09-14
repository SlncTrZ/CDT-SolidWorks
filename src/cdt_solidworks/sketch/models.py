"""Typed sketch-domain models for Mechanical-90 lane A."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, TypeAlias


@dataclass(frozen=True, slots=True)
class Point2D:
    x_mm: float
    y_mm: float


@dataclass(frozen=True, slots=True)
class LineSegment:
    start: Point2D
    end: Point2D
    construction: bool = False


@dataclass(frozen=True, slots=True)
class CenterLine:
    start: Point2D
    end: Point2D


@dataclass(frozen=True, slots=True)
class Circle:
    center: Point2D
    radius_mm: float
    construction: bool = False


class ArcDirection(int, Enum):
    CLOCKWISE = -1
    COUNTER_CLOCKWISE = 1


@dataclass(frozen=True, slots=True)
class Arc:
    center: Point2D
    start: Point2D
    end: Point2D
    direction: ArcDirection = ArcDirection.COUNTER_CLOCKWISE
    construction: bool = False


@dataclass(frozen=True, slots=True)
class Ellipse:
    center: Point2D
    major_axis_point: Point2D
    minor_axis_point: Point2D
    construction: bool = False


@dataclass(frozen=True, slots=True)
class SketchPoint:
    point: Point2D


@dataclass(frozen=True, slots=True)
class Spline:
    points: tuple[Point2D, ...]
    degree: int = 3
    construction: bool = False
    simulate_natural_ends: bool = False


SketchEntity: TypeAlias = LineSegment | CenterLine | Circle | Arc | Ellipse | SketchPoint | Spline


class PlaneKind(str, Enum):
    FRONT = "front"
    TOP = "top"
    RIGHT = "right"
    FACE = "face"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class SketchPlane:
    kind: PlaneKind
    reference_id: str | None = None


@dataclass(frozen=True, slots=True)
class HorizontalConstraint:
    entity_index: int


@dataclass(frozen=True, slots=True)
class VerticalConstraint:
    entity_index: int


@dataclass(frozen=True, slots=True)
class CoincidentConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class ConcentricConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class TangentConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class ParallelConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class PerpendicularConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class EqualConstraint:
    first_entity_index: int
    second_entity_index: int


@dataclass(frozen=True, slots=True)
class MidpointConstraint:
    point_entity_index: int
    target_entity_index: int


@dataclass(frozen=True, slots=True)
class SymmetricConstraint:
    first_entity_index: int
    second_entity_index: int
    symmetry_entity_index: int


@dataclass(frozen=True, slots=True)
class FixConstraint:
    entity_index: int


@dataclass(frozen=True, slots=True)
class UnfixConstraint:
    entity_index: int


SketchConstraint: TypeAlias = (
    HorizontalConstraint
    | VerticalConstraint
    | CoincidentConstraint
    | ConcentricConstraint
    | TangentConstraint
    | ParallelConstraint
    | PerpendicularConstraint
    | EqualConstraint
    | MidpointConstraint
    | SymmetricConstraint
    | FixConstraint
    | UnfixConstraint
)


@dataclass(frozen=True, slots=True)
class DimensionTolerance:
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class DistanceDimension:
    name: str
    entity_index: int
    value_mm: float
    driving: bool = True
    tolerance: DimensionTolerance | None = None

    @property
    def value_for_readback(self) -> float:
        return self.value_mm


@dataclass(frozen=True, slots=True)
class DiameterDimension:
    name: str
    entity_index: int
    value_mm: float
    driving: bool = True
    tolerance: DimensionTolerance | None = None

    @property
    def value_for_readback(self) -> float:
        return self.value_mm


@dataclass(frozen=True, slots=True)
class RadiusDimension:
    name: str
    entity_index: int
    value_mm: float
    driving: bool = True
    tolerance: DimensionTolerance | None = None

    @property
    def value_for_readback(self) -> float:
        return self.value_mm


@dataclass(frozen=True, slots=True)
class AngularDimension:
    name: str
    first_entity_index: int
    second_entity_index: int
    value_deg: float
    driving: bool = True
    tolerance: DimensionTolerance | None = None

    @property
    def value_for_readback(self) -> float:
        return self.value_deg


SketchDimension: TypeAlias = DistanceDimension | DiameterDimension | RadiusDimension | AngularDimension


class DefinitionState(str, Enum):
    UNDER_DEFINED = "under_defined"
    FULLY_DEFINED = "fully_defined"
    OVER_DEFINED = "over_defined"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SketchDefinition:
    name: str
    plane: SketchPlane
    entities: tuple[SketchEntity, ...]
    constraints: tuple[SketchConstraint, ...] = ()
    dimensions: tuple[SketchDimension, ...] = ()


@dataclass(frozen=True, slots=True)
class SketchRelationSnapshot:
    relation_id: str
    relation_type: str
    entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SketchDimensionSnapshot:
    name: str
    value: float
    unit: str
    driving: bool
    lower_tolerance: float | None = None
    upper_tolerance: float | None = None


@dataclass(frozen=True, slots=True)
class SketchSnapshot:
    sketch_id: str
    plane: SketchPlane
    entity_count: int
    constraint_count: int
    dimension_values_mm: Mapping[str, float] = field(default_factory=dict)
    definition_state: DefinitionState = DefinitionState.UNKNOWN
    entity_ids: tuple[str, ...] = ()
    relations: tuple[SketchRelationSnapshot, ...] = ()
    dimensions: tuple[SketchDimensionSnapshot, ...] = ()

    @property
    def dimension_values(self) -> Mapping[str, float]:
        """Backward-compatible read-back map; values use each dimension's declared unit."""
        return self.dimension_values_mm
