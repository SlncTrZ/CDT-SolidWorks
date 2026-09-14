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


class _SketchDomain:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def create(self, target, definition):
        self.calls.append(("create", target, definition))
        return {"sketch_id": definition.name}

    def list_relations(self, target, sketch_id):
        self.calls.append(("list_relations", target, sketch_id))
        return ({"relation_id": "rel-1"},)

    def delete_relation(self, target, sketch_id, relation_id):
        self.calls.append(("delete_relation", target, sketch_id, relation_id))
        return {"deleted": relation_id}

    def set_dimension_value(self, target, sketch_id, name, value, *, unit):
        self.calls.append(("set_dimension", target, sketch_id, name, value, unit))
        return {"name": name, "value": value, "unit": unit}


def test_sketch_integration_promotes_native_passed_constraints_and_dimensions(tmp_path: Path) -> None:
    part = tmp_path / "input.SLDPRT"
    part.write_bytes(b"fixture")
    domain = _SketchDomain()
    service = IntegratedSketchService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=domain,
    )

    result = service.create_geometry(
        path=str(part),
        expected_revision=7,
        name="MountingSketch",
        plane="front",
        entities=(
            {"type": "line", "start_mm": [0.0, 0.0], "end_mm": [20.0, 0.0]},
            {"type": "line", "start_mm": [20.0, 0.0], "end_mm": [20.0, 10.0]},
            {"type": "circle", "center_mm": [5.0, 5.0], "radius_mm": 2.0},
        ),
        constraints=(
            {"type": "horizontal", "entity_index": 0},
            {"type": "perpendicular", "first_entity_index": 0, "second_entity_index": 1},
        ),
        dimensions=(
            {"type": "distance", "name": "D1", "entity_index": 0, "value_mm": 20.0},
            {"type": "radius", "name": "R1", "entity_index": 2, "value_mm": 2.0},
            {"type": "angular", "name": "A1", "first_entity_index": 0, "second_entity_index": 1, "value_deg": 90.0},
        ),
    )

    assert result.state is NativeCallState.SUCCESS
    _, target, definition = domain.calls[-1]
    assert target.expected_revision == 7
    assert [type(item).__name__ for item in definition.constraints] == [
        "HorizontalConstraint",
        "PerpendicularConstraint",
    ]
    assert [type(item).__name__ for item in definition.dimensions] == [
        "DistanceDimension",
        "RadiusDimension",
        "AngularDimension",
    ]


def test_sketch_integration_exposes_relation_and_dimension_management(tmp_path: Path) -> None:
    part = tmp_path / "input.SLDPRT"
    part.write_bytes(b"fixture")
    domain = _SketchDomain()
    service = IntegratedSketchService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=domain,
    )

    listed = service.list_relations(path=str(part), expected_revision=8, sketch_id="Sketch1")
    deleted = service.delete_relation(
        path=str(part), expected_revision=8, sketch_id="Sketch1", relation_id="rel-1"
    )
    changed = service.set_dimension(
        path=str(part), expected_revision=8, sketch_id="Sketch1", name="D1", value=25.0, unit="mm"
    )

    assert listed.state is NativeCallState.SUCCESS
    assert deleted.state is NativeCallState.SUCCESS
    assert changed.state is NativeCallState.SUCCESS
    assert [call[0] for call in domain.calls] == ["list_relations", "delete_relation", "set_dimension"]


def test_sketch_integration_rejects_unknown_constraint_fields_before_domain_call(tmp_path: Path) -> None:
    part = tmp_path / "input.SLDPRT"
    part.write_bytes(b"fixture")
    domain = _SketchDomain()
    service = IntegratedSketchService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=domain,
    )

    result = service.create_geometry(
        path=str(part),
        expected_revision=1,
        name="Sketch1",
        plane="front",
        entities=({"type": "line", "start_mm": [0, 0], "end_mm": [1, 0]},),
        constraints=({"type": "horizontal", "entity_index": 0, "raw_com": True},),
    )

    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "cad_validation_error"
    assert domain.calls == []
