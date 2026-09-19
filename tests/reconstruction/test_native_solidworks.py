from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from cdt_solidworks.integration.runtime import IntegratedProviderRuntime
from cdt_solidworks.integration.server import build_integrated_server
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.server.factory import ServerConfig


pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("CDT_SW_NATIVE_EVIDENCE_ROOT"),
    reason="native SOLIDWORKS evidence requires Windows and CDT_SW_NATIVE_EVIDENCE_ROOT",
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
        "create bracket base",
    )
    context = _require_success(
        runtime.document_service.open(path),
        "open bracket source",
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
        "create bracket source hole",
    )
    context = _require_success(
        runtime.document_service.refresh(context),
        "refresh bracket source",
    )
    context = _require_success(
        runtime.document_service.save(context),
        "save bracket source",
    )
    _require_success(runtime.document_service.close(context), "close bracket source")


def _create_shaft_source(runtime: IntegratedProviderRuntime, path: Path) -> None:
    _require_success(
        runtime.cad_service.create_empty_part(path),
        "create empty shaft source",
    )
    context = _require_success(
        runtime.document_service.open(path),
        "open shaft source",
    )
    sketch = _require_success(
        runtime.sketch_service.create_geometry(
            path=str(path),
            expected_revision=context.update_stamp,
            name="SourceShaftProfile",
            plane="front",
            entities=[
                {"type": "line", "start_mm": [0.0, 0.0], "end_mm": [0.0, 15.0]},
                {"type": "line", "start_mm": [0.0, 15.0], "end_mm": [70.0, 15.0]},
                {"type": "line", "start_mm": [70.0, 15.0], "end_mm": [70.0, 0.0]},
                {"type": "line", "start_mm": [70.0, 0.0], "end_mm": [0.0, 0.0]},
                {"type": "centerline", "start_mm": [0.0, 0.0], "end_mm": [70.0, 0.0]},
            ],
        ),
        "create shaft source profile",
    )
    context = _require_success(
        runtime.document_service.refresh(context),
        "refresh shaft source after sketch",
    )
    _require_success(
        runtime.part_feature_service.revolve(
            path=str(path),
            expected_revision=context.update_stamp,
            sketch_id=sketch.sketch_id,
            name="SourceShaft",
            axis_ref="profile_centerline",
            angle_deg=360.0,
        ),
        "revolve shaft source",
    )
    context = _require_success(
        runtime.document_service.refresh(context),
        "refresh shaft source after revolve",
    )
    context = _require_success(
        runtime.document_service.save(context),
        "save shaft source",
    )
    _require_success(runtime.document_service.close(context), "close shaft source")


def _export_step(runtime: IntegratedProviderRuntime, source: Path, target: Path) -> None:
    _require_success(
        runtime.export_service.export(
            str(source),
            str(target),
            "step",
            source_configuration="Default",
        ),
        "export STEP source",
    )
    assert target.read_bytes()[:64].lstrip().upper().startswith(b"ISO-10303-21")


