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


def test_profile_configuration_must_not_be_whitespace(tmp_path) -> None:
    docs = tmp_path / "docs"
    profiles = tmp_path / "profiles"
    docs.mkdir()
    profiles.mkdir()
    part = docs / "frame.sldprt"
    part.write_bytes(b"fixture")
    profile = profiles / "tube.sldlfp"
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
        profile_configuration="   ",
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


class _Segment:
    def __init__(self, construction: bool) -> None:
        self.ConstructionGeometry = construction


class _SegmentApi:
    def _member(self, obj, name, *args):
        value = getattr(obj, name)
        if args:
            return value(*args)
        return value() if callable(value) else value


class _SegmentSession:
    def __init__(self) -> None:
        self.api = _SegmentApi()


def test_weldment_path_segments_exclude_construction_geometry(tmp_path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    adapter = WeldmentNativeAdapter(
        _SegmentSession(),
        path_policy=DocumentPathPolicy((tmp_path,)),
        profile_roots=(profiles,),
    )
    sketch = type(
        "Sketch",
        (),
        {"GetSketchSegments": lambda self: (_Segment(False), _Segment(True), _Segment(False))},
    )()

    segments = adapter._weldment_path_segments(sketch)

    assert len(segments) == 2
    assert all(segment.ConstructionGeometry is False for segment in segments)


def test_unconfigured_profile_roots_fail_create_before_dispatch(tmp_path) -> None:
    part = tmp_path / "frame.sldprt"
    part.write_bytes(b"fixture")
    profile = tmp_path / "tube.sldlfp"
    profile.write_bytes(b"profile")
    session = NeverExecuteSession()
    adapter = WeldmentNativeAdapter(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
        profile_roots=(),
    )

    result = adapter.create_structural_member(
        part, sketch_feature_name="Sketch1", profile_path=profile
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "path_policy_unconfigured"
    assert session.calls == 0
