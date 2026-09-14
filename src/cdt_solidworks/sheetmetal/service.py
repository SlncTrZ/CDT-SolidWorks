"""Sheet-metal service with typed parameter and native state acceptance gates."""

from __future__ import annotations

import math

from cdt_solidworks.body.runtime import DocumentTarget, FabricationRuntime, ResolvedDocument
from cdt_solidworks.sheetmetal.models import (
    BaseFlangeSpec,
    EdgeFlangeSpec,
    HemSpec,
    SheetMetalMutationResult,
    SheetMetalState,
    SketchedBendSpec,
)

_TOLERANCE_MM = 1e-6
_BOUNDARY_EDGE_SELECTORS = frozenset({"bbox:+x", "bbox:-x", "bbox:+y", "bbox:-y"})


class SheetMetalError(RuntimeError):
    """Base sheet-metal error."""


class SheetMetalValidationError(SheetMetalError):
    """Invalid input before dispatch."""


class SheetMetalContextError(SheetMetalError):
    """Invalid document/context for sheet-metal operation."""


class SheetMetalMutationError(SheetMetalError):
    """Mutation failed rebuild/read-back/persistence acceptance."""


class SheetMetalService:
    def __init__(self, runtime: FabricationRuntime) -> None:
        self._runtime = runtime

    def inspect(self, target: DocumentTarget) -> SheetMetalState:
        document = self._resolve_part(target)
        return self._runtime.get_sheet_metal_state(document)

    def create_base_flange(
        self, target: DocumentTarget, spec: BaseFlangeSpec
    ) -> SheetMetalMutationResult:
        self._validate_target(target)
        self._validate_base_flange(spec)
        document = self._resolve_part(target)
        receipt = self._runtime.create_base_flange(document, spec)
        if not receipt.feature_id.strip():
            raise SheetMetalMutationError("base flange returned an empty feature identity")
        self._require_rebuild(document)
        state = self._runtime.get_sheet_metal_state(document)
        if not state.is_sheet_metal:
            raise SheetMetalMutationError("base flange read-back is not sheet metal")
        self._require_close(state.thickness_mm, spec.thickness_mm, "thickness")
        self._require_close(state.bend_radius_mm, spec.bend_radius_mm, "bend radius")
        if state.k_factor is not None and not math.isclose(
            state.k_factor, spec.k_factor, rel_tol=0.0, abs_tol=1e-6
        ):
            raise SheetMetalMutationError(
                f"K-factor read-back mismatch: expected {spec.k_factor}, got {state.k_factor}"
            )
        self._require_persistence(document)
        return SheetMetalMutationResult(receipt.feature_id, state)

    def add_edge_flange(
        self, target: DocumentTarget, spec: EdgeFlangeSpec
    ) -> SheetMetalMutationResult:
        self._validate_target(target)
        self._validate_edge_flange(spec)
        document = self._resolve_part(target)
        before = self._runtime.get_sheet_metal_state(document)
        if not before.is_sheet_metal:
            raise SheetMetalContextError("edge flange requires an existing sheet-metal body")
        receipt = self._runtime.create_edge_flange(document, spec)
        if not receipt.feature_id.strip():
            raise SheetMetalMutationError("edge flange returned an empty feature identity")
        self._require_rebuild(document)
        after = self._runtime.get_sheet_metal_state(document)
        if not after.is_sheet_metal:
            raise SheetMetalMutationError("edge flange read-back lost sheet-metal state")
        if before.thickness_mm is not None:
            self._require_close(after.thickness_mm, before.thickness_mm, "thickness")
        self._require_persistence(document)
        return SheetMetalMutationResult(receipt.feature_id, after)

    def add_hem(self, target: DocumentTarget, spec: HemSpec) -> SheetMetalMutationResult:
        self._validate_target(target)
        self._validate_hem(spec)
        document = self._resolve_part(target)
        before = self._runtime.get_sheet_metal_state(document)
        if not before.is_sheet_metal:
            raise SheetMetalContextError("hem requires an existing sheet-metal body")
        if before.flattened:
            raise SheetMetalContextError("hem requires the formed sheet-metal state")
        receipt = self._runtime.create_hem(document, spec)
        if not receipt.feature_id.strip():
            raise SheetMetalMutationError("hem returned an empty feature identity")
        readback_length = receipt.parameters.get("length_mm")
        if readback_length is not None:
            self._require_close(float(readback_length), spec.length_mm, "hem length")
        self._require_rebuild(document)
        after = self._runtime.get_sheet_metal_state(document)
        if not after.is_sheet_metal or after.flattened:
            raise SheetMetalMutationError("hem read-back lost formed sheet-metal state")
        if before.thickness_mm is not None:
            self._require_close(after.thickness_mm, before.thickness_mm, "thickness")
        self._require_persistence(document)
        return SheetMetalMutationResult(receipt.feature_id, after)

    def add_sketched_bend(
        self, target: DocumentTarget, spec: SketchedBendSpec
    ) -> SheetMetalMutationResult:
        self._validate_target(target)
        self._validate_sketched_bend(spec)
        document = self._resolve_part(target)
        before = self._runtime.get_sheet_metal_state(document)
        if not before.is_sheet_metal or before.flattened:
            raise SheetMetalContextError("sketched bend requires the formed sheet-metal state")
        receipt = self._runtime.create_sketched_bend(document, spec)
        if not receipt.feature_id.strip():
            raise SheetMetalMutationError("sketched bend returned an empty feature identity")
        readback_angle = receipt.parameters.get("angle_deg")
        readback_radius = receipt.parameters.get("bend_radius_mm")
        if readback_angle is not None:
            self._require_close(float(readback_angle), spec.angle_deg, "sketched bend angle")
        if readback_radius is not None:
            self._require_close(float(readback_radius), spec.bend_radius_mm, "sketched bend radius")
        self._require_rebuild(document)
        after = self._runtime.get_sheet_metal_state(document)
        if not after.is_sheet_metal or after.flattened:
            raise SheetMetalMutationError("sketched bend read-back lost formed sheet-metal state")
        if before.thickness_mm is not None:
            self._require_close(after.thickness_mm, before.thickness_mm, "thickness")
        self._require_persistence(document)
        return SheetMetalMutationResult(receipt.feature_id, after)

    def set_flattened(
        self, target: DocumentTarget, flattened: bool
    ) -> SheetMetalMutationResult:
        self._validate_target(target)
        document = self._resolve_part(target)
        before = self._runtime.get_sheet_metal_state(document)
        if not before.is_sheet_metal:
            raise SheetMetalContextError("flatten/unflatten requires a sheet-metal body")
        receipt = self._runtime.set_flattened(document, bool(flattened))
        if not receipt.feature_id.strip():
            raise SheetMetalMutationError("flatten mutation returned an empty feature identity")
        self._require_rebuild(document)
        state = self._runtime.get_sheet_metal_state(document)
        if state.flattened is not bool(flattened):
            raise SheetMetalMutationError("flat-pattern state read-back mismatch")
        if flattened and not state.flat_pattern_id:
            raise SheetMetalMutationError("flattened state must expose flat-pattern identity")
        self._require_persistence(document)
        return SheetMetalMutationResult(receipt.feature_id, state)

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise SheetMetalValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise SheetMetalValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise SheetMetalValidationError("sheet-metal dimensions currently require millimeter units")

    @staticmethod
    def _validate_base_flange(spec: BaseFlangeSpec) -> None:
        if not spec.name.strip() or not spec.profile_id.strip():
            raise SheetMetalValidationError("base flange name/profile must not be empty")
        SheetMetalService._positive(spec.thickness_mm, "thickness")
        SheetMetalService._positive(spec.bend_radius_mm, "bend radius")
        if not math.isfinite(spec.k_factor) or not 0.0 < spec.k_factor < 1.0:
            raise SheetMetalValidationError("K-factor must be finite and in the range (0, 1)")

    @staticmethod
    def _validate_edge_flange(spec: EdgeFlangeSpec) -> None:
        if not spec.name.strip():
            raise SheetMetalValidationError("edge flange name must not be empty")
        if spec.edge_id not in _BOUNDARY_EDGE_SELECTORS:
            raise SheetMetalValidationError(
                "edge flange edge selector must be one of bbox:+x, bbox:-x, bbox:+y, bbox:-y"
            )
        SheetMetalService._positive(spec.length_mm, "edge flange length")
        if not math.isfinite(spec.angle_deg) or not 0.0 < spec.angle_deg < 180.0:
            raise SheetMetalValidationError("edge flange angle must be finite and in the range (0, 180)")
        if spec.bend_radius_mm is not None:
            SheetMetalService._positive(spec.bend_radius_mm, "edge flange bend radius")

    @staticmethod
    def _validate_sketched_bend(spec: SketchedBendSpec) -> None:
        if not spec.name.strip():
            raise SheetMetalValidationError("sketched bend name must not be empty")
        if not math.isfinite(spec.line_x_mm):
            raise SheetMetalValidationError("sketched bend line_x_mm must be finite")
        if not math.isfinite(spec.angle_deg) or not 0.0 < spec.angle_deg < 180.0:
            raise SheetMetalValidationError("sketched bend angle must be finite and in the range (0, 180)")
        SheetMetalService._positive(spec.bend_radius_mm, "sketched bend radius")

    @staticmethod
    def _validate_hem(spec: HemSpec) -> None:
        if not spec.name.strip():
            raise SheetMetalValidationError("hem name must not be empty")
        if spec.edge_id not in _BOUNDARY_EDGE_SELECTORS:
            raise SheetMetalValidationError(
                "hem edge selector must be one of bbox:+x, bbox:-x, bbox:+y, bbox:-y"
            )
        SheetMetalService._positive(spec.length_mm, "hem length")
        SheetMetalService._positive(spec.gap_mm, "hem gap")
        if spec.position not in {"inside", "outside"}:
            raise SheetMetalValidationError("hem position must be 'inside' or 'outside'")

    @staticmethod
    def _positive(value: float, label: str) -> None:
        if not math.isfinite(value) or value <= 0:
            raise SheetMetalValidationError(f"{label} must be positive and finite")

    def _resolve_part(self, target: DocumentTarget) -> ResolvedDocument:
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise SheetMetalContextError("resolved document identity does not match request")
        if document.revision != target.expected_revision:
            raise SheetMetalContextError("stale document context")
        if document.document_type.lower() != "part":
            raise SheetMetalContextError("sheet-metal operations require a part document")
        if document.units != target.expected_units:
            raise SheetMetalContextError("document units changed before sheet-metal mutation")
        return document

    def _require_rebuild(self, document: ResolvedDocument) -> None:
        result = self._runtime.rebuild(document)
        if not result.ok:
            raise SheetMetalMutationError(
                f"rebuild failed after sheet-metal mutation: {result.error_code or result.message or 'unknown'}"
            )

    def _require_persistence(self, document: ResolvedDocument) -> None:
        result = self._runtime.persist_and_reopen(document)
        if not result.ok:
            raise SheetMetalMutationError(
                f"save/reopen failed after sheet-metal mutation: {result.message or 'unknown error'}"
            )

    @staticmethod
    def _require_close(actual: float | None, expected: float, label: str) -> None:
        if actual is None or not math.isclose(actual, expected, rel_tol=0.0, abs_tol=_TOLERANCE_MM):
            raise SheetMetalMutationError(
                f"{label} read-back mismatch: expected {expected}, got {actual}"
            )