def test_native_step_reconstruction_workflow() -> None:
    root = Path(os.environ["CDT_SW_NATIVE_EVIDENCE_ROOT"]).resolve()
    root.mkdir(parents=True, exist_ok=True)

    bracket_native = root / "step-recon-source-bracket.SLDPRT"
    bracket_step = root / "step-recon-source-bracket.step"
    bracket_output = root / "step-recon-output-bracket.SLDPRT"
    shaft_native = root / "step-recon-source-shaft.SLDPRT"
    shaft_step = root / "step-recon-source-shaft.step"
    shaft_output = root / "step-recon-output-shaft.SLDPRT"
    ambiguous_native = root / "step-recon-source-ambiguous.SLDPRT"
    ambiguous_step = root / "step-recon-source-ambiguous.step"
    evidence_path = root / "step-reconstruction-native.json"

    for path in (
        bracket_native,
        bracket_step,
        bracket_output,
        shaft_native,
        shaft_step,
        shaft_output,
        ambiguous_native,
        ambiguous_step,
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
        runtime = IntegratedProviderRuntime(
            allowed_roots=(root,),
            session=session,
            topology_reference_secret="native-step-reconstruction-acceptance",
        )
        server = build_integrated_server(ServerConfig.in_process(), runtime=runtime)

        _create_bracket_source(runtime, bracket_native)
        _export_step(runtime, bracket_native, bracket_step)
        _create_shaft_source(runtime, shaft_native)
        _export_step(runtime, shaft_native, shaft_step)
        _require_success(
            runtime.cad_service.create_rect_extrude(
                ambiguous_native,
                width_mm=50.0,
                height_mm=50.0,
                depth_mm=50.0,
            ),
            "create ambiguous source",
        )
        _export_step(runtime, ambiguous_native, ambiguous_step)

        assess_tool = _public_tool(server, "reconstruction_assess")
        reconstruct_tool = _public_tool(server, "reconstruction_step_to_editable")

        bracket_assessment = assess_tool.fn(source_path=str(bracket_step))
        assert bracket_assessment["state"] == "success", bracket_assessment
        assert bracket_assessment["dispatched"] is True
        assert bracket_assessment["value"]["recognized_class"] == "prismatic_bracket"
        assert bracket_assessment["value"]["dimensions_mm"]["width"] == pytest.approx(80.0, abs=0.05)
        assert bracket_assessment["value"]["dimensions_mm"]["height"] == pytest.approx(50.0, abs=0.05)
        assert bracket_assessment["value"]["dimensions_mm"]["depth"] == pytest.approx(12.0, abs=0.05)
        assert bracket_assessment["value"]["dimensions_mm"]["hole_diameter"] == pytest.approx(10.0, abs=0.05)

        bracket_result = reconstruct_tool.fn(
            source_path=str(bracket_step),
            output_path=str(bracket_output),
            benchmark_class="prismatic_bracket",
            tolerance_mm=0.08,
            intended_edit={
                "feature_id": "ReconstructionBase",
                "parameter": "depth_mm",
                "value_mm": 15.0,
            },
        )
        assert bracket_result["state"] == "success", bracket_result
        assert bracket_result["value"]["within_tolerance"] is True
        assert bracket_result["value"]["reopen_verified"] is True
        assert bracket_result["value"]["intended_edit_verified"] is True
        assert "ReconstructionBase" in bracket_result["value"]["editable_feature_ids"]
        assert "ReconstructionHole" in bracket_result["value"]["editable_feature_ids"]
        assert max(
            row["deviation_mm"]
            for row in bracket_result["value"]["dimension_ledger"]
        ) <= 0.08

        shaft_assessment = assess_tool.fn(source_path=str(shaft_step))
        assert shaft_assessment["state"] == "success", shaft_assessment
        assert shaft_assessment["value"]["recognized_class"] == "turned_shaft"
        assert shaft_assessment["value"]["dimensions_mm"]["length"] == pytest.approx(70.0, abs=0.05)
        assert shaft_assessment["value"]["dimensions_mm"]["outer_diameter"] == pytest.approx(30.0, abs=0.05)

        shaft_result = reconstruct_tool.fn(
            source_path=str(shaft_step),
            output_path=str(shaft_output),
            benchmark_class="turned_shaft",
            tolerance_mm=0.08,
            intended_edit=None,
        )
        assert shaft_result["state"] == "success", shaft_result
        assert shaft_result["value"]["within_tolerance"] is True
        assert shaft_result["value"]["reopen_verified"] is True
        assert shaft_result["value"]["editable_feature_ids"] == [
            "ReconstructionShaftProfile",
            "ReconstructionShaft",
        ]

        ambiguous_assessment = assess_tool.fn(source_path=str(ambiguous_step))
        assert ambiguous_assessment["state"] == "success", ambiguous_assessment
        assert ambiguous_assessment["value"]["recognized_class"] is None

        refused = reconstruct_tool.fn(
            source_path=str(ambiguous_step),
            output_path=str(root / "must-not-exist.SLDPRT"),
            benchmark_class="prismatic_bracket",
            tolerance_mm=0.08,
            intended_edit=None,
        )
        assert refused["state"] == "failed"
        assert refused["dispatched"] is False
        assert refused["error"]["code"] == "validation_error"
        assert not (root / "must-not-exist.SLDPRT").exists()

        assert bracket_output.is_file() and bracket_output.stat().st_size > 0
        assert shaft_output.is_file() and shaft_output.stat().st_size > 0

        evidence = {
            "host": os.environ.get("COMPUTERNAME"),
            "solidworks_revision": session_info.revision,
            "solidworks_version_year": session_info.version_year,
            "session_ownership": session_info.ownership.value,
            "bracket_assessment": bracket_assessment,
            "bracket_reconstruction": bracket_result,
            "shaft_assessment": shaft_assessment,
            "shaft_reconstruction": shaft_result,
            "negative_ambiguous_assessment": ambiguous_assessment,
            "negative_reconstruction": refused,
            "artifacts": [
                _artifact(path)
                for path in (
                    bracket_native,
                    bracket_step,
                    bracket_output,
                    shaft_native,
                    shaft_step,
                    shaft_output,
                    ambiguous_native,
                    ambiguous_step,
                )
            ],
        }
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assert evidence_path.is_file() and evidence_path.stat().st_size > 0
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=3.0)
