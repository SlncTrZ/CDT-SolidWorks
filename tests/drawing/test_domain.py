import unittest

from cdt_solidworks.drawing.domain import (
    BomSnapshot,
    DimensionSnapshot,
    DrawingPostconditionError,
    DrawingSnapshot,
    DrawingRefusal,
    DrawingService,
    RebuildReport,
    SheetSnapshot,
    ViewSnapshot,
)


class FakeDrawingAdapter:
    def __init__(self):
        self.sheets = {}
        self.views = {}
        self.dimensions = {}
        self.boms = {}
        self.bom_supported = False
        self.rebuild = RebuildReport(ok=True)
        self.force_dangling_view = False
        self.drawings = {}

    def create_drawing(self, drawing_id, template_path):
        self.drawings[drawing_id] = DrawingSnapshot(
            identity=drawing_id,
            path=drawing_id,
            sheet_names=("Sheet1",),
            template_path=template_path,
        )

    def read_drawing(self, drawing_id):
        return self.drawings.get(drawing_id)

    def create_sheet(self, drawing_id, sheet_name):
        self.sheets[sheet_name] = SheetSnapshot(name=sheet_name)

    def read_sheet(self, drawing_id, sheet_name):
        return self.sheets.get(sheet_name)

    def create_view(
        self,
        drawing_id,
        sheet_name,
        view_kind,
        source_model_path,
        source_configuration,
    ):
        view_id = "view-1"
        self.views[view_id] = ViewSnapshot(
            identity=view_id,
            sheet_name=sheet_name,
            view_kind=view_kind,
            source_model_path=source_model_path,
            source_configuration=source_configuration,
            dangling=self.force_dangling_view,
        )
        return view_id

    def read_view(self, drawing_id, view_id):
        return self.views.get(view_id)

    def add_dimension(self, drawing_id, view_id, source_ref):
        dimension_id = "dim-1"
        self.dimensions[dimension_id] = DimensionSnapshot(
            identity=dimension_id,
            view_id=view_id,
            source_ref=source_ref,
            display_text="25.00",
            dangling=False,
        )
        return dimension_id

    def read_dimension(self, drawing_id, dimension_id):
        return self.dimensions.get(dimension_id)

    def supports_bom(self, drawing_id):
        return self.bom_supported

    def create_bom(self, drawing_id, view_id, source_configuration):
        bom_id = "bom-1"
        self.boms[bom_id] = BomSnapshot(
            identity=bom_id,
            view_id=view_id,
            source_configuration=source_configuration,
            row_count=3,
        )
        return bom_id

    def read_bom(self, drawing_id, bom_id):
        return self.boms.get(bom_id)

    def rebuild_drawing(self, drawing_id):
        return self.rebuild


class DrawingServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeDrawingAdapter()
        self.service = DrawingService(self.adapter)
        self.service.create_sheet("drawing-1", "Sheet1")

    def test_create_drawing_requires_native_readback_identity_and_template(self):
        result = self.service.create_drawing(
            r"C:\\drawings\\fixture.SLDDRW",
            r"C:\\templates\\a3.drwdot",
        )
        self.assertEqual(r"C:\\drawings\\fixture.SLDDRW", result.path)
        self.assertEqual(("Sheet1",), result.sheet_names)
        self.assertEqual(r"C:\\templates\\a3.drwdot", result.template_path)

    def test_create_drawing_rejects_blank_template_before_dispatch(self):
        with self.assertRaisesRegex(DrawingRefusal, "invalid_template_path"):
            self.service.create_drawing(r"C:\\drawings\\fixture.SLDDRW", "")

    def test_view_preserves_source_association(self):
        view = self.service.create_view(
            "drawing-1",
            "Sheet1",
            "front",
            r"C:\fixtures\assembly.SLDASM",
            "Default",
        )
        self.assertEqual(r"C:\fixtures\assembly.SLDASM", view.source_model_path)
        self.assertEqual("Default", view.source_configuration)

    def test_dangling_view_cannot_pass(self):
        self.adapter.force_dangling_view = True
        with self.assertRaisesRegex(DrawingPostconditionError, "dangling_view"):
            self.service.create_view(
                "drawing-1",
                "Sheet1",
                "front",
                r"C:\fixtures\assembly.SLDASM",
                "Default",
            )

    def test_dimension_requires_readback(self):
        view = self.service.create_view(
            "drawing-1",
            "Sheet1",
            "front",
            r"C:\fixtures\assembly.SLDASM",
            "Default",
        )
        dimension = self.service.add_dimension(
            "drawing-1", view.identity, "Edge1"
        )
        self.assertEqual(view.identity, dimension.view_id)
        self.assertEqual("25.00", dimension.display_text)

    def test_bom_unsupported_is_typed_refusal(self):
        view = self.service.create_view(
            "drawing-1",
            "Sheet1",
            "front",
            r"C:\fixtures\assembly.SLDASM",
            "Default",
        )
        with self.assertRaisesRegex(DrawingRefusal, "unsupported_capability"):
            self.service.create_bom(
                "drawing-1", view.identity, source_configuration="Default"
            )

    def test_rebuild_failure_prevents_success(self):
        self.adapter.rebuild = RebuildReport(ok=False, errors=("view rebuild failed",))
        with self.assertRaisesRegex(DrawingPostconditionError, "rebuild_failed"):
            self.service.create_view(
                "drawing-1",
                "Sheet1",
                "front",
                r"C:\fixtures\assembly.SLDASM",
                "Default",
            )


if __name__ == "__main__":
    unittest.main()
