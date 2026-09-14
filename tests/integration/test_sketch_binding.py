from __future__ import annotations

from pathlib import Path

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.sketch import IntegratedSketchService
from cdt_solidworks.native.models import NativeCallState


class _NoDispatchApi:
    def _member(self, *args, **kwargs):
        raise AssertionError("COM member access must not occur for rejected input")


class _NoDispatchSession:
    def __init__(self) -> None:
        self.api = _NoDispatchApi()
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("native dispatch must not occur for rejected input")


def _service(tmp_path: Path) -> tuple[IntegratedSketchService, _NoDispatchSession, Path]:
    part = tmp_path / "input.SLDPRT"
    part.write_bytes(b"fixture")
    session = _NoDispatchSession()
    service = IntegratedSketchService(
        session,
        path_policy=DocumentPathPolicy((tmp_path,)),
    )
    return service, session, part


@pytest.mark.parametrize("bad_revision", [True, 1.5, "2", -1])
def test_sketch_integration_rejects_non_integer_or_negative_revision_before_dispatch(
    tmp_path: Path, bad_revision: object
) -> None:
    service, session, part = _service(tmp_path)

    result = service.create_geometry(
        path=str(part),
        expected_revision=bad_revision,  # type: ignore[arg-type]
        name="Sketch1",
        plane="front",
        entities=({"type": "point", "point_mm": [1.0, 2.0]},),
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert session.calls == 0


def test_sketch_integration_rejects_non_string_name_and_nested_unknown_fields(
    tmp_path: Path,
) -> None:
    service, session, part = _service(tmp_path)

    bad_name = service.create_geometry(
        path=str(part),
        expected_revision=1,
        name=123,  # type: ignore[arg-type]
        plane="front",
        entities=({"type": "point", "point_mm": [1.0, 2.0]},),
    )
    bad_entity = service.create_geometry(
        path=str(part),
        expected_revision=1,
        name="Sketch1",
        plane="front",
        entities=({"type": "point", "point_mm": [1.0, 2.0], "unexpected": True},),
    )

    for result in (bad_name, bad_entity):
        assert result.state is NativeCallState.FAILURE
        assert result.dispatched is False
        assert result.failure is not None
        assert result.failure.code == "cad_validation_error"
    assert session.calls == 0
