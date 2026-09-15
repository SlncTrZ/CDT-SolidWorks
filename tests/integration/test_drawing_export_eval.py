from __future__ import annotations

from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.drawing.domain import (
    AnnotationSnapshot,
    BomSnapshot,
    DimensionSnapshot,
    DrawingPostconditionError,
    DrawingSnapshot,
    DrawingUpdateSnapshot,
    SheetSnapshot,
    ViewSnapshot,
)
from cdt_solidworks.evaluation.domain import (
    BoundingBox,
    GeometrySanity,
    InterferenceSnapshot,
    MassProperties,
    MeasureSnapshot,
)
from cdt_solidworks.export.domain import (
    ArtifactInspection,
    ExportFormat,
    ExportPostconditionError,
    ImportFormat,
    ImportSnapshot,
)
from cdt_solidworks.integration.drawing_export_eval import (
    IntegratedDrawingService,
    IntegratedEvaluationService,
    IntegratedExportService,
    IntegratedImportService,
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

    def create_projected_view(self, path: str, parent_view_id: str, x: float, y: float):
        self.calls.append(("projected", path, parent_view_id, x, y))
        return ViewSnapshot("Drawing View2", "Sheet1", "projected", "part.SLDPRT", None, False, (x, y), 1.0, 2, parent_view_id)

    def create_section_view(self, path, parent_view_id, line_start, line_end, x, y, label):
        self.calls.append(("section", path, parent_view_id, line_start, line_end, x, y, label))
        return ViewSnapshot("Section View A-A", "Sheet1", "section", "part.SLDPRT", None, False, (x, y), 1.0, 2, parent_view_id)

    def add_note(self, path: str, view_id: str, text: str):
        self.calls.append(("note", path, view_id, text))
        return AnnotationSnapshot("DetailItem1", view_id, "note", text, False)

    def auto_insert_center_marks(self, path: str, view_id: str):
        self.calls.append(("center_marks", path, view_id))
        return (
            AnnotationSnapshot("DetailItem2", view_id, "center_mark", "DetailItem2", False),
        )

    def add_dimension(
        self,
        path: str,
        view_id: str,
        source_ref: str,
        *,
        source_model_path: str,
        source_configuration: str | None,
        x_mm: float,
        y_mm: float,
    ):
        self.calls.append(("dimension", path, view_id, source_model_path, source_configuration, source_ref, x_mm, y_mm))
        return DimensionSnapshot("D1@Drawing View1", view_id, source_ref, "10.00", False)

    def list_dimensions(self, path: str, view_id: str | None = None):
        self.calls.append(("dimensions", path, view_id))
        return (DimensionSnapshot("D1@Drawing View1", view_id or "Drawing View1", "swref1.opaque.reference", "10.00", False),)

    def create_bom(
        self,
        path: str,
        view_id: str,
        source_configuration: str,
        *,
        source_model_path: str,
    ):
        self.calls.append(("bom_create", path, view_id, source_model_path, source_configuration))
        return BomSnapshot("BOM1", view_id, source_configuration, 2, 2, (("ITEM", "QTY"), ("1", "2")))

    def read_bom(self, path: str, bom_id: str):
        self.calls.append(("bom_read", path, bom_id))
        return BomSnapshot(bom_id, "Drawing View1", "Default", 2, 2, (("ITEM", "QTY"), ("1", "2")))

    def update_drawing(self, path: str, *, source_model_path: str, source_configuration: str | None):
        self.calls.append(("update", path, source_model_path, source_configuration))
        return DrawingUpdateSnapshot(1, 1, 1, True)


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
            drawing_sheet=request.drawing_sheet,
            detected_extension=Path(request.target_path).suffix.lower(),
            byte_size=100,
        )


