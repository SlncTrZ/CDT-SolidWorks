"""Weldment service with explicit path/profile identity and cut-list read-back gates."""

from __future__ import annotations

from cdt_solidworks.body.runtime import DocumentTarget, FabricationRuntime, ResolvedDocument
from cdt_solidworks.weldment.models import (
    StructuralMemberSpec,
    WeldmentMutationResult,
    WeldmentState,
)


class WeldmentError(RuntimeError):
    """Base weldment-domain error."""


class WeldmentValidationError(WeldmentError):
    """Invalid input before native dispatch."""


class WeldmentContextError(WeldmentError):
    """Invalid document/context for weldment operation."""


class WeldmentMutationError(WeldmentError):
    """Mutation failed rebuild/read-back/persistence acceptance."""


class WeldmentService:
    def __init__(self, runtime: FabricationRuntime) -> None:
        self._runtime = runtime

    def inspect(self, target: DocumentTarget) -> WeldmentState:
        document = self._resolve_part(target)
        return self._runtime.get_weldment_state(document)

    def add_structural_member(
        self, target: DocumentTarget, spec: StructuralMemberSpec
    ) -> WeldmentMutationResult:
        self._validate_target(target)
        self._validate_structural_member(spec)
        document = self._resolve_part(target)
        before = self._runtime.get_weldment_state(document)
        receipt = self._runtime.create_structural_member(document, spec)
        if not receipt.feature_id.strip():
            raise WeldmentMutationError("structural member returned an empty feature identity")
        self._require_rebuild(document)
        after = self._runtime.get_weldment_state(document)
        if not after.has_weldment:
            raise WeldmentMutationError("weldment feature read-back is missing")
        if after.structural_member_count <= before.structural_member_count:
            raise WeldmentMutationError("structural member count did not increase after mutation")
        if not after.cut_items:
            raise WeldmentMutationError("weldment cut-list read-back is empty")
        if any(item.quantity <= 0 for item in after.cut_items):
            raise WeldmentMutationError("weldment cut-list contains a non-positive quantity")
        self._require_persistence(document)
        return WeldmentMutationResult(receipt.feature_id, after)

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise WeldmentValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise WeldmentValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise WeldmentValidationError("weldment dimensions currently require millimeter units")

    @staticmethod
    def _validate_structural_member(spec: StructuralMemberSpec) -> None:
        if not spec.name.strip() or not spec.group_name.strip():
            raise WeldmentValidationError("structural member name/group must not be empty")
        if not spec.profile_path.strip():
            raise WeldmentValidationError("structural member profile path must not be empty")
        if not spec.path_ids or len(set(spec.path_ids)) != len(spec.path_ids):
            raise WeldmentValidationError("structural member requires unique path identities")
        if any(not item.strip() for item in spec.path_ids):
            raise WeldmentValidationError("structural member path identities must not be empty")
        if spec.corner_treatment < 0:
            raise WeldmentValidationError("corner treatment must be non-negative")
        if spec.profile_configuration and not spec.profile_configuration.strip():
            raise WeldmentValidationError("profile configuration must not be whitespace-only")

    def _resolve_part(self, target: DocumentTarget) -> ResolvedDocument:
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise WeldmentContextError("resolved document identity does not match request")
        if document.revision != target.expected_revision:
            raise WeldmentContextError("stale document context")
        if document.document_type.lower() != "part":
            raise WeldmentContextError("weldment operations require a part document")
        if document.units != target.expected_units:
            raise WeldmentContextError("document units changed before weldment mutation")
        return document

    def _require_rebuild(self, document: ResolvedDocument) -> None:
        result = self._runtime.rebuild(document)
        if not result.ok:
            raise WeldmentMutationError(
                f"rebuild failed after weldment mutation: {result.error_code or result.message or 'unknown'}"
            )

    def _require_persistence(self, document: ResolvedDocument) -> None:
        result = self._runtime.persist_and_reopen(document)
        if not result.ok:
            raise WeldmentMutationError(
                f"save/reopen failed after weldment mutation: {result.message or 'unknown error'}"
            )
