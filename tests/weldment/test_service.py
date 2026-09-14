from __future__ import annotations

from cdt_solidworks.body.models import MutationReceipt
from cdt_solidworks.body.runtime import DocumentTarget, PersistenceResult, RebuildResult, ResolvedDocument
from cdt_solidworks.weldment.models import (
    CutListItem,
    CutListPropertySpec,
    StructuralMemberSpec,
    WeldmentState,
)
from cdt_solidworks.weldment.service import (
    WeldmentMutationError,
    WeldmentService,
    WeldmentValidationError,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.document = ResolvedDocument("part-1", 4, "part", "mm")
        self.state = WeldmentState(False, 0, ())
        self.rebuild_result = RebuildResult(True)
        self.persistence = PersistenceResult(True, 5)
        self.calls: list[str] = []

    def resolve_document(self, target):
        self.calls.append("resolve")
        return self.document

    def get_weldment_state(self, document):
        self.calls.append("state")
        return self.state

    def create_structural_member(self, document, spec):
        self.calls.append("member")
        self.state = WeldmentState(
            True,
            1,
            (CutListItem("cut-1", "L40x40", 2, {"LENGTH": "500"}),),
        )
        return MutationReceipt("StructuralMember1")

    def set_cut_list_property(self, document, spec):
        self.calls.append("property")
        items = []
        for item in self.state.cut_items:
            properties = dict(item.properties)
            if item.item_id == spec.cut_list_id:
                properties[spec.property_name] = spec.value
            items.append(CutListItem(item.item_id, item.name, item.quantity, properties))
        self.state = WeldmentState(
            self.state.has_weldment,
            self.state.structural_member_count,
            tuple(items),
        )
        return MutationReceipt("CutListProperty", {"value": spec.value})

    def rebuild(self, document):
        self.calls.append("rebuild")
        return self.rebuild_result

    def persist_and_reopen(self, document):
        self.calls.append("persist")
        return self.persistence


def target() -> DocumentTarget:
    return DocumentTarget("part-1", 4, "mm")


def spec() -> StructuralMemberSpec:
    return StructuralMemberSpec(
        "Frame",
        ("seg-1", "seg-2"),
        r"C:\ProgramData\SOLIDWORKS\profiles\square tube.sldlfp",
    )


def test_structural_member_requires_cut_list_readback_and_persistence() -> None:
    runtime = FakeRuntime()
    result = WeldmentService(runtime).add_structural_member(target(), spec())
    assert result.feature_id == "StructuralMember1"
    assert result.state.structural_member_count == 1
    assert result.state.cut_items[0].quantity == 2
    assert runtime.calls == ["resolve", "state", "member", "rebuild", "state", "persist"]


def test_profile_path_is_required_before_dispatch() -> None:
    runtime = FakeRuntime()
    bad = StructuralMemberSpec("Frame", ("seg-1",), "")
    try:
        WeldmentService(runtime).add_structural_member(target(), bad)
    except WeldmentValidationError as exc:
        assert "profile" in str(exc)
    else:
        raise AssertionError("expected WeldmentValidationError")
    assert runtime.calls == []


def test_duplicate_path_identity_is_rejected() -> None:
    runtime = FakeRuntime()
    bad = StructuralMemberSpec("Frame", ("seg-1", "seg-1"), "profile.sldlfp")
    try:
        WeldmentService(runtime).add_structural_member(target(), bad)
    except WeldmentValidationError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("expected WeldmentValidationError")


def test_missing_cut_list_fails_acceptance() -> None:
    runtime = FakeRuntime()
    original = runtime.create_structural_member

    def no_cut_list(document, member_spec):
        receipt = original(document, member_spec)
        runtime.state = WeldmentState(True, 1, ())
        return receipt

    runtime.create_structural_member = no_cut_list  # type: ignore[method-assign]
    try:
        WeldmentService(runtime).add_structural_member(target(), spec())
    except WeldmentMutationError as exc:
        assert "cut-list" in str(exc)
    else:
        raise AssertionError("expected WeldmentMutationError")
    assert "persist" not in runtime.calls


def test_cut_list_property_requires_readback_and_persistence() -> None:
    runtime = FakeRuntime()
    runtime.state = WeldmentState(
        True,
        1,
        (CutListItem("cut-1", "L40x40", 2, {"LENGTH": "500"}),),
    )
    result = WeldmentService(runtime).set_cut_list_property(
        target(), CutListPropertySpec("cut-1", "DESCRIPTION", "FRAME RAIL")
    )
    assert result.state.cut_items[0].properties["DESCRIPTION"] == "FRAME RAIL"
    assert runtime.calls == ["resolve", "state", "property", "rebuild", "state", "persist"]


def test_cut_list_property_rejects_missing_item_before_mutation() -> None:
    runtime = FakeRuntime()
    runtime.state = WeldmentState(
        True,
        1,
        (CutListItem("cut-1", "L40x40", 2, {}),),
    )
    try:
        WeldmentService(runtime).set_cut_list_property(
            target(), CutListPropertySpec("missing", "DESCRIPTION", "FRAME RAIL")
        )
    except WeldmentValidationError as exc:
        assert "cut-list identity" in str(exc)
    else:
        raise AssertionError("expected WeldmentValidationError")
    assert "property" not in runtime.calls


def test_structural_member_rejects_whitespace_profile_configuration() -> None:
    runtime = FakeRuntime()
    bad = StructuralMemberSpec(
        "Frame",
        ("seg-1",),
        r"C:\\ProgramData\\SOLIDWORKS\\profiles\\square tube.sldlfp",
        profile_configuration="   ",
    )
    try:
        WeldmentService(runtime).add_structural_member(target(), bad)
    except WeldmentValidationError as exc:
        assert "configuration" in str(exc)
    else:
        raise AssertionError("expected WeldmentValidationError")
    assert runtime.calls == []
