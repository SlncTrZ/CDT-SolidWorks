from __future__ import annotations

import hashlib
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import DrawingService
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.export.domain import (
    ExportFormat,
    ExportRequest,
    ExportService,
    ImportFormat,
    ImportRequest,
    ImportService,
)
from cdt_solidworks.export.native import (
    FileArtifactInspector,
    SolidWorksExporter,
    SolidWorksGeometryVerifier,
    SolidWorksImporter,
)
from cdt_solidworks.mbd.domain import MbdService
from cdt_solidworks.mbd.native import SolidWorksMbdAdapter
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession

EVIDENCE = ROOT / "_private" / "evidence" / "native" / "20260914" / "m95-d-r3"
ART = EVIDENCE / "artifacts"
ART.mkdir(parents=True, exist_ok=True)
OUT = EVIDENCE / "native_acceptance.json"


class ServicePathPolicy:
    def __init__(self, native: DocumentPathPolicy) -> None:
        self._native = native

    def allows(self, value: str) -> bool:
        try:
            self._native.validate_save(value)
            return True
        except Exception:
            return False


def require(result, label: str):
    if result.state is not NativeCallState.SUCCESS:
        raise RuntimeError(f"{label}: {result.state.value}: {result.failure}")
    return result.value


def artifact(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": str(path),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


report: dict[str, object] = {"ok": False}
session = SolidWorksSession()
try:
    info = require(
        session.connect(
            policy=AttachPolicy.ATTACH_OR_START,
            version=2024,
            visible=True,
            timeout=180.0,
        ),
        "connect",
    )
    policy = DocumentPathPolicy((ART,))
    service_policy = ServicePathPolicy(policy)

    source_part = ART / "lane-d-source.SLDPRT"
    drawing_path = ART / "lane-d-drawing.SLDDRW"
    pdf_path = ART / "lane-d-sheet1.pdf"
    step_path = ART / "lane-d-source.step"
    imported_part = ART / "lane-d-imported.SLDPRT"
    for path in (source_part, drawing_path, pdf_path, step_path, imported_part):
        if path.exists():
            path.unlink()

    require(
        CadCoreService(
            session,
            path_policy=policy,
            default_timeout=90.0,
        ).create_rect_extrude(
            source_part,
            width_mm=120.0,
            height_mm=80.0,
            depth_mm=16.0,
            timeout=90.0,
        ),
        "fixture part",
    )

    drawing = DrawingService(
        SolidWorksDrawingAdapter(
            session,
            path_policy=policy,
            default_timeout=90.0,
        )
    )
    drawing_snapshot = drawing.create_drawing(str(drawing_path), None)
    sheet_name = drawing_snapshot.sheet_names[0]
    front = drawing.create_view(
        str(drawing_path),
        sheet_name,
        "front",
        str(source_part),
        None,
    )
    projected = drawing.create_projected_view(
        str(drawing_path), front.identity, 0.24, 0.10
    )
    note = drawing.add_note(
        str(drawing_path), projected.identity, "LANE D NATIVE NOTE"
    )
    if projected.parent_view_id != front.identity or projected.position is None:
        raise RuntimeError(f"projected view readback invalid: {projected}")
    if note.text != "LANE D NATIVE NOTE" or note.dangling:
        raise RuntimeError(f"note readback invalid: {note}")

    exporter = SolidWorksExporter(
        session,
        path_policy=policy,
        default_timeout=120.0,
    )
    pdf_service = ExportService(
        exporter,
        FileArtifactInspector(),
        service_policy,
    )
    pdf = pdf_service.export(
        ExportRequest(
            source_document_id=str(drawing_path),
            target_path=str(pdf_path),
            format=ExportFormat.PDF,
            drawing_sheet=sheet_name,
        )
    )
    if not pdf.readable or pdf.byte_size <= 0 or pdf.drawing_sheet != sheet_name:
        raise RuntimeError(f"single-sheet PDF verification failed: {pdf}")

    geometry_verifier = SolidWorksGeometryVerifier(
        session,
        path_policy=policy,
        default_timeout=120.0,
    )
    step_service = ExportService(
        exporter,
        FileArtifactInspector(geometry_verifier),
        service_policy,
    )
    step = step_service.export(
        ExportRequest(
            source_document_id=str(source_part),
            target_path=str(step_path),
            format=ExportFormat.STEP,
            source_configuration="Default",
        )
    )
    if not step.readable or not step.geometry_verified:
        raise RuntimeError(f"STEP export verification failed: {step}")

    imported = ImportService(
        SolidWorksImporter(
            session,
            path_policy=policy,
            default_timeout=120.0,
        ),
        service_policy,
    ).import_model(
        ImportRequest(
            source_path=str(step_path),
            target_document_id=str(imported_part),
            format=ImportFormat.STEP,
        )
    )
    if imported.solid_body_count + imported.surface_body_count < 1:
        raise RuntimeError(f"imported geometry missing: {imported}")

    mbd = MbdService(
        SolidWorksMbdAdapter(
            session,
            path_policy=policy,
            default_timeout=90.0,
        )
    ).query_pmi(str(source_part), "Default")

    artifact_paths = (source_part, drawing_path, pdf_path, step_path, imported_part)
    report = {
        "ok": True,
        "verdict": "NATIVE_PASS",
        "host": "Slnc_TrZ",
        "solidworks_revision": info.revision,
        "solidworks_version_year": info.version_year,
        "session_ownership": info.ownership.value,
        "drawing": {
            "sheet": sheet_name,
            "front_view": front.identity,
            "projected_view": projected.identity,
            "projected_position": projected.position,
            "projected_scale": projected.scale_decimal,
            "projected_display_style": projected.display_style,
            "note": {
                "identity": note.identity,
                "text": note.text,
                "dangling": note.dangling,
            },
        },
        "single_sheet_pdf": {
            "readable": pdf.readable,
            "byte_size": pdf.byte_size,
            "sheet": pdf.drawing_sheet,
        },
        "interop": {
            "step_readable": step.readable,
            "step_geometry_verified": step.geometry_verified,
            "import_document_type": imported.document_type,
            "import_solid_body_count": imported.solid_body_count,
            "import_surface_body_count": imported.surface_body_count,
            "import_component_count": imported.component_count,
        },
        "mbd": {
            "configuration": mbd.configuration,
            "annotation_count": len(mbd.annotations),
            "kinds": [item.kind.value for item in mbd.annotations],
        },
        "artifacts": [artifact(path) for path in artifact_paths],
    }
except BaseException as exc:
    report = {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
finally:
    try:
        session.disconnect(timeout=15.0)
    except Exception:
        pass
    try:
        session.close_dispatcher(timeout=5.0)
    except Exception:
        pass
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

if not report["ok"]:
    raise SystemExit(1)
