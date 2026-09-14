from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import DrawingService
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


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


def test_native_drawing_lifecycle_sheet_and_standard_view() -> None:
    root = Path(os.environ["CDT_SW_NATIVE_EVIDENCE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    part = root / "drawing-source.SLDPRT"
    drawing = root / "drawing-fixture.SLDDRW"
    evidence = root / "drawing-native.json"
    for path in (part, drawing, evidence):
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
                width_mm=120.0,
                height_mm=80.0,
                depth_mm=16.0,
                timeout=90.0,
            ),
            "create_rect_extrude",
        )

        adapter = SolidWorksDrawingAdapter(
            session,
            path_policy=policy,
            default_timeout=90.0,
        )
        service = DrawingService(adapter)
        snapshot = service.create_drawing(str(drawing), None)
        assert drawing.is_file() and drawing.stat().st_size > 0
        assert snapshot.sheet_names
        initial_sheet = snapshot.sheet_names[0]

        view = service.create_view(
            str(drawing),
            initial_sheet,
            "front",
            str(part),
            None,
        )
        assert view.dangling is False
        assert Path(view.source_model_path) == part
        assert view.sheet_name == initial_sheet
        assert view.view_kind == "front"

        extra_sheet = service.create_sheet(str(drawing), "AgentDSheet2")
        reread = adapter.read_drawing(str(drawing))
        assert extra_sheet.name == "AgentDSheet2"
        assert reread is not None
        assert "AgentDSheet2" in reread.sheet_names
        assert drawing.stat().st_size > 0

        evidence.write_text(
            json.dumps(
                {
                    "host": os.environ.get("COMPUTERNAME"),
                    "solidworks_revision": session_info.revision,
                    "solidworks_version_year": session_info.version_year,
                    "session_ownership": session_info.ownership.value,
                    "drawing_bytes": drawing.stat().st_size,
                    "sheets": reread.sheet_names,
                    "view": {
                        "identity": view.identity,
                        "sheet_name": view.sheet_name,
                        "view_kind": view.view_kind,
                        "source_model_path": view.source_model_path,
                        "dangling": view.dangling,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert evidence.is_file() and evidence.stat().st_size > 0
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=3.0)
