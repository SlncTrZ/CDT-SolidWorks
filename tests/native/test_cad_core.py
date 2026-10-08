from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure


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


def test_add_rect_extrude_activates_existing_target_before_mutation(tmp_path: Path) -> None:
    target = tmp_path / "part.SLDPRT"
    target.write_bytes(b"fixture")
    model = object()
    other_model = object()

    class Api:
        def __init__(self) -> None:
            self.activation_calls = []

        @staticmethod
        def get_open_document(app, path):
            assert path == str(target.resolve())
            return model

        def activate_document(self, app, candidate):
            self.activation_calls.append(candidate)
            app.active_model = candidate
            return candidate, 0

        @staticmethod
        def document_path(candidate):
            assert candidate is model
            return str(target.resolve())

        @staticmethod
        def feature_name(feature):
            return "Boss-Extrude2"

        @staticmethod
        def bodies(candidate, body_type, visible_only):
            assert candidate is model
            assert body_type == 0
            return (object(),)

    class Session:
        def __init__(self) -> None:
            self.api = Api()
            self.app = type("App", (), {"active_model": other_model})()

        def execute(self, operation, *, stage, timeout, mutation, **kwargs):
            assert mutation is True
            try:
                value = operation(self.app)
            except Exception as exc:
                return NativeCallResult.failed(
                    NativeFailure("test_native_failure", stage, str(exc)),
                    call_id="call-1",
                    dispatched=True,
                )
            return NativeCallResult.success(value, call_id="call-1", dispatched=True)

    class Service(CadCoreService):
        def _create_rectangle_sketch(self, candidate, *args):
            assert candidate is model
            assert self.session.app.active_model is model, "target part must be active before sketch mutation"
            return {"plane": "Front Plane", "sketch_name": "Sketch2", "entity_count": 4}

        @staticmethod
        def _extrude_selected_sketch(candidate, depth, *, merge):
            assert candidate is model
            assert merge is True
            return object()

        @staticmethod
        def _require_clean_rebuild(candidate, stage):
            assert candidate is model

        @staticmethod
        def _save(candidate, stage):
            assert candidate is model

    session = Session()
    service = Service(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = service.add_rect_extrude(
        target,
        width_mm=20.0,
        height_mm=20.0,
        depth_mm=20.0,
        center_x_mm=30.0,
        merge=True,
    )

    assert result.state is NativeCallState.SUCCESS
    assert session.api.activation_calls == [model]
