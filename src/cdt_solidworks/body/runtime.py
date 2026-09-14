"""Lane-B fabrication runtime seam shared by body/surface/sheet-metal/weldment services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cdt_solidworks.body.models import (
        BodyKind,
        BodySnapshot,
        CombineSpec,
        DeleteKeepBodiesSpec,
        MoveCopyBodySpec,
        MutationReceipt,
    )
    from cdt_solidworks.sheetmetal.models import (
        BaseFlangeSpec,
        EdgeFlangeSpec,
        FoldSpec,
        HemSpec,
        SheetMetalState,
        SketchedBendSpec,
        UnfoldSpec,
    )
    from cdt_solidworks.surface.models import OffsetSurfaceSpec, SurfaceKnitSpec, ThickenSpec
    from cdt_solidworks.weldment.models import (
        CutListPropertySpec,
        StructuralMemberSpec,
        WeldmentState,
        WeldmentTrimSpec,
    )


@dataclass(frozen=True, slots=True)
class DocumentTarget:
    document_id: str
    expected_revision: int
    expected_units: str = "mm"


@dataclass(frozen=True, slots=True)
class ResolvedDocument:
    document_id: str
    revision: int
    document_type: str
    units: str
    configuration: str | None = None


@dataclass(frozen=True, slots=True)
class RebuildResult:
    ok: bool
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    ok: bool
    reopened_revision: int | None = None
    message: str | None = None


class FabricationRuntime(Protocol):
    """Semantic runtime contract; integration binds it to the accepted COM/session runtime."""

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument: ...

    def list_bodies(
        self, document: ResolvedDocument, kind: BodyKind | None = None
    ) -> tuple[BodySnapshot, ...]: ...

    def combine_bodies(self, document: ResolvedDocument, spec: CombineSpec) -> MutationReceipt: ...

    def move_copy_bodies(self, document: ResolvedDocument, spec: MoveCopyBodySpec) -> MutationReceipt: ...

    def delete_keep_bodies(
        self, document: ResolvedDocument, spec: DeleteKeepBodiesSpec
    ) -> MutationReceipt: ...

    def knit_surfaces(self, document: ResolvedDocument, spec: SurfaceKnitSpec) -> MutationReceipt: ...

    def thicken_surface(self, document: ResolvedDocument, spec: ThickenSpec) -> MutationReceipt: ...

    def offset_surface(self, document: ResolvedDocument, spec: OffsetSurfaceSpec) -> MutationReceipt: ...

    def create_base_flange(self, document: ResolvedDocument, spec: BaseFlangeSpec) -> MutationReceipt: ...

    def create_edge_flange(self, document: ResolvedDocument, spec: EdgeFlangeSpec) -> MutationReceipt: ...

    def create_hem(self, document: ResolvedDocument, spec: HemSpec) -> MutationReceipt: ...

    def create_sketched_bend(
        self, document: ResolvedDocument, spec: SketchedBendSpec
    ) -> MutationReceipt: ...

    def unfold_sheet_metal(
        self, document: ResolvedDocument, spec: UnfoldSpec
    ) -> MutationReceipt: ...

    def fold_sheet_metal(
        self, document: ResolvedDocument, spec: FoldSpec
    ) -> MutationReceipt: ...

    def set_flattened(self, document: ResolvedDocument, flattened: bool) -> MutationReceipt: ...

    def get_sheet_metal_state(self, document: ResolvedDocument) -> SheetMetalState: ...

    def create_structural_member(
        self, document: ResolvedDocument, spec: StructuralMemberSpec
    ) -> MutationReceipt: ...

    def get_weldment_state(self, document: ResolvedDocument) -> WeldmentState: ...

    def set_cut_list_property(
        self, document: ResolvedDocument, spec: CutListPropertySpec
    ) -> MutationReceipt: ...

    def trim_weldment_member(
        self, document: ResolvedDocument, spec: WeldmentTrimSpec
    ) -> MutationReceipt: ...

    def rebuild(self, document: ResolvedDocument) -> RebuildResult: ...

    def persist_and_reopen(self, document: ResolvedDocument) -> PersistenceResult: ...
