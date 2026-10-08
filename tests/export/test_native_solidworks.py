from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import DrawingService
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.export.domain import ExportFormat, ExportRequest, ExportService
from cdt_solidworks.export.native import (
    FileArtifactInspector,
    SolidWorksExporter,
    SolidWorksGeometryVerifier,
)
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("CDT_SW_NATIVE_EVIDENCE_ROOT"),
    reason="native SOLIDWORKS evidence requires Windows and CDT_SW_NATIVE_EVIDENCE_ROOT",
)


class _ExportPathGate:
    def __init__(self, policy: DocumentPathPolicy) -> None:
        self._policy = policy

    def allows(self, target_path: str) -> bool:
        try:
            self._policy.validate_save(target_path)
        except Exception:
            return False
        return True


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


def test_native_step_roundtrip_and_pdf_artifact() -> None:
    root = Path(os.environ["CDT_SW_NATIVE_EVIDENCE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    part = root / "export-source.SLDPRT"
    geometry_targets = {
        ExportFormat.STEP: root / "export-source.step",
        ExportFormat.IGES: root / "export-source.igs",
        ExportFormat.PARASOLID: root / "export-source.x_t",
        ExportFormat.STL: root / "export-source.stl",
        ExportFormat.THREE_MF: root / "export-source.3mf",
    }
    drawing = root / "export-drawing.SLDDRW"
    pdf = root / "export-drawing.pdf"
    dxf = root / "export-drawing.dxf"
    dwg = root / "export-drawing.dwg"
    evidence = root / "export-native.json"
    for path in (part, *geometry_targets.values(), drawing, pdf, dxf, dwg, evidence):
        if path.exists():
            path.unlink()

    policy = DocumentPathPolicy((root,))
    session = SolidWorksSession()
    try:
        session_info = _require_success(
            session.connect(
                policy=AttachPolicy.ATTACH_OR_START,
                version=2024,
                visible=True,
                timeout=120.0,
            ),
            "connect",
        )
        _require_success(
            CadCoreService(
                session,
                path_policy=policy,
                default_timeout=90.0,
            ).create_rect_extrude(
                part,
                width_mm=90.0,
                height_mm=50.0,
                depth_mm=12.0,
                timeout=90.0,
            ),
            "create_rect_extrude",
        )

        exporter = SolidWorksExporter(
            session,
            path_policy=policy,
            default_timeout=90.0,
        )
        verifier = SolidWorksGeometryVerifier(
            session,
            path_policy=policy,
            default_timeout=90.0,
        )
        service = ExportService(
            exporter,
            FileArtifactInspector(geometry_verifier=verifier),
            _ExportPathGate(policy),
        )
        geometry_results = {}
        for export_format, target in geometry_targets.items():
            result = service.export(
                ExportRequest(
                    source_document_id=str(part),
                    target_path=str(target),
                    format=export_format,
                    source_configuration="Default",
                )
            )
            assert result.readable is True
            assert result.geometry_verified is True
            assert result.byte_size is not None and result.byte_size > 0
            geometry_results[export_format.value] = result
        assert geometry_targets[ExportFormat.STEP].read_bytes()[:64].lstrip().upper().startswith(b"ISO-10303-21")

        drawing_service = DrawingService(
            SolidWorksDrawingAdapter(
                session,
                path_policy=policy,
                default_timeout=90.0,
            )
        )
        drawing_snapshot = drawing_service.create_drawing(str(drawing), None)
        drawing_service.create_view(
            str(drawing),
            drawing_snapshot.sheet_names[0],
            "front",
            str(part),
            None,
        )
        drawing_exports = {}
        for export_format, target in (
            (ExportFormat.PDF, pdf),
            (ExportFormat.DXF, dxf),
            (ExportFormat.DWG, dwg),
        ):
            result = service.export(
                ExportRequest(
                    source_document_id=str(drawing),
                    target_path=str(target),
                    format=export_format,
                )
            )
            assert result.readable is True
            assert result.byte_size is not None and result.byte_size > 0
            drawing_exports[export_format.value] = result
        assert pdf.read_bytes().startswith(b"%PDF-")

        step_242_native = exporter.export(
            ExportRequest(
                source_document_id=str(part),
                target_path=str(root / "refused-step242.stp"),
                format=ExportFormat.STEP_242,
            )
        )
        assert step_242_native.completed is False
        assert "step_242_requires_pmi_publish_path" in step_242_native.errors

        single_sheet_native = exporter.export(
            ExportRequest(
                source_document_id=str(drawing),
                target_path=str(root / "refused-sheet.pdf"),
                format=ExportFormat.PDF,
                drawing_sheet=drawing_snapshot.sheet_names[0],
            )
        )
        assert single_sheet_native.completed is False
        assert "single_sheet_pdf_requires_export_data" in single_sheet_native.errors

        evidence.write_text(
            json.dumps(
                {
                    "host": os.environ.get("COMPUTERNAME"),
                    "solidworks_revision": session_info.revision,
                    "solidworks_version_year": session_info.version_year,
                    "session_ownership": session_info.ownership.value,
                    "geometry_exports": {
                        name: {
                            "readable": result.readable,
                            "geometry_verified": result.geometry_verified,
                            "detected_extension": result.detected_extension,
                            "byte_size": result.byte_size,
                        }
                        for name, result in geometry_results.items()
                    },
                    "drawing_exports": {
                        name: {
                            "readable": result.readable,
                            "detected_extension": result.detected_extension,
                            "byte_size": result.byte_size,
                        }
                        for name, result in drawing_exports.items()
                    },
                    "negative": {
                        "step_242": list(step_242_native.errors),
                        "single_sheet_pdf": list(single_sheet_native.errors),
                    },
                    "artifacts": [
                        _artifact(path)
                        for path in (
                            part,
                            *geometry_targets.values(),
                            drawing,
                            pdf,
                            dxf,
                            dwg,
                        )
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert evidence.is_file() and evidence.stat().st_size > 0
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=3.0)
