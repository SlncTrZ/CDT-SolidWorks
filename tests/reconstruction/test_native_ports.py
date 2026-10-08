from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from cdt_solidworks.document.models import DocumentContext, DocumentType
from cdt_solidworks.evaluation.domain import BoundingBox, GeometrySanity
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.reconstruction.native_ports import (
    NativeStepPartPort,
    NativeStepTopologyPort,
    NativeStlMeshPort,
    bind_native_mesh_port,
    bind_native_step_ports,
)
from cdt_solidworks.topology.models import (
    FaceGeometry,
    Point3D,
    TopologyInspection,
    TopologyItem,
    TopologyQueryResult,
    Vector3D,
)


def _ok(value):
    return NativeCallResult.success(value, call_id="test-call", dispatched=True)


def _face(ref: str, geometry: FaceGeometry) -> TopologyInspection:
    return TopologyInspection(
        kind="face",
        reference=ref,
        body_name="Body1",
        component_id=None,
        geometry=geometry,
    )


def _bracket_mesh_triangles(
    *,
    width: float = 80.0,
    height: float = 50.0,
    depth: float = 12.0,
    hole_radius: float = 5.0,
    segments: int = 16,
) -> list[tuple[tuple[float, float, float], ...]]:
    per_side = segments // 4
    outer: list[tuple[float, float]] = []
    for index in range(segments):
        side, step = divmod(index, per_side)
        fraction = step / per_side
        if side == 0:
            outer.append((fraction * width, 0.0))
        elif side == 1:
            outer.append((width, fraction * height))
        elif side == 2:
            outer.append((width * (1.0 - fraction), height))
        else:
            outer.append((0.0, height * (1.0 - fraction)))
    center = (width / 2.0, height / 2.0)
    inner = [
        (
            center[0] + hole_radius * math.cos(-3.0 * math.pi / 4.0 + 2.0 * math.pi * index / segments),
            center[1] + hole_radius * math.sin(-3.0 * math.pi / 4.0 + 2.0 * math.pi * index / segments),
        )
        for index in range(segments)
    ]
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(segments):
        nxt = (index + 1) % segments
        ob0 = (*outer[index], 0.0)
        ob1 = (*outer[nxt], 0.0)
        ot0 = (*outer[index], depth)
        ot1 = (*outer[nxt], depth)
        ib0 = (*inner[index], 0.0)
        ib1 = (*inner[nxt], 0.0)
        it0 = (*inner[index], depth)
        it1 = (*inner[nxt], depth)
        triangles.extend(
            [
                (ot0, ot1, it1),
                (ot0, it1, it0),
                (ob0, ib1, ob1),
                (ob0, ib0, ib1),
                (ob0, ob1, ot1),
                (ob0, ot1, ot0),
                (ib0, it1, ib1),
                (ib0, it0, it1),
            ]
        )
    return triangles


def _write_binary_stl(
    path: Path,
    triangles: list[tuple[tuple[float, float, float], ...]],
) -> None:
    payload = bytearray(b"cdt-solidworks-test".ljust(80, b" "))
    payload.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        payload.extend(
            struct.pack(
                "<12fH",
                0.0,
                0.0,
                0.0,
                *(coordinate for vertex in triangle for coordinate in vertex),
                0,
            )
        )
    path.write_bytes(payload)


