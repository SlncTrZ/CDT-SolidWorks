"""Mechanical-90 lane-A runtime seam for part/sketch domain services.

The protocol exposes semantic operations only. Integration binds these methods to the
accepted shared native/session primitives without copying or bypassing that runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cdt_solidworks.part.models import (
        BodySnapshot,
        ChamferSpec,
        CircularPatternSpec,
        CutSpec,
        DraftSpec,
        ExtrudeSpec,
        FeatureSnapshot,
        FilletSpec,
        HoleSpec,
        HoleWizardSpec,
        LinearPatternSpec,
        LoftSpec,
        MirrorSpec,
        ReferenceAxisSpec,
        ReferencePlaneSpec,
        ReferencePointSpec,
        RevolveCutSpec,
        RevolveSpec,
        RibSpec,
        ShellSpec,
        SweepSpec,
    )
    from cdt_solidworks.sketch.models import (
        SketchDefinition,
        SketchDimensionSnapshot,
        SketchRelationSnapshot,
        SketchSnapshot,
    )


@dataclass(frozen=True, slots=True)
class DocumentTarget:
    """Explicit caller-supplied identity required before any mutation."""

    document_id: str
    expected_revision: int
    expected_units: str = "mm"
    expected_configuration: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedDocument:
    """Runtime-backed document context used for a single bounded operation."""

    document_id: str
    revision: int
    document_type: str
    units: str
    configuration: str | None = None


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """Opaque identity returned by one semantic native mutation dispatch."""

    object_id: str


@dataclass(frozen=True, slots=True)
class RebuildResult:
    """Normalized rebuild/error state read from the native runtime."""

    ok: bool
    error_code: str | None = None
    message: str | None = None


class PartSketchRuntime(Protocol):
    """Narrow semantic seam integration must bind to the accepted native runtime."""

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument: ...

    def create_sketch(
        self,
        document: ResolvedDocument,
        definition: SketchDefinition,
    ) -> MutationReceipt: ...

    def get_sketch(
        self,
        document: ResolvedDocument,
        sketch_id: str,
    ) -> SketchSnapshot | None: ...

    def list_sketch_relations(
        self,
        document: ResolvedDocument,
        sketch_id: str,
    ) -> tuple[SketchRelationSnapshot, ...]: ...

    def delete_sketch_relation(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        relation_id: str,
    ) -> MutationReceipt: ...

    def set_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
        value: float,
        unit: str,
    ) -> MutationReceipt: ...

    def get_sketch_dimension(
        self,
        document: ResolvedDocument,
        sketch_id: str,
        name: str,
    ) -> SketchDimensionSnapshot | None: ...

    def create_extrude(
        self,
        document: ResolvedDocument,
        spec: ExtrudeSpec,
    ) -> MutationReceipt: ...

    def create_cut(
        self,
        document: ResolvedDocument,
        spec: CutSpec,
    ) -> MutationReceipt: ...

    def create_revolve(
        self,
        document: ResolvedDocument,
        spec: RevolveSpec,
    ) -> MutationReceipt: ...

    def create_revolve_cut(
        self,
        document: ResolvedDocument,
        spec: RevolveCutSpec,
    ) -> MutationReceipt: ...

    def create_hole(
        self,
        document: ResolvedDocument,
        spec: HoleSpec,
    ) -> MutationReceipt: ...

    def create_hole_wizard(
        self,
        document: ResolvedDocument,
        spec: HoleWizardSpec,
    ) -> MutationReceipt: ...

    def create_fillet(
        self,
        document: ResolvedDocument,
        spec: FilletSpec,
    ) -> MutationReceipt: ...

    def create_chamfer(
        self,
        document: ResolvedDocument,
        spec: ChamferSpec,
    ) -> MutationReceipt: ...

    def create_shell(
        self,
        document: ResolvedDocument,
        spec: ShellSpec,
    ) -> MutationReceipt: ...

    def create_draft(
        self,
        document: ResolvedDocument,
        spec: DraftSpec,
    ) -> MutationReceipt: ...

    def create_rib(
        self,
        document: ResolvedDocument,
        spec: RibSpec,
    ) -> MutationReceipt: ...

    def create_linear_pattern(
        self,
        document: ResolvedDocument,
        spec: LinearPatternSpec,
    ) -> MutationReceipt: ...

    def create_circular_pattern(
        self,
        document: ResolvedDocument,
        spec: CircularPatternSpec,
    ) -> MutationReceipt: ...

    def create_mirror(
        self,
        document: ResolvedDocument,
        spec: MirrorSpec,
    ) -> MutationReceipt: ...

    def create_sweep(
        self,
        document: ResolvedDocument,
        spec: SweepSpec,
    ) -> MutationReceipt: ...

    def create_loft(
        self,
        document: ResolvedDocument,
        spec: LoftSpec,
    ) -> MutationReceipt: ...

    def create_reference_plane(
        self,
        document: ResolvedDocument,
        spec: ReferencePlaneSpec,
    ) -> MutationReceipt: ...

    def create_reference_axis(
        self,
        document: ResolvedDocument,
        spec: ReferenceAxisSpec,
    ) -> MutationReceipt: ...

    def create_reference_point(
        self,
        document: ResolvedDocument,
        spec: ReferencePointSpec,
    ) -> MutationReceipt: ...

    def rename_feature(
        self,
        document: ResolvedDocument,
        feature_id: str,
        new_name: str,
    ) -> MutationReceipt: ...

    def set_feature_suppressed(
        self,
        document: ResolvedDocument,
        feature_id: str,
        suppressed: bool,
    ) -> MutationReceipt: ...

    def set_feature_parameter(
        self,
        document: ResolvedDocument,
        feature_id: str,
        parameter: str,
        value: float,
    ) -> MutationReceipt: ...

    def rebuild(self, document: ResolvedDocument) -> RebuildResult: ...

    def get_feature(
        self,
        document: ResolvedDocument,
        feature_id: str,
    ) -> FeatureSnapshot | None: ...

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]: ...

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]: ...
