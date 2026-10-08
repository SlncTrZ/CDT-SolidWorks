from __future__ import annotations

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.surface.native import SurfaceNativeAdapter


class NeverExecuteSession:
    def __init__(self) -> None:
        self.api = object()
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("validation failure must not dispatch")


def test_knit_rejects_tolerance_above_documented_range_before_dispatch(tmp_path) -> None:
    source = tmp_path / "surface.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = SurfaceNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.knit(source, surface_body_names=("S1", "S2"), tolerance_mm=0.2)

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_offset_rejects_nonpositive_distance_before_dispatch(tmp_path) -> None:
    source = tmp_path / "surface.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = SurfaceNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.offset(source, surface_body_name="S1", distance_mm=0.0)

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_thicken_rejects_nonpositive_thickness_before_dispatch(tmp_path) -> None:
    source = tmp_path / "surface.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = SurfaceNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.thicken(source, surface_body_name="S1", thickness_mm=0.0)

    assert result.state is NativeCallState.FAILURE
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0