def test_topology_evidence_recognizes_controlled_prismatic_bracket() -> None:
    bounds = BoundingBox((0.0, 0.0, 0.0), (0.080, 0.050, 0.012))
    sanity = GeometrySanity(1, 0, 0, 0)
    query = TopologyQueryResult(
        items=tuple(
            TopologyItem("face", f"f{i}", "Body1", i)
            for i in range(8)
        ),
        counts={"body": 1, "face": 8, "edge": 18, "vertex": 12},
    )
    planes = [
        FaceGeometry("planar", normal=Vector3D(1.0, 0.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(-1.0, 0.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 1.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, -1.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 0.0, 1.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 0.0, -1.0)),
    ]
    cylinder = FaceGeometry(
        "cylindrical",
        origin_mm=Point3D(0.0, 0.0, 0.0),
        axis=Vector3D(0.0, 0.0, 1.0),
        radius_mm=5.0,
    )
    inspections = tuple(
        _face(f"f{i}", geometry)
        for i, geometry in enumerate([*planes, cylinder, cylinder])
    )

    evidence = NativeStepTopologyPort.evidence_from_native(
        bounds=bounds,
        sanity=sanity,
        query=query,
        inspections=inspections,
    )

    assert evidence["recognized_class"] == "prismatic_bracket"
    assert evidence["dimensions_mm"] == {
        "width": pytest.approx(80.0),
        "height": pytest.approx(50.0),
        "depth": pytest.approx(12.0),
        "hole_diameter": pytest.approx(10.0),
    }
    assert evidence["unit"] == "mm"
    assert evidence["unit_confidence"] == 1.0
    assert evidence["frame_confidence"] >= 0.95
    assert sum(item["kind"] == "cylinder" for item in evidence["primitives"]) == 1


def test_topology_evidence_refuses_two_distinct_bracket_holes() -> None:
    bounds = BoundingBox((0.0, 0.0, 0.0), (0.080, 0.050, 0.012))
    sanity = GeometrySanity(1, 0, 0, 0)
    query = TopologyQueryResult(
        items=tuple(TopologyItem("face", f"f{i}", "Body1", i) for i in range(8)),
        counts={"body": 1, "face": 8, "edge": 20, "vertex": 16},
    )
    planes = [
        FaceGeometry("planar", normal=Vector3D(1.0, 0.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(-1.0, 0.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 1.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, -1.0, 0.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 0.0, 1.0)),
        FaceGeometry("planar", normal=Vector3D(0.0, 0.0, -1.0)),
    ]
    cylinders = [
        FaceGeometry(
            "cylindrical",
            origin_mm=Point3D(-15.0, 0.0, 0.0),
            axis=Vector3D(0.0, 0.0, 1.0),
            radius_mm=5.0,
        ),
        FaceGeometry(
            "cylindrical",
            origin_mm=Point3D(15.0, 0.0, 0.0),
            axis=Vector3D(0.0, 0.0, 1.0),
            radius_mm=5.0,
        ),
    ]
    inspections = tuple(
        _face(f"f{i}", geometry)
        for i, geometry in enumerate([*planes, *cylinders])
    )

    evidence = NativeStepTopologyPort.evidence_from_native(
        bounds=bounds,
        sanity=sanity,
        query=query,
        inspections=inspections,
    )

    assert evidence["recognized_class"] is None
    assert sum(item["kind"] == "cylinder" for item in evidence["primitives"]) == 2


def test_topology_evidence_recognizes_axis_aligned_turned_shaft() -> None:
    bounds = BoundingBox((0.0, -0.015, -0.015), (0.070, 0.015, 0.015))
    sanity = GeometrySanity(1, 0, 0, 0)
    query = TopologyQueryResult(
        items=tuple(
            TopologyItem("face", f"f{i}", "Body1", i)
            for i in range(4)
        ),
        counts={"body": 1, "face": 4, "edge": 6, "vertex": 4},
    )
    inspections = (
        _face(
            "f0",
            FaceGeometry(
                "cylindrical",
                axis=Vector3D(1.0, 0.0, 0.0),
                radius_mm=15.0,
            ),
        ),
        _face(
            "f1",
            FaceGeometry(
                "cylindrical",
                axis=Vector3D(1.0, 0.0, 0.0),
                radius_mm=10.0,
            ),
        ),
        _face("f2", FaceGeometry("planar", normal=Vector3D(-1.0, 0.0, 0.0))),
        _face("f3", FaceGeometry("planar", normal=Vector3D(1.0, 0.0, 0.0))),
    )

    evidence = NativeStepTopologyPort.evidence_from_native(
        bounds=bounds,
        sanity=sanity,
        query=query,
        inspections=inspections,
    )

    assert evidence["recognized_class"] == "turned_shaft"
    assert evidence["dimensions_mm"]["length"] == pytest.approx(70.0)
    assert evidence["dimensions_mm"]["outer_diameter"] == pytest.approx(30.0)
    assert evidence["frame_confidence"] >= 0.95


def test_topology_evidence_refuses_ambiguous_plain_cube() -> None:
    bounds = BoundingBox((0.0, 0.0, 0.0), (0.050, 0.050, 0.050))
    sanity = GeometrySanity(1, 0, 0, 0)
    query = TopologyQueryResult(
        items=tuple(TopologyItem("face", f"f{i}", "Body1", i) for i in range(6)),
        counts={"body": 1, "face": 6, "edge": 12, "vertex": 8},
    )
    inspections = tuple(
        _face(
            f"f{i}",
            FaceGeometry(
                "planar",
                normal=Vector3D(
                    1.0 if i == 0 else -1.0 if i == 1 else 0.0,
                    1.0 if i == 2 else -1.0 if i == 3 else 0.0,
                    1.0 if i == 4 else -1.0 if i == 5 else 0.0,
                ),
            ),
        )
        for i in range(6)
    )

    evidence = NativeStepTopologyPort.evidence_from_native(
        bounds=bounds,
        sanity=sanity,
        query=query,
        inspections=inspections,
    )

    assert evidence["recognized_class"] is None


@dataclass
class _FakeDocumentService:
    revision: int = 1

    def _context(self, path: str) -> DocumentContext:
        return DocumentContext(
            session_id="session",
            path=path,
            title=Path(path).name,
            document_type=DocumentType.PART,
            configuration="Default",
            update_stamp=self.revision,
        )

    def open(self, path: str, **kwargs):
        return _ok(self._context(path))

    def refresh(self, context: DocumentContext, **kwargs):
        self.revision += 1
        return _ok(self._context(context.path))

    def save(self, context: DocumentContext, **kwargs):
        self.revision += 1
        return _ok(self._context(context.path))

    def reopen(self, context: DocumentContext, **kwargs):
        self.revision += 1
        return _ok(self._context(context.path))

    def close(self, context: DocumentContext, **kwargs):
        return _ok(True)


class _FakeCad:
    def create_rect_extrude(self, output_path: str, **kwargs):
        Path(output_path).write_bytes(b"native-part")
        return _ok(
            {
                "path": output_path,
                "sketch_name": "ReconstructionProfile",
                "feature_name": "ReconstructionBase",
                "body_count": 1,
            }
        )

    def create_empty_part(self, output_path: str, **kwargs):
        Path(output_path).write_bytes(b"empty-native-part")
        return _ok({"path": output_path})


class _FakePart:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str, float]] = []
        self.parameters = {
            "ReconstructionHole": {"diameter_mm": 10.0},
            "ReconstructionBase": {"depth_mm": 12.0},
        }

    def rename_feature(self, **kwargs):
        return _ok(SimpleNamespace(feature_id=kwargs["new_name"]))

    def simple_hole(self, **kwargs):
        return _ok(
            SimpleNamespace(
                feature=SimpleNamespace(
                    feature_id="ReconstructionHole",
                    parameters={"diameter_mm": kwargs["diameter_mm"]},
                )
            )
        )

    def revolve(self, **kwargs):
        return _ok(
            SimpleNamespace(
                feature=SimpleNamespace(
                    feature_id="ReconstructionShaft",
                    parameters={"angle_deg": 360.0},
                )
            )
        )

    def feature_parameters_get(self, **kwargs):
        return _ok(dict(self.parameters.get(kwargs["feature_id"], {})))

    def feature_parameter_set(self, **kwargs):
        value = float(kwargs["value"])
        self.edits.append((kwargs["feature_id"], kwargs["parameter"], value))
        self.parameters.setdefault(kwargs["feature_id"], {})[kwargs["parameter"]] = value
        return _ok(
            SimpleNamespace(
                feature_id=kwargs["feature_id"],
                parameters={kwargs["parameter"]: float(kwargs["value"])},
            )
        )


