from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cdt_solidworks.integration.plugins.agent5_fabrication_reconstruction import (
    PLUGIN_CONTRACT_VERSION,
    PLUGIN_ID,
    PLUGIN_ORDER,
    capability_descriptors,
    register_tools,
)
from cdt_solidworks.native.models import NativeCallResult


class _CapturingServer:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str):
        def register(func):
            self.tools[name] = func
            return func
        return register


class _Adapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _ok(self, method: str, *args: object, **kwargs: object):
        self.calls.append((method, args, kwargs))
        return NativeCallResult.success({"method": method, "args": args, "kwargs": kwargs}, call_id=f"{method}-1", dispatched=True)

    def move_copy(self, *args, **kwargs): return self._ok("move_copy", *args, **kwargs)
    def delete_keep(self, *args, **kwargs): return self._ok("delete_keep", *args, **kwargs)
    def offset(self, *args, **kwargs): return self._ok("offset", *args, **kwargs)
    def add_edge_flange(self, *args, **kwargs): return self._ok("add_edge_flange", *args, **kwargs)
    def add_hem(self, *args, **kwargs): return self._ok("add_hem", *args, **kwargs)
    def add_sketched_bend(self, *args, **kwargs): return self._ok("add_sketched_bend", *args, **kwargs)
    def unfold_bend(self, *args, **kwargs): return self._ok("unfold_bend", *args, **kwargs)
    def fold_bend(self, *args, **kwargs): return self._ok("fold_bend", *args, **kwargs)
    def trim_extend(self, *args, **kwargs): return self._ok("trim_extend", *args, **kwargs)
    def set_cut_list_property(self, *args, **kwargs): return self._ok("set_cut_list_property", *args, **kwargs)


class _TopologyPort:
    def inspect_source(self, source_path: str) -> dict[str, object]:
        return {
            "body_count": 1, "face_count": 8, "edge_count": 18,
            "recognized_class": "prismatic_bracket",
            "dimensions_mm": {"width": 40.0, "height": 30.0, "depth": 10.0},
            "primitives": [{"kind": "plane", "confidence": 1.0, "fit_residual_mm": 0.0}],
            "unit": "mm", "unit_confidence": 1.0, "frame_confidence": 1.0,
        }


class _PartPort:
    def rebuild_editable(self, output_path: str, plan: dict[str, object]) -> dict[str, object]:
        return {
            "path": output_path,
            "feature_ids": ["Sketch1", "Boss-Extrude1"],
            "dimensions_mm": dict(plan["critical_dimensions_mm"]),
            "reopen_verified": True,
        }

    def reopen_and_edit(self, output_path: str, edit: dict[str, object]) -> dict[str, object]:
        return {"edited": True, "reopen_verified": True, "readback_mm": edit["value_mm"]}


@dataclass
class _Dependency:
    name: str = "solidworks"
    available: bool = True
    reason: str | None = None


@dataclass
class _Context:
    dependencies: tuple[_Dependency, ...] = (_Dependency(),)


class _Runtime:
    def __init__(self, *, with_reconstruction_ports: bool = True) -> None:
        self.body_service = _Adapter()
        self.surface_service = _Adapter()
        self.sheetmetal_service = _Adapter()
        self.weldment_service = _Adapter()
        self.reconstruction_topology_port = _TopologyPort() if with_reconstruction_ports else None
        self.reconstruction_part_port = _PartPort() if with_reconstruction_ports else None
        self.reconstruction_drawing_port = None
        self.reconstruction_mesh_port = None

    def runtime_context(self):
        return _Context()


def test_plugin_identity_and_public_tool_set() -> None:
    runtime = _Runtime()
    server = _CapturingServer()
    register_tools(server, runtime)

    assert PLUGIN_CONTRACT_VERSION == 1
    assert PLUGIN_ORDER == 5
    assert PLUGIN_ID == "agent5.fabrication_reconstruction"
    assert {
        "body_move_copy", "body_delete_keep", "surface_offset",
        "sheet_metal_add_edge_flange", "sheet_metal_add_hem",
        "sheet_metal_add_sketched_bend", "sheet_metal_unfold_bend", "sheet_metal_fold_bend",
        "weldment_trim_extend", "weldment_set_cut_list_property",
        "reconstruction_assess", "reconstruction_step_to_editable",
        "reconstruction_mesh_to_parametric", "reconstruction_compare",
    } == set(server.tools)


