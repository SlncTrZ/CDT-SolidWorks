from __future__ import annotations

from cdt_solidworks.body.models import BodyKind, BodySnapshot, CombineOperation, CombineSpec, MutationReceipt
from cdt_solidworks.body.runtime import DocumentTarget, PersistenceResult, RebuildResult, ResolvedDocument
from cdt_solidworks.body.service import BodyContextError, BodyMutationError, BodyService, BodyValidationError


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.document = ResolvedDocument("part-1", 4, "part", "mm", "Default")
        self.before = (
            BodySnapshot("b1", "Body1", BodyKind.SOLID),
            BodySnapshot("b2", "Body2", BodyKind.SOLID),
            BodySnapshot("b3", "Body3", BodyKind.SOLID),
        )
        self.after = (BodySnapshot("b1", "Body1", BodyKind.SOLID), BodySnapshot("b3", "Body3", BodyKind.SOLID))
        self.mutated = False
        self.rebuild_result = RebuildResult(True)
        self.persistence = PersistenceResult(True, 5)

    def resolve_document(self, target):
        self.calls.append("resolve")
        return self.document

    def list_bodies(self, document, kind=None):
        self.calls.append("list")
        return self.after if self.mutated else self.before

    def combine_bodies(self, document, spec):
        self.calls.append("combine")
        self.mutated = True
        return MutationReceipt("Combine1", {"operation": spec.operation.value})

    def rebuild(self, document):
        self.calls.append("rebuild")
        return self.rebuild_result

    def persist_and_reopen(self, document):
        self.calls.append("persist")
        return self.persistence


def target() -> DocumentTarget:
    return DocumentTarget("part-1", 4, "mm")


def test_subtract_requires_main_body_and_accepts_rebuild_readback_persistence() -> None:
    runtime = FakeRuntime()
    service = BodyService(runtime)
    result = service.combine(target(), CombineSpec("Subtract", CombineOperation.SUBTRACT, ("b1", "b2"), "b1"))
    assert result.feature_id == "Combine1"
    assert len(result.bodies) == 2
    assert runtime.calls == ["resolve", "list", "combine", "rebuild", "list", "persist"]


def test_subtract_without_main_body_fails_before_dispatch() -> None:
    runtime = FakeRuntime()
    service = BodyService(runtime)
    try:
        service.combine(target(), CombineSpec("Subtract", CombineOperation.SUBTRACT, ("b1", "b2")))
    except BodyValidationError as exc:
        assert "main_body_id" in str(exc)
    else:
        raise AssertionError("expected BodyValidationError")
    assert runtime.calls == []


def test_missing_body_identity_fails_closed() -> None:
    runtime = FakeRuntime()
    service = BodyService(runtime)
    try:
        service.combine(target(), CombineSpec("Add", CombineOperation.ADD, ("b1", "missing")))
    except BodyContextError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("expected BodyContextError")
    assert "combine" not in runtime.calls


def test_rebuild_failure_is_not_success() -> None:
    runtime = FakeRuntime()
    runtime.rebuild_result = RebuildResult(False, "feature_error")
    service = BodyService(runtime)
    try:
        service.combine(target(), CombineSpec("Add", CombineOperation.ADD, ("b1", "b2")))
    except BodyMutationError as exc:
        assert "rebuild" in str(exc)
    else:
        raise AssertionError("expected BodyMutationError")
    assert "persist" not in runtime.calls


def test_stale_revision_rejected_before_mutation() -> None:
    runtime = FakeRuntime()
    runtime.document = ResolvedDocument("part-1", 5, "part", "mm")
    service = BodyService(runtime)
    try:
        service.combine(target(), CombineSpec("Add", CombineOperation.ADD, ("b1", "b2")))
    except BodyContextError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("expected BodyContextError")
    assert runtime.calls == ["resolve"]
