from __future__ import annotations

from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import (
    DrawingPostconditionError,
    DrawingSnapshot,
    SheetSnapshot,
    ViewSnapshot,
)
from cdt_solidworks.evaluation.domain import BoundingBox, GeometrySanity, MassProperties
from cdt_solidworks.export.domain import (
    ArtifactInspection,
    ExportFormat,
    ExportPostconditionError,
)
from cdt_solidworks.integration.drawing_export_eval import (
    IntegratedDrawingService,
    IntegratedEvaluationService,
    IntegratedExportService,
)
from cdt_solidworks.native.models import NativeCallState


class _DrawingDomain:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def create_drawing(self, path: str, template_path: str | None = None):
        self.calls.append(("create", path, template_path))
        return DrawingSnapshot(path, path, ("Sheet1",), template_path)

    def create_sheet(self, path: str, name: str):
        self.calls.append(("sheet", path, name))
        return SheetSnapshot(name)

    def create_view(self, path: str, sheet: str, kind: str, source: str, configuration: str | None = None):
        self.calls.append(("view", path, sheet, kind, source, configuration))
        return ViewSnapshot("Drawing View1", sheet, kind, source, configuration, False)


class _ExportDomain:
    def __init__(self) -> None:
        self.calls = []

    def export(self, request):
        self.calls.append(request)
        return ArtifactInspection(
            detected_format=request.format,
            readable=True,
            geometry_verified=request.format in {
                ExportFormat.STEP,
                ExportFormat.IGES,
                ExportFormat.PARASOLID,
                ExportFormat.STL,
                ExportFormat.THREE_MF,
            },
            source_document_id=request.source_document_id,
            source_configuration=request.source_configuration,
            drawing_sheet=None,
            detected_extension=Path(request.target_path).suffix.lower(),
            byte_size=100,
        )


class _EvaluationDomain:
    def __init__(self) -> None:
        self.calls = []

    def mass_properties(self, path: str, configuration: str | None = None):
        self.calls.append(("mass", path, configuration))
        return MassProperties(1.0, 0.001, 0.1, (0.0, 0.0, 0.0), (1, 1, 1, 0, 0, 0))

    def bounding_box(self, path: str, configuration: str | None = None):
        self.calls.append(("bounds", path, configuration))
        return BoundingBox((0, 0, 0), (1, 1, 1), approximate=True)

    def require_clean_geometry(self, path: str, configuration: str | None = None):
        self.calls.append(("sanity", path, configuration))
        return GeometrySanity(1, 0, 0, 0)


def _files(tmp_path: Path):
    part = tmp_path / "part.SLDPRT"; part.write_bytes(b"part")
    drawing = tmp_path / "drawing.SLDDRW"; drawing.write_bytes(b"drawing")
    assembly = tmp_path / "assembly.SLDASM"; assembly.write_bytes(b"assembly")
    return part, drawing, assembly


def test_drawing_wrapper_promotes_create_sheet_and_front_view_only(tmp_path: Path) -> None:
    part, drawing, assembly = _files(tmp_path)
    domain = _DrawingDomain()
    service = IntegratedDrawingService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    created = service.create(str(tmp_path / "new.SLDDRW"))
    assert created.state is NativeCallState.SUCCESS
    assert domain.calls[-1][0] == "create"

    sheet = service.create_sheet(str(drawing), "Sheet2")
    assert sheet.state is NativeCallState.SUCCESS

    view = service.create_front_view(str(drawing), "Sheet1", str(part))
    assert view.state is NativeCallState.SUCCESS
    assert domain.calls[-1][3] == "front"

    wrong_source = service.create_front_view(str(drawing), "Sheet1", str(assembly))
    assert wrong_source.state is NativeCallState.FAILURE
    assert wrong_source.dispatched is False

    bad_target = service.create(str(tmp_path / "bad.SLDPRT"))
    assert bad_target.state is NativeCallState.FAILURE
    assert bad_target.dispatched is False


def test_drawing_wrapper_preserves_uncertain_native_call_id(tmp_path: Path) -> None:
    _part, drawing, _assembly = _files(tmp_path)

    class _Uncertain(_DrawingDomain):
        def create_sheet(self, path: str, name: str):
            raise DrawingPostconditionError(
                "native_state_uncertain",
                "call_id=drawing-native-42; timeout_after_dispatch@drawing_create_sheet",
            )

    service = IntegratedDrawingService(path_policy=DocumentPathPolicy((tmp_path,)), service=_Uncertain())
    result = service.create_sheet(str(drawing), "Sheet2")
    assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
    assert result.call_id == "drawing-native-42"
    assert result.dispatched is True


def test_export_wrapper_allows_only_native_evidence_source_format_pairs(tmp_path: Path) -> None:
    part, drawing, assembly = _files(tmp_path)
    domain = _ExportDomain()
    service = IntegratedExportService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    for fmt, suffix in (("step", ".step"), ("iges", ".igs"), ("parasolid", ".x_t"), ("stl", ".stl"), ("3mf", ".3mf")):
        result = service.export(str(part), str(tmp_path / f"part{suffix}"), fmt, source_configuration="Default")
        assert result.state is NativeCallState.SUCCESS, fmt

    for fmt, suffix in (("pdf", ".pdf"), ("dxf", ".dxf"), ("dwg", ".dwg")):
        result = service.export(str(drawing), str(tmp_path / f"drawing{suffix}"), fmt)
        assert result.state is NativeCallState.SUCCESS, fmt

    before = len(domain.calls)
    assert service.export(str(assembly), str(tmp_path / "asm.step"), "step").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.pdf"), "pdf").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.stp"), "step_242").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.pdf"), "step").dispatched is False
    assert len(domain.calls) == before


def test_export_wrapper_preserves_uncertain_call_id(tmp_path: Path) -> None:
    part, _drawing, _assembly = _files(tmp_path)

    class _Uncertain(_ExportDomain):
        def export(self, request):
            raise ExportPostconditionError(
                "native_state_uncertain",
                "native_call_uncertain_after_dispatch",
                call_id="export-native-42",
            )

    service = IntegratedExportService(path_policy=DocumentPathPolicy((tmp_path,)), service=_Uncertain())
    result = service.export(str(part), str(tmp_path / "part.step"), "step")
    assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
    assert result.call_id == "export-native-42"
    assert result.dispatched is True


def test_evaluation_wrapper_is_read_only_part_surface(tmp_path: Path) -> None:
    part, _drawing, assembly = _files(tmp_path)
    domain = _EvaluationDomain()
    service = IntegratedEvaluationService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    assert service.mass_properties(str(part), "Default").state is NativeCallState.SUCCESS
    assert service.bounding_box(str(part), "Default").state is NativeCallState.SUCCESS
    assert service.geometry_sanity(str(part), "Default").state is NativeCallState.SUCCESS

    before = len(domain.calls)
    refused = service.mass_properties(str(assembly))
    assert refused.state is NativeCallState.FAILURE
    assert refused.dispatched is False
    assert len(domain.calls) == before
