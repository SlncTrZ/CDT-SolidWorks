"""Typed sketch-domain models for lane C."""

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


@dataclass(frozen=True, slots=True)
class Circle:
    center: Point2D
    radius_mm: float


SketchEntity: TypeAlias = LineSegment | Circle


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


SketchConstraint: TypeAlias = HorizontalConstraint | VerticalConstraint | CoincidentConstraint


@dataclass(frozen=True, slots=True)
class DistanceDimension:
    name: str
    entity_index: int
    value_mm: float


@dataclass(frozen=True, slots=True)
class DiameterDimension:
    name: str
    entity_index: int
    value_mm: float


SketchDimension: TypeAlias = DistanceDimension | DiameterDimension


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
class SketchSnapshot:
    sketch_id: str
    plane: SketchPlane
    entity_count: int
    constraint_count: int
    dimension_values_mm: Mapping[str, float] = field(default_factory=dict)
    definition_state: DefinitionState = DefinitionState.UNKNOWN
