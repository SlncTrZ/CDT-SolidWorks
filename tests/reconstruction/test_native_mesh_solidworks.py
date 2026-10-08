from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct

import pytest

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_server
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.server.factory import ServerConfig


pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("CDT_SW_NATIVE_MESH_EVIDENCE_ROOT"),
    reason="native SOLIDWORKS mesh evidence requires Windows and CDT_SW_NATIVE_MESH_EVIDENCE_ROOT",
)


def _require_success(result, label: str):
    assert result.state is NativeCallState.SUCCESS, (
        label,
        result.state,
        result.failure,
    )
    assert result.value is not None
    return result.value


def _artifact(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _public_tool(server, name: str):
    return next(tool for tool in server._tool_manager.list_tools() if tool.name == name)


def _create_bracket_source(runtime: IntegratedProviderRuntime, path: Path) -> None:
    _require_success(
        runtime.cad_service.create_rect_extrude(
            path,
            width_mm=80.0,
            height_mm=50.0,
            depth_mm=12.0,
        ),
        "create mesh benchmark bracket base",
    )
    context = _require_success(
        runtime.document_service.open(path),
        "open mesh benchmark bracket",
    )
    _require_success(
        runtime.part_feature_service.simple_hole(
            path=str(path),
            expected_revision=context.update_stamp,
            name="SourceHole",
            diameter_mm=10.0,
            face_ref="bbox:+z",
            center_mm=(0.0, 0.0),
            through_all=True,
        ),
        "create mesh benchmark through-hole",
    )
    context = _require_success(
        runtime.document_service.refresh(context),
        "refresh mesh benchmark bracket",
    )
    context = _require_success(
        runtime.document_service.save(context),
        "save mesh benchmark bracket",
    )
    _require_success(
        runtime.document_service.close(context),
        "close mesh benchmark bracket",
    )


def _export_stl(runtime: IntegratedProviderRuntime, source: Path, target: Path) -> None:
    inspection = _require_success(
        runtime.export_service.export(
            str(source),
            str(target),
            "stl",
            source_configuration="Default",
        ),
        "export STL mesh benchmark",
    )
    assert inspection.readable is True
    assert inspection.geometry_verified is True
    payload = target.read_bytes()
    assert len(payload) >= 84
    triangle_count = struct.unpack_from("<I", payload, 80)[0]
    assert triangle_count > 0
    assert len(payload) == 84 + triangle_count * 50


def _make_open_mesh(source: Path, target: Path) -> None:
    payload = source.read_bytes()
    triangle_count = struct.unpack_from("<I", payload, 80)[0]
    assert triangle_count > 1
    degraded = bytearray(payload[:-50])
    struct.pack_into("<I", degraded, 80, triangle_count - 1)
    target.write_bytes(degraded)


def test_native_stl_reconstruction_workflow() -> None:
    root = Path(os.environ["CDT_SW_NATIVE_MESH_EVIDENCE_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)

    source_native = root / "mesh-recon-source-bracket.SLDPRT"
    source_stl = root / "mesh-recon-source-bracket.stl"
    output_native = root / "mesh-recon-output-bracket.SLDPRT"
    degraded_stl = root / "mesh-recon-open-bracket.stl"
    refused_output = root / "mesh-recon-must-not-exist.SLDPRT"
    evidence_path = root / "mesh-reconstruction-native.json"

    for path in (
        source_native,
        source_stl,
        output_native,
        degraded_stl,
        refused_output,
        evidence_path,
    ):
        path.unlink(missing_ok=True)

    session = SolidWorksSession()
    try:
        session_info = _require_success(
            session.connect(
                policy=AttachPolicy.START_NEW,
                version=2024,
                visible=False,
                timeout=180.0,
            ),
            "provider-owned SOLIDWORKS connect",
        )
        assert session_info.ownership.value == "provider_owned"
        runtime = IntegratedProviderRuntime(
            allowed_roots=(root,),
            session=session,
            topology_reference_secret="native-stl-reconstruction-acceptance",
        )
        server = build_integrated_server(ServerConfig.in_process(), runtime=runtime)

        _create_bracket_source(runtime, source_native)
        _export_stl(runtime, source_native, source_stl)
        _make_open_mesh(source_stl, degraded_stl)

        assess_tool = _public_tool(server, "reconstruction_assess")
        reconstruct_tool = _public_tool(server, "reconstruction_mesh_to_parametric")

        unitless = assess_tool.fn(source_path=str(source_stl))
        assert unitless["state"] == "success", unitless
        assert unitless["value"]["source"]["source_class"] == "mesh"
        assert unitless["value"]["unit"] is None
        assert unitless["value"]["unit_confidence"] == 0.0
        assert unitless["value"]["recognized_class"] is None

        assessment = assess_tool.fn(
            source_path=str(source_stl),
            mesh_scale_to_mm=1.0,
        )
        assert assessment["state"] == "success", assessment
        assert assessment["value"]["recognized_class"] == "prismatic_bracket"
        assert assessment["value"]["mesh"]["watertight"] is True
        assert assessment["value"]["mesh"]["manifold"] is True
        assert assessment["value"]["mesh"]["component_count"] == 1
        assert assessment["value"]["mesh"]["boundary_edge_count"] == 0
        assert assessment["value"]["dimensions_mm"]["width"] == pytest.approx(80.0, abs=0.05)
        assert assessment["value"]["dimensions_mm"]["height"] == pytest.approx(50.0, abs=0.05)
        assert assessment["value"]["dimensions_mm"]["depth"] == pytest.approx(12.0, abs=0.05)
        assert assessment["value"]["dimensions_mm"]["hole_diameter"] == pytest.approx(10.0, abs=0.05)
        cylinder = next(
            item
            for item in assessment["value"]["primitives"]
            if item["kind"] == "cylinder"
        )
        assert 0.0 < cylinder["fit_residual_mm"] <= 0.10
        assert cylinder["confidence"] >= 0.8

        missing_scale = reconstruct_tool.fn(
            source_path=str(source_stl),
            output_path=str(refused_output),
            benchmark_class="prismatic_bracket",
            approximation_tolerance_mm=0.10,
            mesh_scale_to_mm=None,
            intended_edit=None,
        )
        assert missing_scale["state"] == "failed"
        assert missing_scale["dispatched"] is False
        assert missing_scale["error"]["code"] == "validation_error"
        assert not refused_output.exists()

        reconstruction = reconstruct_tool.fn(
            source_path=str(source_stl),
            output_path=str(output_native),
            benchmark_class="prismatic_bracket",
            approximation_tolerance_mm=0.10,
            mesh_scale_to_mm=1.0,
            intended_edit={
                "feature_id": "ReconstructionBase",
                "parameter": "depth_mm",
                "value_mm": 15.0,
            },
        )
        assert reconstruction["state"] == "success", reconstruction
        assert reconstruction["value"]["strategy"] == "approximation"
        assert reconstruction["value"]["within_tolerance"] is True
        assert reconstruction["value"]["reopen_verified"] is True
        assert reconstruction["value"]["intended_edit_verified"] is True
        assert "ReconstructionBase" in reconstruction["value"]["editable_feature_ids"]
        assert "ReconstructionHole" in reconstruction["value"]["editable_feature_ids"]
        assert max(
            row["deviation_mm"]
            for row in reconstruction["value"]["dimension_ledger"]
        ) <= 0.10

        degraded_assessment = assess_tool.fn(
            source_path=str(degraded_stl),
            mesh_scale_to_mm=1.0,
        )
        assert degraded_assessment["state"] == "success", degraded_assessment
        assert degraded_assessment["value"]["mesh"]["watertight"] is False
        assert degraded_assessment["value"]["mesh"]["boundary_edge_count"] > 0
        assert degraded_assessment["value"]["recognized_class"] is None

        degraded_refusal = reconstruct_tool.fn(
            source_path=str(degraded_stl),
            output_path=str(refused_output),
            benchmark_class="prismatic_bracket",
            approximation_tolerance_mm=0.10,
            mesh_scale_to_mm=1.0,
            intended_edit=None,
        )
        assert degraded_refusal["state"] == "failed"
        assert degraded_refusal["dispatched"] is False
        assert degraded_refusal["error"]["code"] == "validation_error"
        assert not refused_output.exists()

        assert output_native.is_file() and output_native.stat().st_size > 0
        evidence = {
            "host": os.environ.get("COMPUTERNAME"),
            "solidworks_revision": session_info.revision,
            "solidworks_version_year": session_info.version_year,
            "session_ownership": session_info.ownership.value,
            "unitless_assessment": unitless,
            "scaled_assessment": assessment,
            "missing_scale_refusal": missing_scale,
            "reconstruction": reconstruction,
            "degraded_assessment": degraded_assessment,
            "degraded_refusal": degraded_refusal,
            "artifacts": [
                _artifact(path)
                for path in (
                    source_native,
                    source_stl,
                    output_native,
                    degraded_stl,
                )
            ],
        }
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assert evidence_path.is_file() and evidence_path.stat().st_size > 0
    finally:
        session.disconnect(timeout=15.0)
        session.close_dispatcher(timeout=5.0)
