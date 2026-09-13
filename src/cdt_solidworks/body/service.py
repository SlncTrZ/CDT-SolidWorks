"""Body-domain service with explicit identity, rebuild, read-back, and persistence gates."""

from __future__ import annotations

from cdt_solidworks.body.models import (
    BodyKind,
    BodyMutationResult,
    BodySnapshot,
    CombineOperation,
    CombineSpec,
)
from cdt_solidworks.body.runtime import DocumentTarget, FabricationRuntime, ResolvedDocument


class BodyError(RuntimeError):
    """Base body-domain error."""


class BodyValidationError(BodyError):
    """Input is invalid before native dispatch."""


class BodyContextError(BodyError):
    """Resolved document/context is unsafe for mutation."""


class BodyMutationError(BodyError):
    """Native mutation failed acceptance gates."""


class BodyService:
    def __init__(self, runtime: FabricationRuntime) -> None:
        self._runtime = runtime

    def inspect(self, target: DocumentTarget) -> tuple[BodySnapshot, ...]:
        document = self._resolve_part(target)
        return self._runtime.list_bodies(document)

    def combine(self, target: DocumentTarget, spec: CombineSpec) -> BodyMutationResult:
        self._validate_target(target)
        self._validate_combine(spec)
        document = self._resolve_part(target)
        before = self._runtime.list_bodies(document, BodyKind.SOLID)
        by_id = {body.body_id: body for body in before}
        missing = [body_id for body_id in spec.body_ids if body_id not in by_id]
        if missing:
            raise BodyContextError(f"combine body identity not found: {missing[0]}")

        receipt = self._runtime.combine_bodies(document, spec)
        if not receipt.feature_id.strip():
            raise BodyMutationError("combine mutation returned an empty feature identity")
        self._require_rebuild(document)

        after = self._runtime.list_bodies(document, BodyKind.SOLID)
        expected = len(before) - (len(spec.body_ids) - 1)
        if len(after) != expected:
            raise BodyMutationError(
                f"combine body-count read-back mismatch: expected {expected}, got {len(after)}"
            )
        self._require_persistence(document)
        return BodyMutationResult(feature_id=receipt.feature_id, bodies=after)

    @staticmethod
    def _validate_target(target: DocumentTarget) -> None:
        if not target.document_id.strip():
            raise BodyValidationError("document identity must not be empty")
        if target.expected_revision < 0:
            raise BodyValidationError("document revision must be non-negative")
        if target.expected_units != "mm":
            raise BodyValidationError("fabrication dimensions currently require millimeter units")

    @classmethod
    def _validate_combine(cls, spec: CombineSpec) -> None:
        if not spec.name.strip():
            raise BodyValidationError("combine feature name must not be empty")
        if len(spec.body_ids) < 2 or len(set(spec.body_ids)) != len(spec.body_ids):
            raise BodyValidationError("combine requires at least two unique body identities")
        if any(not body_id.strip() for body_id in spec.body_ids):
            raise BodyValidationError("combine body identities must not be empty")
        if spec.operation is CombineOperation.SUBTRACT:
            if spec.main_body_id is None or spec.main_body_id not in spec.body_ids:
                raise BodyValidationError("subtract combine requires main_body_id in body_ids")
        elif spec.main_body_id is not None:
            raise BodyValidationError("main_body_id is only valid for subtract combine")

    def _resolve_part(self, target: DocumentTarget) -> ResolvedDocument:
        self._validate_target(target)
        document = self._runtime.resolve_document(target)
        if document.document_id != target.document_id:
            raise BodyContextError("resolved document identity does not match request")
        if document.revision != target.expected_revision:
            raise BodyContextError(
                f"stale document context: expected revision {target.expected_revision}, got {document.revision}"
            )
        if document.document_type.lower() != "part":
            raise BodyContextError("body operations require a part document")
        if document.units != target.expected_units:
            raise BodyContextError("document units changed before mutation")
        return document

    def _require_rebuild(self, document: ResolvedDocument) -> None:
        result = self._runtime.rebuild(document)
        if not result.ok:
            detail = result.error_code or result.message or "unknown rebuild error"
            raise BodyMutationError(f"rebuild failed after body mutation: {detail}")

    def _require_persistence(self, document: ResolvedDocument) -> None:
        persisted = self._runtime.persist_and_reopen(document)
        if not persisted.ok:
            raise BodyMutationError(
                f"save/reopen failed after body mutation: {persisted.message or 'unknown error'}"
            )
