"""Body-domain service with explicit identity, rebuild, read-back, and persistence gates."""

from __future__ import annotations

import math

from cdt_solidworks.body.models import (
    BodyKind,
    BodyMutationResult,
    BodySnapshot,
    CombineOperation,
    CombineSpec,
    DeleteKeepBodiesSpec,
    MoveCopyBodySpec,
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

    def move_copy(self, target: DocumentTarget, spec: MoveCopyBodySpec) -> BodyMutationResult:
        self._validate_target(target)
        self._validate_move_copy(spec)
        document = self._resolve_part(target)
        before = self._runtime.list_bodies(document, BodyKind.SOLID)
        available = {body.body_id for body in before}
        missing = [body_id for body_id in spec.body_ids if body_id not in available]
        if missing:
            raise BodyContextError(f"move/copy body identity not found: {missing[0]}")
        receipt = self._runtime.move_copy_bodies(document, spec)
        if not receipt.feature_id.strip():
            raise BodyMutationError("move/copy mutation returned an empty feature identity")
        self._require_rebuild(document)
        after = self._runtime.list_bodies(document, BodyKind.SOLID)
        expected = len(before) + (len(spec.body_ids) * spec.copies if spec.copy else 0)
        if len(after) != expected:
            raise BodyMutationError(
                f"move/copy body-count read-back mismatch: expected {expected}, got {len(after)}"
            )
        self._require_persistence(document)
        return BodyMutationResult(receipt.feature_id, after)

    def delete_keep(
        self, target: DocumentTarget, spec: DeleteKeepBodiesSpec
    ) -> BodyMutationResult:
        self._validate_target(target)
        self._validate_delete_keep(spec)
        document = self._resolve_part(target)
        before = self._runtime.list_bodies(document, BodyKind.SOLID)
        available = {body.body_id for body in before}
        missing = [body_id for body_id in spec.body_ids if body_id not in available]
        if missing:
            raise BodyContextError(f"delete/keep body identity not found: {missing[0]}")
        receipt = self._runtime.delete_keep_bodies(document, spec)
        if not receipt.feature_id.strip():
            raise BodyMutationError("delete/keep mutation returned an empty feature identity")
        self._require_rebuild(document)
        after = self._runtime.list_bodies(document, BodyKind.SOLID)
        expected = len(spec.body_ids) if spec.keep else len(before) - len(spec.body_ids)
        if len(after) != expected:
            raise BodyMutationError(
                f"delete/keep body-count read-back mismatch: expected {expected}, got {len(after)}"
            )
        if spec.keep and {body.body_id for body in after} != set(spec.body_ids):
            raise BodyMutationError("keep-bodies read-back does not match requested body identities")
        self._require_persistence(document)
        return BodyMutationResult(receipt.feature_id, after)

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

    @staticmethod
    def _validate_move_copy(spec: MoveCopyBodySpec) -> None:
        if not spec.name.strip():
            raise BodyValidationError("move/copy feature name must not be empty")
        if not spec.body_ids or len(set(spec.body_ids)) != len(spec.body_ids):
            raise BodyValidationError("move/copy requires unique body identities")
        if any(not body_id.strip() for body_id in spec.body_ids):
            raise BodyValidationError("move/copy body identities must not be empty")
        if len(spec.translation_mm) != 3 or any(
            not math.isfinite(value) for value in spec.translation_mm
        ):
            raise BodyValidationError("move/copy translation must contain three finite values")
        if all(abs(value) <= 1e-12 for value in spec.translation_mm):
            raise BodyValidationError("move/copy translation must be non-zero")
        if spec.copy:
            if spec.copies < 1 or spec.copies > 100:
                raise BodyValidationError("copy count must be in the range 1..100")
        elif spec.copies != 1:
            raise BodyValidationError("move operations require copies=1")

    @staticmethod
    def _validate_delete_keep(spec: DeleteKeepBodiesSpec) -> None:
        if not spec.name.strip():
            raise BodyValidationError("delete/keep feature name must not be empty")
        if not spec.body_ids or len(set(spec.body_ids)) != len(spec.body_ids):
            raise BodyValidationError("delete/keep requires unique body identities")
        if any(not body_id.strip() for body_id in spec.body_ids):
            raise BodyValidationError("delete/keep body identities must not be empty")

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
