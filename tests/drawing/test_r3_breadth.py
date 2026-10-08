import unittest

from cdt_solidworks.drawing.domain import (
    AnnotationSnapshot,
    BomSnapshot,
    DrawingPostconditionError,
    DrawingService,
    RebuildReport,
    SheetSnapshot,
    ViewSnapshot,
)


class BreadthAdapter:
    def __init__(self):
        self.sheets = {"Sheet1": SheetSnapshot("Sheet1")}
        self.views = {
            "base": ViewSnapshot(
                identity="base",
                sheet_name="Sheet1",
                view_kind="front",
                source_model_path=r"C:\\models\\fixture.SLDASM",
                source_configuration="Default",
                dangling=False,
                position=(0.10, 0.10),
                scale_decimal=1.0,
                display_style=3,
                parent_view_id=None,
            )
        }
        self.annotations = {}
        self.boms = {}
        self.bom_supported = True

    def create_drawing(self, drawing_id, template_path):
        raise AssertionError("not used")

    def read_drawing(self, drawing_id):
        return None

    def create_sheet(self, drawing_id, sheet_name):
        self.sheets[sheet_name] = SheetSnapshot(sheet_name)

    def read_sheet(self, drawing_id, sheet_name):
        return self.sheets.get(sheet_name)

    def create_view(self, *args, **kwargs):
        raise AssertionError("not used")

    def create_projected_view(self, drawing_id, parent_view_id, x, y):
        return self._derived_view("projected-1", "projected", parent_view_id, x, y)

    def create_section_view(
        self, drawing_id, parent_view_id, line_start, line_end, x, y, label
    ):
        return self._derived_view("section-1", "section", parent_view_id, x, y)

    def _derived_view(self, view_id, kind, parent_view_id, x, y):
        parent = self.views[parent_view_id]
        self.views[view_id] = ViewSnapshot(
            identity=view_id,
            sheet_name=parent.sheet_name,
            view_kind=kind,
            source_model_path=parent.source_model_path,
            source_configuration=parent.source_configuration,
            dangling=False,
            position=(x, y),
            scale_decimal=parent.scale_decimal,
            display_style=parent.display_style,
            parent_view_id=parent_view_id,
        )
        return view_id

    def read_view(self, drawing_id, view_id):
        return self.views.get(view_id)

    def add_note(self, drawing_id, view_id, text):
        annotation_id = "note-1"
        self.annotations[annotation_id] = AnnotationSnapshot(
            identity=annotation_id,
            view_id=view_id,
            annotation_kind="note",
            text=text,
            dangling=False,
        )
        return annotation_id

    def import_model_annotations(self, drawing_id, view_id):
        annotation_id = "model-dim-1"
        self.annotations[annotation_id] = AnnotationSnapshot(
            identity=annotation_id,
            view_id=view_id,
            annotation_kind="display_dimension",
            text="25.00",
            dangling=False,
        )
        return (annotation_id,)

    def auto_insert_center_marks(self, drawing_id, view_id):
        ids = ("center-1", "center-2")
        for annotation_id in ids:
            self.annotations[annotation_id] = AnnotationSnapshot(
                identity=annotation_id,
                view_id=view_id,
                annotation_kind="center_mark",
                text=annotation_id,
                dangling=False,
            )
        return ids

    def read_annotation(self, drawing_id, annotation_id):
        return self.annotations.get(annotation_id)

    def add_dimension(self, drawing_id, view_id, source_ref):
        raise AssertionError("legacy path not used")

    def read_dimension(self, drawing_id, dimension_id):
        return None

    def supports_bom(self, drawing_id):
        return self.bom_supported

    def create_bom(self, drawing_id, view_id, source_configuration):
        bom_id = "bom-1"
        self.boms[bom_id] = BomSnapshot(
            identity=bom_id,
            view_id=view_id,
            source_configuration=source_configuration,
            row_count=3,
            column_count=2,
            rows=(("ITEM NO.", "QTY."), ("1", "2"), ("2", "1")),
        )
        return bom_id

    def read_bom(self, drawing_id, bom_id):
        return self.boms.get(bom_id)

    def rebuild_drawing(self, drawing_id):
        return RebuildReport(ok=True)


class DrawingR3BreadthTests(unittest.TestCase):
    def setUp(self):
        self.adapter = BreadthAdapter()
        self.service = DrawingService(self.adapter)

    def test_projected_view_preserves_parent_source_and_position(self):
        result = self.service.create_projected_view("drawing", "base", 0.21, 0.10)
        self.assertEqual("projected", result.view_kind)
        self.assertEqual("base", result.parent_view_id)
        self.assertEqual((0.21, 0.10), result.position)
        self.assertEqual(r"C:\\models\\fixture.SLDASM", result.source_model_path)

    def test_projected_view_rejects_non_finite_position_before_dispatch(self):
        with self.assertRaisesRegex(Exception, "invalid_view_position"):
            self.service.create_projected_view("drawing", "base", float("nan"), 0.1)

    def test_section_view_preserves_parent_source_and_position(self):
        result = self.service.create_section_view(
            "drawing", "base", (0.0, -0.04), (0.0, 0.04), 0.24, 0.10, "A"
        )
        self.assertEqual("section", result.view_kind)
        self.assertEqual("base", result.parent_view_id)
        self.assertEqual((0.24, 0.10), result.position)

    def test_section_view_rejects_degenerate_line_before_dispatch(self):
        with self.assertRaisesRegex(Exception, "invalid_section_line"):
            self.service.create_section_view(
                "drawing", "base", (0.0, 0.0), (0.0, 0.0), 0.24, 0.10, "A"
            )

    def test_note_requires_non_dangling_readback(self):
        result = self.service.add_note("drawing", "base", "CHECK TORQUE")
        self.assertEqual("note", result.annotation_kind)
        self.assertEqual("CHECK TORQUE", result.text)

    def test_auto_center_marks_require_unique_non_dangling_readback(self):
        result = self.service.auto_insert_center_marks("drawing", "base")
        self.assertEqual(2, len(result))
        self.assertEqual(("center-1", "center-2"), tuple(item.identity for item in result))
        self.assertTrue(all(item.annotation_kind == "center_mark" for item in result))
        self.assertTrue(all(not item.dangling for item in result))

    def test_import_model_annotations_requires_at_least_one_verified_annotation(self):
        result = self.service.import_model_annotations("drawing", "base")
        self.assertEqual(1, len(result))
        self.assertEqual("display_dimension", result[0].annotation_kind)

    def test_bom_requires_column_and_cell_readback(self):
        result = self.service.create_bom("drawing", "base", "Default")
        self.assertEqual(2, result.column_count)
        self.assertEqual("QTY.", result.rows[0][1])

    def test_bom_shape_mismatch_is_postcondition_failure(self):
        self.adapter.boms["forced"] = BomSnapshot(
            identity="forced",
            view_id="base",
            source_configuration="Default",
            row_count=2,
            column_count=2,
            rows=(("A", "B"),),
        )
        self.adapter.create_bom = lambda *args: "forced"
        with self.assertRaisesRegex(DrawingPostconditionError, "bom_table_shape_mismatch"):
            self.service.create_bom("drawing", "base", "Default")


if __name__ == "__main__":
    unittest.main()
