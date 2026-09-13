"""Typed parametric part-domain models for lane C."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class FeatureKind(str, Enum):
    EXTRUDE = "extrude"
    CUT = "cut"
    REVOLVE = "revolve"


@dataclass(frozen=True, slots=True)
class ProfileRef:
    sketch_id: str


@dataclass(frozen=True, slots=True)
class ExtrudeSpec:
    name: str
    profile: ProfileRef
    depth_mm: float


@dataclass(frozen=True, slots=True)
class CutSpec:
    name: str
    profile: ProfileRef
    through_all: bool = False
    depth_mm: float | None = None


@dataclass(frozen=True, slots=True)
class RevolveSpec:
    name: str
    profile: ProfileRef
    axis_ref: str
    angle_deg: float


@dataclass(frozen=True, slots=True)
class Bounds3D:
    min_x_mm: float
    min_y_mm: float
    min_z_mm: float
    max_x_mm: float
    max_y_mm: float
    max_z_mm: float


@dataclass(frozen=True, slots=True)
class HoleFact:
    center_x_mm: float
    center_y_mm: float
    radius_mm: float
    through: bool = True


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    feature_id: str
    name: str
    kind: FeatureKind
    parameters: Mapping[str, float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BodySnapshot:
    body_id: str
    bounds: Bounds3D
    holes: tuple[HoleFact, ...] = ()


@dataclass(frozen=True, slots=True)
class PartPostconditions:
    body_count: int | None = None
    bounds: tuple[Bounds3D, ...] | None = None
    holes: tuple[HoleFact, ...] | None = None
    tolerance_mm: float = 1e-6


@dataclass(frozen=True, slots=True)
class PartMutationResult:
    feature: FeatureSnapshot
    bodies: tuple[BodySnapshot, ...]


@dataclass(frozen=True, slots=True)
class PartSnapshot:
    features: tuple[FeatureSnapshot, ...]
    bodies: tuple[BodySnapshot, ...]
