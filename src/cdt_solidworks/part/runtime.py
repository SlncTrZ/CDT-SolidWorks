"""Lane-C runtime seam for part/sketch domain services.

The protocol intentionally describes semantic operations only. Agent E can adapt the
accepted Agent-B native/document runtime to this seam without Agent C importing or
copying Agent-B implementation details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cdt_solidworks.part.models import (
        BodySnapshot,
        CutSpec,
        ExtrudeSpec,
        FeatureSnapshot,
        RevolveSpec,
    )
    from cdt_solidworks.sketch.models import SketchDefinition, SketchSnapshot


@dataclass(frozen=True, slots=True)
class DocumentTarget:
    """Explicit caller-supplied identity required before any mutation."""

    document_id: str
    expected_revision: int
    expected_units: str = "mm"


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
    """Narrow service seam Agent E must bind to the accepted native runtime."""

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

    def rebuild(self, document: ResolvedDocument) -> RebuildResult: ...

    def get_feature(
        self,
        document: ResolvedDocument,
        feature_id: str,
    ) -> FeatureSnapshot | None: ...

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]: ...

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]: ...