class _FakeSketch:
    def create_geometry(self, **kwargs):
        return _ok(SimpleNamespace(sketch_id="ReconstructionShaftProfile"))


class _FakeEvaluation:
    def __init__(self, bounds: BoundingBox) -> None:
        self.bounds = bounds

    def bounding_box(self, path: str, configuration: str | None = None):
        return _ok(self.bounds)

    def geometry_sanity(self, path: str, configuration: str | None = None):
        return _ok(GeometrySanity(1, 0, 0, 0))


def test_native_part_port_rebuilds_prismatic_bracket_and_verifies_reopen(tmp_path: Path) -> None:
    part = _FakePart()
    port = NativeStepPartPort(
        cad_service=_FakeCad(),
        document_service=_FakeDocumentService(),
        sketch_service=_FakeSketch(),
        part_service=part,
        evaluation_service=_FakeEvaluation(
            BoundingBox((-0.040, -0.025, 0.0), (0.040, 0.025, 0.012))
        ),
    )
    output = tmp_path / "reconstructed.SLDPRT"

    result = port.rebuild_editable(
        str(output),
        {
            "benchmark_class": "prismatic_bracket",
            "critical_dimensions_mm": {
                "width": 80.0,
                "height": 50.0,
                "depth": 12.0,
                "hole_diameter": 10.0,
            },
            "primitive_fits": [
                {
                    "kind": "cylinder",
                    "confidence": 1.0,
                    "fit_residual_mm": 0.0,
                    "parameters": {
                        "center_x_mm": 0.0,
                        "center_y_mm": 0.0,
                        "axis": "z",
                        "radius_mm": 5.0,
                    },
                }
            ],
        },
    )

    assert output.is_file()
    assert result["reopen_verified"] is True
    assert result["dimensions_mm"]["width"] == pytest.approx(80.0)
    assert result["dimensions_mm"]["hole_diameter"] == pytest.approx(10.0)
    assert "ReconstructionBase" in result["feature_ids"]
    assert "ReconstructionHole" in result["feature_ids"]


