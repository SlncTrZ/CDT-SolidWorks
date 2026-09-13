from __future__ import annotations

from cdt_solidworks.body.models import BodyKind, BodySnapshot, MutationReceipt
from cdt_solidworks.body.runtime import DocumentTarget, PersistenceResult, RebuildResult, ResolvedDocument
from cdt_solidworks.surface.models import SurfaceKnitSpec, ThickenSpec
from cdt_solidworks.surface.service import SurfaceMutationError, SurfaceService, SurfaceValidationError


class FakeRuntime:
    def __init__(self) -> None:
        self.document = ResolvedDocument("part-1", 4, "part", "mm")
        self.surfaces_before = (
            BodySnapshot("s1", "Surface1", BodyKind.SURFACE),
            BodySnapshot("s2", "Surface2", BodyKind.SURFACE),
        )
        self.surfaces_after: tuple[BodySnapshot, ...] = ()
        self.solids_before: tuple[BodySnapshot, ...] = ()
        self.solids_after = (BodySnapshot("b1", "Solid1", BodyKind.SOLID),)
        self.mutated = False
        self.rebuild_result = RebuildResult(True)
        self.persistence = PersistenceResult(True, 5)
        self.calls: list[str] = []

    def resolve_document(self, target):
        self.calls.append("resolve")
        return self.document

    def list_bodies(self, document, kind=None):
        self.calls.append(f"list:{kind.value if kind else 'all'}")
        if kind is BodyKind.SURFACE:
            return self.surfaces_after if self.mutated else self.surfaces_before
        if kind is BodyKind.SOLID:
            return self.solids_after if self.mutated else self.solids_before
        return ()

    def knit_surfaces(self, document, spec):
        self.calls.append("knit")
        self.mutated = True
        return MutationReceipt("Knit1")

    def thicken_surface(self, document, spec):
        self.calls.append("thicken")
        self.mutated = True
        return MutationReceipt("Thicken1")

    def rebuild(self, document):
        self.calls.append("rebuild")
        return self.rebuild_result

    def persist_and_reopen(self, document):
        self.calls.append("persist")
        return self.persistence


def target() -> DocumentTarget:
    return DocumentTarget("part-1", 4, "mm")


def test_knit_to_solid_requires_surface_consumption_and_solid_readback() -> None:
    runtime = FakeRuntime()
    result = SurfaceService(runtime).knit(target(), SurfaceKnitSpec("Knit1", ("s1", "s2")))
    assert result.feature_id == "Knit1"
    assert len(result.solid_bodies) == 1
    assert runtime.calls[-2:] == ["list:solid", "persist"]


def test_knit_requires_two_unique_surface_bodies() -> None:
    runtime = FakeRuntime()
    try:
        SurfaceService(runtime).knit(target(), SurfaceKnitSpec("Knit1", ("s1", "s1")))
    except SurfaceValidationError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("expected SurfaceValidationError")
    assert runtime.calls == []


def test_thicken_requires_positive_thickness() -> None:
    runtime = FakeRuntime()
    try:
        SurfaceService(runtime).thicken(target(), ThickenSpec("Thicken1", "s1", 0.0))
    except SurfaceValidationError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected SurfaceValidationError")
    assert runtime.calls == []


def test_thicken_rebuild_failure_does_not_persist() -> None:
    runtime = FakeRuntime()
    runtime.rebuild_result = RebuildResult(False, "zero_thickness")
    try:
        SurfaceService(runtime).thicken(target(), ThickenSpec("Thicken1", "s1", 2.0))
    except SurfaceMutationError as exc:
        assert "rebuild" in str(exc)
    else:
        raise AssertionError("expected SurfaceMutationError")
    assert "persist" not in runtime.calls


def test_merged_thicken_accepts_stable_solid_count() -> None:
    runtime = FakeRuntime()
    existing = BodySnapshot("b0", "Existing", BodyKind.SOLID)
    runtime.solids_before = (existing,)
    runtime.solids_after = (existing,)

    result = SurfaceService(runtime).thicken(
        target(), ThickenSpec("Thicken1", "s1", 2.0, merge=True)
    )

    assert result.feature_id == "Thicken1"
    assert len(result.solid_bodies) == 1
    assert runtime.calls[-1] == "persist"
