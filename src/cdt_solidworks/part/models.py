"""Typed parametric part-domain models for Mechanical-90 lane A."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class FeatureKind(str, Enum):
    EXTRUDE = "extrude"
    CUT = "cut"
    REVOLVE = "revolve"
    REVOLVE_CUT = "revolve_cut"
    HOLE = "hole"
    FILLET = "fillet"
    CHAMFER = "chamfer"
    SHELL = "shell"
    DRAFT = "draft"
    RIB = "rib"
    LINEAR_PATTERN = "linear_pattern"
    CIRCULAR_PATTERN = "circular_pattern"
    MIRROR = "mirror"
    SWEEP = "sweep"
    LOFT = "loft"
    REFERENCE_PLANE = "reference_plane"
    REFERENCE_AXIS = "reference_axis"
    REFERENCE_POINT = "reference_point"


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
class RevolveCutSpec:
    name: str
    profile: ProfileRef
    axis_ref: str
    angle_deg: float


@dataclass(frozen=True, slots=True)
class HoleSpec:
    name: str
    diameter_mm: float
    face_ref: str = ""
    centers_mm: tuple[tuple[float, float], ...] = ()
    through_all: bool = False
    depth_mm: float | None = None


class HoleWizardSize(str, Enum):
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"
    M6 = "M6"


@dataclass(frozen=True, slots=True)
class HoleWizardSpec:
    name: str
    size: HoleWizardSize
    face_ref: str = "bbox:+z"
    center_mm: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True, slots=True)
class FilletSpec:
    name: str
    edge_refs: tuple[str, ...]
    radius_mm: float
    tangent_propagation: bool = False


@dataclass(frozen=True, slots=True)
class ChamferSpec:
    name: str
    edge_refs: tuple[str, ...]
    distance_mm: float
    angle_deg: float = 45.0


@dataclass(frozen=True, slots=True)
class ShellSpec:
    name: str
    face_refs: tuple[str, ...]
    thickness_mm: float
    outward: bool = False


@dataclass(frozen=True, slots=True)
class DraftSpec:
    name: str
    face_refs: tuple[str, ...]
    neutral_plane_ref: str
    angle_deg: float
    reverse_direction: bool = False


@dataclass(frozen=True, slots=True)
class RibSpec:
    name: str
    profile: ProfileRef
    thickness_mm: float
    both_sides: bool = True


@dataclass(frozen=True, slots=True)
class LinearPatternSpec:
    name: str
    seed_feature_ids: tuple[str, ...]
    direction_ref: str
    count: int
    spacing_mm: float
    geometry_pattern: bool = False


@dataclass(frozen=True, slots=True)
class CircularPatternSpec:
    name: str
    seed_feature_ids: tuple[str, ...]
    axis_ref: str
    count: int
    angle_deg: float = 360.0
    geometry_pattern: bool = False


@dataclass(frozen=True, slots=True)
class MirrorSpec:
    name: str
    seed_feature_ids: tuple[str, ...]
    mirror_ref: str
    geometry_pattern: bool = False


@dataclass(frozen=True, slots=True)
class SweepSpec:
    name: str
    profile: ProfileRef
    path: ProfileRef


@dataclass(frozen=True, slots=True)
class LoftSpec:
    name: str
    profiles: tuple[ProfileRef, ...]
    closed: bool = False


@dataclass(frozen=True, slots=True)
class ReferencePlaneSpec:
    name: str
    reference: str
    offset_mm: float = 0.0
    reverse_direction: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceAxisSpec:
    name: str
    first_ref: str
    second_ref: str


@dataclass(frozen=True, slots=True)
class ReferencePointSpec:
    name: str
    reference: str


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
    parameters: Mapping[str, float | str | bool | int] = field(default_factory=dict)
    suppressed: bool = False


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
