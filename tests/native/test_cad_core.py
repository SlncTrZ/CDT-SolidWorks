from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState


class _NeverExecuteSession:
    def __init__(self) -> None:
        self.api = object()
        self.execute_calls = 0

    def execute(self, *args, **kwargs):
        self.execute_calls += 1
        raise AssertionError("validation failure must not dispatch native work")


def _service(root: Path) -> tuple[CadCoreService, _NeverExecuteSession]:
    session = _NeverExecuteSession()
    return CadCoreService(session, path_policy=DocumentPathPolicy((root,))), session


def test_create_rect_extrude_rejects_nonpositive_depth_before_dispatch(tmp_path: Path) -> None:
    service, session = _service(tmp_path)

    result = service.create_rect_extrude(
        tmp_path / "part.SLDPRT",
        width_mm=120.0,
        height_mm=80.0,
        depth_mm=0.0,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure.code == "cad_validation_error"
    assert result.dispatched is False
    assert session.execute_calls == 0


def test_create_rectangle_sketch_rejects_unknown_standard_plane(tmp_path: Path) -> None:
    service, session = _service(tmp_path)

    result = service.create_rectangle_sketch(
        tmp_path / "part.SLDPRT",
        width_mm=50.0,
        height_mm=30.0,
        plane="xy",
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure.code == "cad_validation_error"
    assert session.execute_calls == 0


def test_create_refuses_overwrite_before_dispatch(tmp_path: Path) -> None:
    target = tmp_path / "part.SLDPRT"
    target.write_bytes(b"existing")
    service, session = _service(tmp_path)

    result = service.create_rect_extrude(
        target,
        width_mm=20.0,
        height_mm=10.0,
        depth_mm=5.0,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure.code == "document_already_exists"
    assert session.execute_calls == 0


def test_create_assembly_requires_one_xyz_placement_per_component(tmp_path: Path) -> None:
    part = tmp_path / "part.SLDPRT"
    part.write_bytes(b"fixture")
    service, session = _service(tmp_path)

    result = service.create_assembly(
        tmp_path / "assembly.SLDASM",
        component_paths=[str(part)],
        placements_mm=[],
    )

    assert result.state is NativeCallState.FAILURE
    assert result.failure.code == "cad_validation_error"
    assert session.execute_calls == 0


def test_combine_requires_native_part_extension_before_dispatch(tmp_path: Path) -> None:
    wrong = tmp_path / "part.txt"
    wrong.write_text("fixture")
    service, session = _service(tmp_path)

    result = service.combine_all_bodies(wrong)

    assert result.state is NativeCallState.FAILURE
    assert result.failure.code == "document_extension_mismatch"
    assert session.execute_calls == 0
