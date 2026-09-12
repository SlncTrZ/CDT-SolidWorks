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
            geometry_verified=request.format in {ExportFormat.STEP, ExportFormat.IGES},
            source_document_id=request.source_document_id,
            source_configuration=request.source_configuration,
            drawing_sheet=request.drawing_sheet,
            detected_extension=extension,
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
            completed=False, partial=True, errors=("translator failed",)
        )
        with self.assertRaisesRegex(ExportPostconditionError, "native_export_incomplete"):
            self.service.export(
                ExportRequest(
                    source_document_id="part-1",
                    target_path=r"C:\exports\part.step",
                    format=ExportFormat.STEP,
                )
            )

    def test_step_requires_independent_geometry_verification(self):
        self.inspector.inspection = ArtifactInspection(
            detected_format=ExportFormat.STEP,
            readable=True,
            geometry_verified=False,
            source_document_id=None,
            source_configuration=None,
            drawing_sheet=None,
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


if __name__ == "__main__":
    unittest.main()
