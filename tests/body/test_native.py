from __future__ import annotations

from cdt_solidworks.body.models import CombineOperation
from cdt_solidworks.body.native import BodyNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.models import NativeCallState


class NeverExecuteSession:
    def __init__(self) -> None:
        self.api = object()
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("validation failure must not dispatch to session")


def test_subtract_without_main_body_fails_before_native_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = BodyNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.combine(
        source,
        operation=CombineOperation.SUBTRACT,
        body_names=("Body1", "Body2"),
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_non_native_extension_fails_closed(tmp_path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = BodyNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.inspect(source)

    assert result.state is NativeCallState.FAILURE
    assert result.failure is not None
    assert result.failure.code == "document_extension_mismatch"
    assert session.calls == 0


def test_move_copy_rejects_zero_translation_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = BodyNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.move_copy(
        source,
        body_names=("Body1",),
        translation_mm=(0.0, 0.0, 0.0),
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_delete_keep_rejects_duplicate_body_identity_before_dispatch(tmp_path) -> None:
    source = tmp_path / "part.sldprt"
    source.write_bytes(b"fixture")
    session = NeverExecuteSession()
    adapter = BodyNativeAdapter(session, path_policy=DocumentPathPolicy((tmp_path,)))

    result = adapter.delete_keep(
        source,
        body_names=("Body1", "Body1"),
        keep=False,
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0
