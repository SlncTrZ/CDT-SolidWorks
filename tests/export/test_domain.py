import unittest

from cdt_solidworks.export.domain import (
    ArtifactInspection,
    ExportFormat,
    ExportPostconditionError,
    ExportRefusal,
    ExportRequest,
    ExportService,
    NativeExportResult,
)


class FakePathPolicy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def allows(self, target_path):
        return self.allowed


class FakeExporter:
    def __init__(self):
        self.result = NativeExportResult(completed=True, partial=False, errors=())
        self.call_count = 0

    def export(self, request):
        self.call_count += 1
        return self.result


class FakeInspector:
    def __init__(self):
        self.inspection = None

    def inspect(self, request):
        if self.inspection is not None:
            return self.inspection
        extension = request.target_path[request.target_path.rfind(".") :].lower()
        return ArtifactInspection(
            detected_format=request.format,
            readable=True,
            geometry_verified=request.format
            in {
                ExportFormat.STEP,
                ExportFormat.STEP_242,
                ExportFormat.IGES,
                ExportFormat.PARASOLID,
                ExportFormat.STL,
                ExportFormat.THREE_MF,
            },
            source_document_id=request.source_document_id,
            source_configuration=request.source_configuration,
            drawing_sheet=request.drawing_sheet,
            detected_extension=extension,
            byte_size=1024,
        )


class ExportServiceTests(unittest.TestCase):
    def setUp(self):
        self.policy = FakePathPolicy()
        self.exporter = FakeExporter()
        self.inspector = FakeInspector()
        self.service = ExportService(self.exporter, self.inspector, self.policy)

    def test_extension_must_match_requested_format(self):
        with self.assertRaisesRegex(ExportRefusal, "format_extension_mismatch"):
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.pdf",
                    format=ExportFormat.STEP,
                )
            )

    def test_path_policy_blocks_before_export(self):
        self.policy.allowed = False
        with self.assertRaisesRegex(ExportRefusal, "path_not_allowed"):
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.step",
                    format=ExportFormat.STEP,
                )
            )

    def test_drawing_sheet_is_rejected_before_non_pdf_side_effect(self):
        with self.assertRaisesRegex(ExportRefusal, "drawing_sheet_requires_pdf"):
            self.service.export(
                ExportRequest(
                    source_document_id="drawing-1",
                    target_path=r"C:\\exports\\drawing.step",
                    format=ExportFormat.STEP,
                    drawing_sheet="Sheet1",
                )
            )
        self.assertEqual(0, self.exporter.call_count)

    def test_partial_native_export_cannot_pass(self):
        self.exporter.result = NativeExportResult(
            completed=False,
            partial=True,
            errors=("translator failed",),
            call_id="native-export-42",
        )
        with self.assertRaises(ExportPostconditionError) as caught:
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.step",
                    format=ExportFormat.STEP,
                )
            )
        self.assertEqual("native_state_uncertain", caught.exception.reason)
        self.assertEqual("native-export-42", caught.exception.call_id)
        self.assertIn("translator failed", caught.exception.detail or "")

    def test_step_requires_independent_geometry_verification(self):
        self.inspector.inspection = ArtifactInspection(
            detected_format=ExportFormat.STEP,
            readable=True,
            geometry_verified=False,
            source_document_id=None,
            source_configuration=None,
            drawing_sheet=None,
            byte_size=1024,
        )
        with self.assertRaisesRegex(ExportPostconditionError, "geometry_not_verified"):
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.step",
                    format=ExportFormat.STEP,
                )
            )

    def test_iges_requires_geometry_verification_and_format_readback(self):
        result = self.service.export(
            ExportRequest(
                source_document_id="part-1",
                target_path=r"C:\\exports\\part.igs",
                format=ExportFormat.IGES,
            )
        )
        self.assertTrue(result.geometry_verified)
        self.assertEqual(ExportFormat.IGES, result.detected_format)

    def test_native_export_requires_native_subtype_readback(self):
        result = self.service.export(
            ExportRequest(
                source_document_id="part-1",
                target_path=r"C:\\exports\\part.sldprt",
                format=ExportFormat.NATIVE,
            )
        )
        self.assertEqual(".sldprt", result.detected_extension)

    def test_pdf_requires_expected_sheet_and_configuration(self):
        result = self.service.export(
            ExportRequest(
                source_document_id="drawing-1",
                target_path=r"C:\exports\drawing.pdf",
                format=ExportFormat.PDF,
                source_configuration="Default",
                drawing_sheet="Sheet1",
            )
        )
        self.assertTrue(result.readable)
        self.assertEqual("Sheet1", result.drawing_sheet)
        self.assertEqual("Default", result.source_configuration)

    def test_geometry_export_matrix_accepts_independently_verified_artifacts(self):
        cases = (
            (ExportFormat.PARASOLID, r"C:\exports\part.x_t"),
            (ExportFormat.STL, r"C:\exports\part.stl"),
            (ExportFormat.THREE_MF, r"C:\exports\part.3mf"),
            (ExportFormat.STEP_242, r"C:\exports\part.stp"),
        )
        for export_format, target in cases:
            with self.subTest(export_format=export_format):
                self.inspector.inspection = ArtifactInspection(
                    detected_format=export_format,
                    readable=True,
                    geometry_verified=True,
                    source_document_id="part-1",
                    source_configuration=None,
                    drawing_sheet=None,
                    detected_extension=target[target.rfind(".") :],
                    byte_size=2048,
                )
                result = self.service.export(
                    ExportRequest(
                        source_document_id="part-1",
                        target_path=target,
                        format=export_format,
                    )
                )
                self.assertTrue(result.geometry_verified)

    def test_geometry_format_cannot_pass_without_geometry_verification(self):
        self.inspector.inspection = ArtifactInspection(
            detected_format=ExportFormat.STL,
            readable=True,
            geometry_verified=False,
            source_document_id="part-1",
            source_configuration=None,
            drawing_sheet=None,
            detected_extension=".stl",
            byte_size=2048,
        )
        with self.assertRaisesRegex(ExportPostconditionError, "geometry_not_verified"):
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.stl",
                    format=ExportFormat.STL,
                )
            )

    def test_zero_byte_artifact_is_rejected(self):
        self.inspector.inspection = ArtifactInspection(
            detected_format=ExportFormat.PDF,
            readable=True,
            geometry_verified=False,
            source_document_id="drawing-1",
            source_configuration=None,
            drawing_sheet=None,
            detected_extension=".pdf",
            byte_size=0,
        )
        with self.assertRaisesRegex(ExportPostconditionError, "artifact_empty"):
            self.service.export(
                ExportRequest(
                    source_document_id="drawing-1",
                    target_path=r"C:\exports\drawing.pdf",
                    format=ExportFormat.PDF,
                )
            )

    def test_dxf_and_dwg_extensions_are_typed(self):
        for export_format, target in (
            (ExportFormat.DXF, r"C:\exports\drawing.dxf"),
            (ExportFormat.DWG, r"C:\exports\drawing.dwg"),
        ):
            with self.subTest(export_format=export_format):
                self.inspector.inspection = ArtifactInspection(
                    detected_format=export_format,
                    readable=True,
                    geometry_verified=False,
                    source_document_id="drawing-1",
                    source_configuration=None,
                    drawing_sheet=None,
                    detected_extension=target[target.rfind(".") :],
                    byte_size=2048,
                )
                self.service.export(
                    ExportRequest(
                        source_document_id="drawing-1",
                        target_path=target,
                        format=export_format,
                    )
                )


if __name__ == "__main__":
    unittest.main()
