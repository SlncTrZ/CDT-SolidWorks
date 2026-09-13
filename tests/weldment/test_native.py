from __future__ import annotations

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.weldment.native import WeldmentNativeAdapter


class NeverExecuteSession:
    def __init__(self) -> None:
        self.api = object()
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("validation failure must not dispatch")


def test_profile_path_outside_allow_roots_fails_before_dispatch(tmp_path) -> None:
    docs = tmp_path / "docs"
    profiles = tmp_path / "profiles"
    outside = tmp_path / "outside"
    docs.mkdir()
    profiles.mkdir()
    outside.mkdir()
    part = docs / "frame.sldprt"
    part.write_bytes(b"fixture")
    profile = outside / "tube.sldlfp"
    profile.write_bytes(b"profile")
    session = NeverExecuteSession()
    adapter = WeldmentNativeAdapter(
        session,
        path_policy=DocumentPathPolicy((docs,)),
        profile_roots=(profiles,),
    )

    result = adapter.create_structural_member(
        part,
        sketch_feature_name="Sketch1",
        profile_path=profile,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "path_outside_allowed_root"
    assert session.calls == 0


def test_profile_must_be_sldlfp(tmp_path) -> None:
    docs = tmp_path / "docs"
    profiles = tmp_path / "profiles"
    docs.mkdir()
    profiles.mkdir()
    part = docs / "frame.sldprt"
    part.write_bytes(b"fixture")
    profile = profiles / "tube.txt"
    profile.write_text("profile")
    session = NeverExecuteSession()
    adapter = WeldmentNativeAdapter(
        session,
        path_policy=DocumentPathPolicy((docs,)),
        profile_roots=(profiles,),
    )

    result = adapter.create_structural_member(
        part,
        sketch_feature_name="Sketch1",
        profile_path=profile,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0
