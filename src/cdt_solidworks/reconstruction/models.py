"""Typed reconstruction contracts for bounded neutral-CAD and mesh workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class SourceClass(str, Enum):
    NEUTRAL_CAD = "neutral_cad"
    NATIVE_IMPORTED_BODY = "native_imported_body"
    MESH = "mesh"
    MIXED_DEGRADED = "mixed_degraded"
    INVALID = "invalid"


class ReconstructionStrategy(str, Enum):
    DIRECT_EDIT = "direct_edit"
    FEATURE_RECOGNITION = "feature_recognition"
    PARAMETRIC_REBUILD = "parametric_rebuild"
    APPROXIMATION = "approximation"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    path: str
    sha256: str
    source_format: str
    source_class: SourceClass
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PrimitiveFit:
    kind: str
    confidence: float
    fit_residual_mm: float | None = None
    parameters: Mapping[str, float | str | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconstructionAssessment:
    source: SourceIdentity
    strategy: ReconstructionStrategy
    unit: str | None
    unit_confidence: float
    frame_confidence: float
    body_count: int | None
    topology: Mapping[str, int | float | bool | str | None]
    mesh: Mapping[str, int | float | bool | str | None]
    primitives: tuple[PrimitiveFit, ...]
    recognized_class: str | None
    dimensions_mm: Mapping[str, float]
    missing_design_semantics: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DimensionLedgerEntry:
    name: str
    expected_mm: float
    actual_mm: float
    tolerance_mm: float
    deviation_mm: float
    within_tolerance: bool


@dataclass(frozen=True, slots=True)
class ReconstructionComparison:
    ledger: tuple[DimensionLedgerEntry, ...]
    max_deviation_mm: float
    within_tolerance: bool


@dataclass(frozen=True, slots=True)
class ReconstructionResult:
    source: SourceIdentity
    output_path: str
    benchmark_class: str
    strategy: ReconstructionStrategy
    editable_feature_ids: tuple[str, ...]
    dimension_ledger: tuple[DimensionLedgerEntry, ...]
    within_tolerance: bool
    reopen_verified: bool
    intended_edit_verified: bool | None
    drawing_handoff: Mapping[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