def test_public_fabrication_tools_forward_bounded_arguments() -> None:
    runtime = _Runtime()
    server = _CapturingServer()
    register_tools(server, runtime)

    cases = [
        ("body_move_copy", {"path": "/tmp/a.SLDPRT", "body_names": ["Body1"], "translation_mm": [1.0, 2.0, 3.0], "copy": True, "copies": 2}, runtime.body_service, "move_copy"),
        ("body_delete_keep", {"path": "/tmp/a.SLDPRT", "body_names": ["Body1"], "keep": False}, runtime.body_service, "delete_keep"),
        ("surface_offset", {"path": "/tmp/a.SLDPRT", "surface_body_name": "Surface1", "distance_mm": 2.5, "reverse": False}, runtime.surface_service, "offset"),
        ("sheet_metal_add_edge_flange", {"path": "/tmp/a.SLDPRT", "edge_selector": "bbox:+x", "length_mm": 20.0, "angle_deg": 90.0, "bend_radius_mm": 1.0}, runtime.sheetmetal_service, "add_edge_flange"),
        ("sheet_metal_add_hem", {"path": "/tmp/a.SLDPRT", "edge_selector": "bbox:-x", "length_mm": 8.0, "gap_mm": 0.5, "position": "outside", "reverse": False}, runtime.sheetmetal_service, "add_hem"),
        ("sheet_metal_add_sketched_bend", {"path": "/tmp/a.SLDPRT", "line_x_mm": 10.0, "angle_deg": 45.0, "bend_radius_mm": 1.0, "reverse": False}, runtime.sheetmetal_service, "add_sketched_bend"),
        ("sheet_metal_unfold_bend", {"path": "/tmp/a.SLDPRT", "bend_feature_name": "OneBend1", "fixed_x_mm": 5.0}, runtime.sheetmetal_service, "unfold_bend"),
        ("sheet_metal_fold_bend", {"path": "/tmp/a.SLDPRT", "unfold_feature_name": "UnFold1", "bend_feature_name": "OneBend1", "fixed_x_mm": 5.0}, runtime.sheetmetal_service, "fold_bend"),
        ("weldment_trim_extend", {"path": "/tmp/a.SLDPRT", "body_to_trim_name": "Body1", "boundary_body_name": "Body2"}, runtime.weldment_service, "trim_extend"),
        ("weldment_set_cut_list_property", {"path": "/tmp/a.SLDPRT", "cut_list_name": "Cut-List-Item1", "property_name": "PARTNO", "value": "A-100"}, runtime.weldment_service, "set_cut_list_property"),
    ]

    for tool_name, kwargs, adapter, method in cases:
        result = server.tools[tool_name](**kwargs)
        assert result["state"] == "success"
        assert result["error"] is None
        assert adapter.calls[-1][0] == method


def test_reconstruction_public_wrapper_preserves_envelope_and_typed_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "bracket.step"
    source.write_bytes(b"step")

    runtime = _Runtime(with_reconstruction_ports=True)
    server = _CapturingServer()
    register_tools(server, runtime)
    ok = server.tools["reconstruction_step_to_editable"](
        source_path=str(source), output_path=str(tmp_path / "out.SLDPRT"),
        benchmark_class="prismatic_bracket", tolerance_mm=0.05, intended_edit=None,
    )
    assert ok["state"] == "success"
    assert ok["call_id"]
    assert ok["dispatched"] is True
    assert ok["value"]["within_tolerance"] is True

    blocked_runtime = _Runtime(with_reconstruction_ports=False)
    blocked_server = _CapturingServer()
    register_tools(blocked_server, blocked_runtime)
    blocked = blocked_server.tools["reconstruction_step_to_editable"](
        source_path=str(source), output_path=str(tmp_path / "blocked.SLDPRT"),
        benchmark_class="prismatic_bracket", tolerance_mm=0.05, intended_edit=None,
    )
    assert blocked["state"] == "failed"
    assert blocked["dispatched"] is False
    assert blocked["error"]["native_code"] == "capability_unavailable"


def test_capability_descriptors_use_live_dependency_and_port_state() -> None:
    ready = {row["name"]: row for row in capability_descriptors(_Runtime())}
    assert ready["solidworks.body.move_copy"]["implemented"] is True
    assert ready["solidworks.body.move_copy"]["available"] is True
    assert ready["solidworks.reconstruction.step_editable"]["implemented"] is True
    assert ready["solidworks.reconstruction.step_editable"]["available"] is True
    assert ready["solidworks.reconstruction.mesh_parametric"]["available"] is False
    assert ready["solidworks.reconstruction.mesh_parametric"]["reason"] == "mesh_port_unavailable"

    blocked = {row["name"]: row for row in capability_descriptors(_Runtime(with_reconstruction_ports=False))}
    assert blocked["solidworks.reconstruction.step_editable"]["available"] is False
    assert blocked["solidworks.reconstruction.step_editable"]["reason"] == "topology_port_unavailable"