def test_native_part_port_rebuilds_turned_shaft_with_revolve(tmp_path: Path) -> None:
    port = NativeStepPartPort(
        cad_service=_FakeCad(),
        document_service=_FakeDocumentService(),
        sketch_service=_FakeSketch(),
        part_service=_FakePart(),
        evaluation_service=_FakeEvaluation(
            BoundingBox((0.0, -0.015, -0.015), (0.070, 0.015, 0.015))
        ),
    )
    output = tmp_path / "shaft.SLDPRT"

    result = port.rebuild_editable(
        str(output),
        {
            "benchmark_class": "turned_shaft",
            "critical_dimensions_mm": {
                "length": 70.0,
                "outer_diameter": 30.0,
            },
            "primitive_fits": [],
        },
    )

    assert result["reopen_verified"] is True
    assert result["dimensions_mm"] == {
        "length": pytest.approx(70.0),
        "outer_diameter": pytest.approx(30.0),
    }
    assert result["feature_ids"] == [
        "ReconstructionShaftProfile",
        "ReconstructionShaft",
    ]


def test_native_part_port_reopen_and_edit_uses_part_parameter_readback(tmp_path: Path) -> None:
    part = _FakePart()
    docs = _FakeDocumentService()
    port = NativeStepPartPort(
        cad_service=_FakeCad(),
        document_service=docs,
        sketch_service=_FakeSketch(),
        part_service=part,
        evaluation_service=_FakeEvaluation(
            BoundingBox((-0.040, -0.025, 0.0), (0.040, 0.025, 0.012))
        ),
    )
    output = tmp_path / "editable.SLDPRT"
    output.write_bytes(b"part")

    result = port.reopen_and_edit(
        str(output),
        {
            "feature_id": "ReconstructionBase",
            "parameter": "depth_mm",
            "value_mm": 15.0,
        },
    )

    assert result["edited"] is True
    assert result["reopen_verified"] is True
    assert result["readback_mm"] == pytest.approx(15.0)
    assert part.edits == [("ReconstructionBase", "depth_mm", 15.0)]


def test_stl_mesh_port_requires_explicit_scale_before_classification(tmp_path: Path) -> None:
    source = tmp_path / "bracket.stl"
    _write_binary_stl(source, _bracket_mesh_triangles())
    port = NativeStlMeshPort()

    unitless = port.inspect_mesh(str(source))

    assert unitless["triangle_count"] == 128
    assert unitless["watertight"] is True
    assert unitless["manifold"] is True
    assert unitless["unit"] is None
    assert unitless["unit_confidence"] == 0.0
    assert unitless["recognized_class"] is None
    assert unitless["dimensions_mm"] == {}

    scaled = port.inspect_mesh(str(source), scale_to_mm=1.0)

    assert scaled["recognized_class"] == "prismatic_bracket"
    assert scaled["dimensions_mm"] == {
        "width": pytest.approx(80.0),
        "height": pytest.approx(50.0),
        "depth": pytest.approx(12.0),
        "hole_diameter": pytest.approx(10.0),
    }
    assert scaled["unit"] == "mm"
    assert scaled["unit_confidence"] == 1.0
    assert scaled["frame_confidence"] >= 0.95
    cylinder = next(item for item in scaled["primitives"] if item["kind"] == "cylinder")
    assert 0.0 < cylinder["fit_residual_mm"] < 0.1


def test_stl_mesh_port_marks_open_mesh_non_watertight_and_unclassified(tmp_path: Path) -> None:
    source = tmp_path / "open-bracket.stl"
    triangles = _bracket_mesh_triangles()
    _write_binary_stl(source, triangles[:-1])

    evidence = NativeStlMeshPort().inspect_mesh(str(source), scale_to_mm=1.0)

    assert evidence["watertight"] is False
    assert evidence["recognized_class"] is None


def test_bind_native_mesh_port_requires_runtime_path_policy() -> None:
    runtime = SimpleNamespace(path_policy=object())

    bind_native_mesh_port(runtime)

    assert isinstance(runtime.reconstruction_mesh_port, NativeStlMeshPort)


def test_bind_native_step_ports_composes_only_when_dependencies_are_present() -> None:
    runtime = SimpleNamespace(
        import_service=object(),
        evaluation_service=object(),
        document_service=object(),
        topology_service=object(),
        cad_service=object(),
        sketch_service=object(),
        part_feature_service=object(),
    )

    bind_native_step_ports(runtime)

    assert isinstance(runtime.reconstruction_topology_port, NativeStepTopologyPort)
    assert isinstance(runtime.reconstruction_part_port, NativeStepPartPort)
    assert getattr(runtime, "reconstruction_mesh_port", None) is None
