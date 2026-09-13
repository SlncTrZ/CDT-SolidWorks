"""Surface-domain service with deterministic rebuild/read-back/persistence acceptance."""

from __future__ import annotations

import math

from cdt_solidworks.body.models import BodyKind
from cdt_solidworks.body.runtime import DocumentTarget, FabricationRuntime, ResolvedDocument
from cdt_solidworks.surface.models import SurfaceKnitSpec, SurfaceMutationResult, ThickenSpec


class SurfaceError(RuntimeError):
    """Base surface-domain error."""


class SurfaceValidationError(SurfaceError):
    """Input is invalid before native dispatch."""


class SurfaceContextError(SurfaceError):
    """Resolved document/context is invalid."""


class SurfaceMutationError(SurfaceError):
    """Mutation failed rebuild/read-back/persistence gates."""


class SurfaceService:
    def __init__(self, runtime: FabricationRuntime) -> None:
        self._runtime = runtime

    def knit(self, target: DocumentTarget, spec: SurfaceKnitSpec) -> SurfaceMutationResult:
        self._validate_target(target)
        self._validate_knit(spec)
        document = self._resolve_part(target)
        before_surfaces = self._runtime.list_bodies(document, BodyKind.SURFACE)
        before_solids = self._runtime.list_bodies(document, BodyKind.SOLID)
        available = {body.body_id for body in before_surfaces}
        missing = [item for item in spec.surface_body_ids if item not in available]
        if missing:
            raise SurfaceContextError(f"surface body identity not found: {missing[0]}")

        receipt = self._runtime.knit_surfaces(document, spec)
        if not receipt.feature_id.strip():
            raise SurfaceMutationError("surface knit returned an empty feature identity")
        self._require_rebuild(document)
        after_surfaces = self._runtime.list_bodies(document, BodyKind.SURFACE)
        after_solids = self._runtime.list_bodies(document, BodyKind.SOLID)
        if spec.try_form_solid:
            if len(after_solids) <= len(before_solids):
                raise SurfaceMutationError("knit-to-solid read-back did not create a solid body")
            if len(after_surfaces) >= len(before_surfaces):
                raise SurfaceMutationError("knit-to-solid read-back did not consume surface bodies")
        self._require_persistence(document)
        return SurfaceMutationResult(receipt.feature_id, after_surfaces, after_solids)

    def thicken(self, target: DocumentTarget, spec: ThickenSpec) -> SurfaceMutationResult:
        self._validate_target(target)
        self._validate_thicken(spec)
        document = self._resolve_part(target)
        before_surfaces = self._runtime.list_bodies(document, BodyKind.SURFACE)
        before_solids = self._runtime.list_bodies(document, BodyKind.SOLID)
        if spec.surface_body_id not in {body.body_id for body in before_surfaces}:
            raise SurfaceContextError("surface body identity not found for thicken")
        receipt = self._runtime.thicken_surface(document, spec)
        if not receipt.feature_id.strip():
            raise SurfaceMutationError("thicken returned an empty feature identity")
        self._require_rebuild(document)
        after_surfaces = self._runtime.list_bodies(document, BodyKind.SURFACE)
        after_solids = self._runtime.list_bodies(document, BodyKind.SOLID)
        if spec.merge and before_solids:
            if len(after_solids) < len(before_solids):
                raise SurfaceMutationError("merged thicken unexpectedly reduced solid-body count")
        elif len(after_solids) <= len(before_solids):
            raise SurfaceMutationError("thicken read-back did not create a solid body")
        self._require_persistence(document)
        return SurfaceMutationResult(receipt.feature_id, after_surfaces, after_solids)

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise SurfaceValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise SurfaceValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise SurfaceValidationError("surface dimensions currently require millimeter units")

    @staticmethod
    def _validate_knit(spec: SurfaceKnitSpec) -> None:
        if not spec.name.strip():
            raise SurfaceValidationError("surface knit feature name must not be empty")
        if len(spec.surface_body_ids) < 2 or len(set(spec.surface_body_ids)) != len(spec.surface_body_ids):
            raise SurfaceValidationError("surface knit requires at least two unique surface body identities")
        if any(not item.strip() for item in spec.surface_body_ids):
            raise SurfaceValidationError("surface body identities must not be empty")
        if not math.isfinite(spec.tolerance_mm) or spec.tolerance_mm <= 0:
            raise SurfaceValidationError("knit tolerance must be positive and finite")

    @staticmethod
    def _validate_thicken(spec: ThickenSpec) -> None:
        if not spec.name.strip() or not spec.surface_body_id.strip():
            raise SurfaceValidationError("thicken name and surface body identity must not be empty")
        if not math.isfinite(spec.thickness_mm) or spec.thickness_mm <= 0:
            raise SurfaceValidationError("thicken thickness must be positive and finite")
        if spec.side not in {0, 1, 2}:
            raise SurfaceValidationError("thicken side must be 0, 1, or 2")

    def _resolve_part(self, target: DocumentTarget) -> ResolvedDocument:
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise SurfaceContextError("resolved document identity does not match request")
        if document.revision != target.expected_revision:
            raise SurfaceContextError("stale document context")
        if document.document_type.lower() != "part":
            raise SurfaceContextError("surface operations require a part document")
        if document.units != target.expected_units:
            raise SurfaceContextError("document units changed before surface mutation")
        return document

    def _require_rebuild(self, document: ResolvedDocument) -> None:
        result = self._runtime.rebuild(document)
        if not result.ok:
            raise SurfaceMutationError(
                f"rebuild failed after surface mutation: {result.error_code or result.message or 'unknown'}"
            )

    def _require_persistence(self, document: ResolvedDocument) -> None:
        result = self._runtime.persist_and_reopen(document)
        if not result.ok:
            raise SurfaceMutationError(
                f"save/reopen failed after surface mutation: {result.message or 'unknown error'}"
            )
