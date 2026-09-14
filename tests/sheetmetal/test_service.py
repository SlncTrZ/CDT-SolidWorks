from __future__ import annotations

from cdt_solidworks.body.models import MutationReceipt
from cdt_solidworks.body.runtime import DocumentTarget, PersistenceResult, RebuildResult, ResolvedDocument
from cdt_solidworks.sheetmetal.models import BaseFlangeSpec, EdgeFlangeSpec, SheetMetalState
from cdt_solidworks.sheetmetal.service import (
    SheetMetalContextError,
    SheetMetalMutationError,
    SheetMetalService,
    SheetMetalValidationError,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.document = ResolvedDocument("part-1", 4, "part", "mm")
        self.state = SheetMetalState(False, None, None, None, False, None)
        self.rebuild_result = RebuildResult(True)
        self.persistence = PersistenceResult(True, 5)
        self.calls: list[str] = []

    def resolve_document(self, target):
        self.calls.append("resolve")
        return self.document

    def create_base_flange(self, document, spec):
        self.calls.append("base")
        self.state = SheetMetalState(True, spec.thickness_mm, spec.bend_radius_mm, spec.k_factor, False, None)
        return MutationReceipt("BaseFlange1")

    def create_edge_flange(self, document, spec):
        self.calls.append("edge")
        return MutationReceipt("EdgeFlange1")

    def set_flattened(self, document, flattened):
        self.calls.append("flatten")
        self.state = SheetMetalState(True, 2.0, 1.0, 0.5, flattened, "Flat-Pattern1" if flattened else None)
        return MutationReceipt("Flat-Pattern1")

    def get_sheet_metal_state(self, document):
        self.calls.append("state")
        return self.state

    def rebuild(self, document):
        self.calls.append("rebuild")
        return self.rebuild_result

    def persist_and_reopen(self, document):
        self.calls.append("persist")
        return self.persistence


def target() -> DocumentTarget:
    return DocumentTarget("part-1", 4, "mm")


def test_base_flange_requires_parameter_readback_and_persistence() -> None:
    runtime = FakeRuntime()
    result = SheetMetalService(runtime).create_base_flange(
        target(), BaseFlangeSpec("BaseFlange1", "Sketch1", 2.0, 1.0, 0.42)
    )
    assert result.state.is_sheet_metal
    assert result.state.thickness_mm == 2.0
    assert runtime.calls == ["resolve", "base", "rebuild", "state", "persist"]


def test_invalid_k_factor_rejected_before_dispatch() -> None:
    runtime = FakeRuntime()
    try:
        SheetMetalService(runtime).create_base_flange(
            target(), BaseFlangeSpec("BaseFlange1", "Sketch1", 2.0, 1.0, 1.2)
        )
    except SheetMetalValidationError as exc:
        assert "K-factor" in str(exc)
    else:
        raise AssertionError("expected SheetMetalValidationError")
    assert runtime.calls == []


def test_edge_flange_requires_existing_sheet_metal_state() -> None:
    runtime = FakeRuntime()
    try:
        SheetMetalService(runtime).add_edge_flange(
            target(), EdgeFlangeSpec("Edge1", "edge-1", 25.0, 90.0, 1.0)
        )
    except SheetMetalContextError as exc:
        assert "existing sheet-metal" in str(exc)
    else:
        raise AssertionError("expected SheetMetalContextError")
    assert "edge" not in runtime.calls


def test_flatten_requires_flat_pattern_identity() -> None:
    runtime = FakeRuntime()
    runtime.state = SheetMetalState(True, 2.0, 1.0, 0.5, False, None)
    service = SheetMetalService(runtime)
    result = service.set_flattened(target(), True)
    assert result.state.flattened is True
    assert result.state.flat_pattern_id == "Flat-Pattern1"


def test_rebuild_failure_is_not_reported_as_success() -> None:
    runtime = FakeRuntime()
    runtime.rebuild_result = RebuildResult(False, "feature_error")
    try:
        SheetMetalService(runtime).create_base_flange(
            target(), BaseFlangeSpec("BaseFlange1", "Sketch1", 2.0, 1.0, 0.5)
        )
    except SheetMetalMutationError as exc:
        assert "rebuild" in str(exc)
    else:
        raise AssertionError("expected SheetMetalMutationError")
    assert "persist" not in runtime.calls