class _ImportDomain:
    def __init__(self) -> None:
        self.calls = []

    def import_model(self, request):
        self.calls.append(request)
        return ImportSnapshot(
            source_path=request.source_path,
            target_document_id=request.target_document_id,
            format=request.format,
            document_type=1,
            solid_body_count=1,
            surface_body_count=0,
            component_count=0,
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

    def measure(self, path: str, first_ref: str, second_ref: str | None = None):
        self.calls.append(("measure", path, first_ref, second_ref))
        return MeasureSnapshot(0.1 if second_ref else None, 1.57 if second_ref else None, 0.005 if second_ref is None else None, 0.01 if second_ref is None else None)

    def interferences(self, path: str, configuration: str | None = None):
        self.calls.append(("interferences", path, configuration))
        return (InterferenceSnapshot("A|B|0", "A-1", "B-1", 1e-6),)


def _files(tmp_path: Path):
    part = tmp_path / "part.SLDPRT"; part.write_bytes(b"part")
    drawing = tmp_path / "drawing.SLDDRW"; drawing.write_bytes(b"drawing")
    assembly = tmp_path / "assembly.SLDASM"; assembly.write_bytes(b"assembly")
    return part, drawing, assembly


def test_drawing_wrapper_promotes_native_accepted_views_and_annotations(tmp_path: Path) -> None:
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

    for kind in ("top", "right", "isometric"):
        standard = service.create_standard_view(str(drawing), "Sheet1", str(part), kind)
        assert standard.state is NativeCallState.SUCCESS
        assert domain.calls[-1][3] == kind

    assembly_view = service.create_standard_view(
        str(drawing), "Sheet1", str(assembly), "front"
    )
    assert assembly_view.state is NativeCallState.SUCCESS
    assert domain.calls[-1][4] == str(assembly.resolve())

    projected = service.create_projected_view(str(drawing), "Drawing View1", 0.24, 0.10)
    assert projected.state is NativeCallState.SUCCESS
    section = service.create_section_view(
        str(drawing), "Drawing View1", (0.0, -0.04), (0.0, 0.04), 0.235, 0.10, "A"
    )
    assert section.state is NativeCallState.SUCCESS
    assert service.add_note(str(drawing), "Drawing View1", "CHECK").state is NativeCallState.SUCCESS
    assert service.auto_insert_center_marks(str(drawing), "Drawing View1").state is NativeCallState.SUCCESS

    dimension = service.create_dimension(
        str(drawing),
        "Drawing View1",
        str(part),
        None,
        "swref1.opaque.reference",
        40.0,
        30.0,
    )
    assert dimension.state is NativeCallState.SUCCESS
    assert service.list_dimensions(str(drawing), "Drawing View1").state is NativeCallState.SUCCESS
    assembly_dimension = service.create_dimension(
        str(drawing),
        "Drawing View1",
        str(assembly),
        None,
        "swref1.opaque.assembly.reference",
        45.0,
        35.0,
    )
    assert assembly_dimension.state is NativeCallState.SUCCESS
    bom = service.create_bom(str(drawing), "Drawing View1", str(assembly), "Default")
    assert bom.state is NativeCallState.SUCCESS
    assert service.read_bom(str(drawing), "BOM1").state is NativeCallState.SUCCESS
    assert service.update(str(drawing), str(part)).state is NativeCallState.SUCCESS

    before = len(domain.calls)
    bad_ref = service.create_dimension(
        str(drawing), "Drawing View1", str(part), None, "Edge1", 40.0, 30.0
    )
    assert bad_ref.state is NativeCallState.FAILURE
    assert bad_ref.dispatched is False
    assert len(domain.calls) == before

    wrong_source = tmp_path / "foreign.STEP"
    wrong_source.write_bytes(b"foreign")
    refused_source = service.create_front_view(str(drawing), "Sheet1", str(wrong_source))
    assert refused_source.state is NativeCallState.FAILURE
    assert refused_source.dispatched is False

    bad_target = service.create(str(tmp_path / "bad.SLDPRT"))
    assert bad_target.state is NativeCallState.FAILURE
    assert bad_target.dispatched is False


def test_native_drawing_bom_capability_requires_real_allowed_template(tmp_path: Path) -> None:
    class _Session:
        api = object()

    policy = DocumentPathPolicy((tmp_path,))
    valid = tmp_path / "bom-standard.sldbomtbt"
    valid.write_bytes(b"template")
    wrong_extension = tmp_path / "bom-standard.txt"
    wrong_extension.write_bytes(b"template")

    configured = IntegratedDrawingService(
        _Session(), path_policy=policy, bom_template_path=str(valid)
    )
    invalid = IntegratedDrawingService(
        _Session(), path_policy=policy, bom_template_path=str(wrong_extension)
    )

    assert configured.bom_available is True
    assert invalid.bom_available is False


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

    single_sheet = service.export(
        str(drawing), str(tmp_path / "sheet1.pdf"), "pdf", drawing_sheet="Sheet1"
    )
    assert single_sheet.state is NativeCallState.SUCCESS
    assert domain.calls[-1].drawing_sheet == "Sheet1"

    before = len(domain.calls)
    assert service.export(str(assembly), str(tmp_path / "asm.step"), "step").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.pdf"), "pdf").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.stp"), "step_242").dispatched is False
    assert service.export(str(part), str(tmp_path / "part.pdf"), "step").dispatched is False
    assert len(domain.calls) == before


def test_import_wrapper_promotes_only_step_iges_and_parasolid(tmp_path: Path) -> None:
    part, _drawing, _assembly = _files(tmp_path)
    domain = _ImportDomain()
    service = IntegratedImportService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)
    cases = (("step", ".step"), ("iges", ".igs"), ("parasolid", ".x_t"))
    for fmt, suffix in cases:
        foreign = tmp_path / f"source{suffix}"; foreign.write_bytes(b"foreign")
        target = tmp_path / f"imported-{fmt}.SLDPRT"
        result = service.import_model(str(foreign), str(target), fmt)
        assert result.state is NativeCallState.SUCCESS, fmt
    assert service.import_model(str(part), str(tmp_path / "bad.SLDPRT"), "step").dispatched is False
    assert service.import_model(str(tmp_path / "source.step"), str(tmp_path / "bad.SLDDRW"), "step").dispatched is False


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
    assert service.measure(str(part), "Sketch1:segment:0", "Sketch1:segment:1").state is NativeCallState.SUCCESS
    assert service.measure(str(part), "Sketch2:segment:0").state is NativeCallState.SUCCESS

    before = len(domain.calls)
    refused = service.mass_properties(str(assembly))
    assert refused.state is NativeCallState.FAILURE
    assert refused.dispatched is False
    assert len(domain.calls) == before

    interference = service.interferences(str(assembly), "Default")
    assert interference.state is NativeCallState.SUCCESS
    wrong_interference_type = service.interferences(str(part))
    assert wrong_interference_type.state is NativeCallState.FAILURE
    assert wrong_interference_type.dispatched is False
