import unittest

from cdt_solidworks.drawing.domain import DrawingRefusal, DrawingService
from cdt_solidworks.drawing.native import SolidWorksDrawingAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)

    def validate_save(self, value):
        return str(value)


class FakeReferencedModel:
    def __init__(self, path):
        self.path = path


class FakeSpecific:
    def __init__(self, text):
        self.text = text

    def GetText(self):
        return self.text


class FakeAnnotation:
    def __init__(self, name, type_code, text):
        self.name = name
        self.type_code = type_code
        self.specific = FakeSpecific(text)

    def GetName(self):
        return self.name

    def GetType(self):
        return self.type_code

    def GetSpecificAnnotation(self):
        return self.specific

    def GetAttachedEntityTypes(self):
        return (1,)


class FakeBadAttachmentAnnotation(FakeAnnotation):
    def GetAttachedEntityTypes(self):
        raise RuntimeError("attachment read failed")


class FakeNote:
    def __init__(self, annotation):
        self.annotation = annotation

    def GetAnnotation(self):
        return self.annotation


class FakeFeature:
    Name = "BOM1"


class FakeComponent:
    def __init__(self, select_id):
        self.select_id = select_id

    def GetSelectByIDString(self):
        return self.select_id


class FakeTable:
    RowCount = 2
    ColumnCount = 4

    def __init__(self):
        self._next = None

    def GetFeature(self):
        return FakeFeature()

    def DisplayedText2(self, row, column, include_hidden):
        values = (
            ("ITEM NO.", "QTY.", "PART NUMBER", "DESCRIPTION"),
            ("1", "2", "P-100", "PIN"),
        )
        return values[row][column]

    def GetComponents2(self, row, configuration):
        if row == 0:
            return ()
        return (FakeComponent("PIN-1@fixture"), FakeComponent("PIN-2@fixture"))

    def GetNext(self):
        return self._next


class FakeSection:
    def __init__(self, label):
        self.label = label

    def GetLabel(self):
        return self.label


class FakeDisplayDimension:
    def __init__(self, name, text="10.00"):
        self.annotation = FakeAnnotation(name, 4, text)
        self._next = None

    def GetAnnotation(self):
        return self.annotation

    def GetNext5(self):
        return self._next


class FakeCenterMark:
    def __init__(self, name):
        self.annotation = FakeAnnotation(name, 0, name)
        self._next = None

    def GetAnnotation(self):
        return self.annotation

    def GetNext(self):
        return self._next


class FakeSketchSegment:
    def __init__(self, drawing, kind):
        self.drawing = drawing
        self.kind = kind

    def Select4(self, append, data):
        self.drawing.selected_segment = self
        return True


class FakeSketchManager:
    def __init__(self, drawing):
        self.drawing = drawing

    def CreateLine(self, *args):
        return FakeSketchSegment(self.drawing, "line")


class FakeView:
    def __init__(self, name, source, *, position=(0.15, 0.10), config="Default", specific=None):
        self.Name = name
        self.ReferencedDocument = FakeReferencedModel(source)
        self.ReferencedConfiguration = config
        self.Position = position
        self.ScaleDecimal = 1.0
        self._next = None
        self._specific = specific
        self._center_marks = []
        self._display_dimensions = []
        self._tables = []

    def GetNextView(self):
        return self._next

    def GetDisplayMode2(self):
        return 3

    def InsertBomTable5(self, *args):
        table = FakeTable()
        if self._tables:
            self._tables[-1]._next = table
        self._tables.append(table)
        return table

    def GetSection(self):
        return self._specific if isinstance(self._specific, FakeSection) else None

    def AutoInsertCenterMarks2(self, *args):
        first = FakeCenterMark("DetailItem1")
        second = FakeCenterMark("DetailItem2")
        first._next = second
        self._center_marks = [first, second]
        return True

    def GetFirstCenterMark(self):
        return self._center_marks[0] if self._center_marks else None

    def GetFirstDisplayDimension5(self):
        return self._display_dimensions[0] if self._display_dimensions else None

    def GetFirstTableAnnotation(self):
        return self._tables[0] if self._tables else None


class FakeExtension:
    def __init__(self, drawing):
        self.drawing = drawing

    def SelectByID2(self, name, select_type, *args):
        self.drawing.selected_view = next(
            (view for view in self.drawing.views if view.Name == name), None
        )
        return self.drawing.selected_view is not None and select_type == "DRAWINGVIEW"


class FakeDrawing:
    def __init__(self, path):
        self.path = path
        self.title = "fixture.SLDDRW"
        self.sheet_names = ["Sheet1"]
        self.views = []
        self.selected_view = None
        self.selected_segment = None
        self.Extension = FakeExtension(self)
        self.SketchManager = FakeSketchManager(self)

    def GetType(self):
        return 3

    def GetPathName(self):
        return self.path

    def GetTitle(self):
        return self.title

    def GetSheetNames(self):
        return tuple(self.sheet_names)

    def ActivateSheet(self, name):
        return name in self.sheet_names

    def ActivateView(self, name):
        self.selected_view = next((view for view in self.views if view.Name == name), None)
        return self.selected_view is not None

    @property
    def ActiveDrawingView(self):
        return self.selected_view

    def ClearSelection2(self, _all):
        self.selected_view = None
        return True

    def CreateDrawViewFromModelView3(self, source, view_name, x, y, z):
        view = FakeView(f"View{len(self.views) + 1}", source, position=(x, y))
        self.views.append(view)
        return view

    def CreateUnfoldedViewAt3(self, x, y, z, flip):
        assert self.selected_view is not None
        view = FakeView(
            f"View{len(self.views) + 1}",
            self.selected_view.ReferencedDocument.path,
            position=(x, y),
            config=self.selected_view.ReferencedConfiguration,
        )
        self.views.append(view)
        return view

    def CreateSectionViewAt5(self, x, y, z, label, options, excluded, depth):
        assert self.selected_view is not None
        assert self.selected_segment is not None and self.selected_segment.kind == "line"
        view = FakeView(
            f"View{len(self.views) + 1}",
            self.selected_view.ReferencedDocument.path,
            position=(x, y),
            config=self.selected_view.ReferencedConfiguration,
            specific=FakeSection(label),
        )
        self.views.append(view)
        return view

    def InsertNote(self, text):
        return FakeNote(FakeAnnotation("Note1", 6, text))

    def InsertModelAnnotations4(self, *args):
        return (FakeAnnotation("D1@Sketch1", 4, "25.00"),)

    def AddDimension2(self, x, y, z):
        assert self.selected_view is not None
        display = FakeDisplayDimension(f"D{len(self.selected_view._display_dimensions) + 1}@{self.selected_view.Name}")
        if self.selected_view._display_dimensions:
            self.selected_view._display_dimensions[-1]._next = display
        self.selected_view._display_dimensions.append(display)
        return display

    def GetFirstView(self):
        sheet = FakeView("SheetFormat", "")
        current = sheet
        for view in self.views:
            current._next = view
            current = view
        return sheet


class FakeApi:
    def __init__(self, drawing):
        self.drawing = drawing

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if args else (member() if callable(member) else member)

    def get_open_document(self, app, path):
        return self.drawing

    def open_document(self, *args, **kwargs):
        raise AssertionError("fixture is already open")

    def document_type(self, model):
        return model.GetType()

    def document_path(self, model):
        return getattr(model, "path", "")

    def save_document(self, model):
        return True, 0, 0

    def force_rebuild(self, model, top_only):
        return True

    def first_feature(self, model):
        return None

    def null_dispatch(self):
        return object()


class FakeReferenceSelector:
    def __init__(self):
        self.calls = []

    def select_for_drawing(self, **kwargs):
        self.calls.append(kwargs)
        return str(kwargs["source_ref"]).startswith("swref1.")


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app

    def execute(self, operation, *, stage, timeout, mutation):
        return NativeCallResult.success(operation(self.app), call_id=stage)


class DrawingR3NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.path = r"C:\\drawings\\fixture.SLDDRW"
        self.drawing = FakeDrawing(self.path)
        self.api = FakeApi(self.drawing)
        self.selector = FakeReferenceSelector()
        self.adapter = SolidWorksDrawingAdapter(
            FakeSession(self.api, object()),
            path_policy=FakePathPolicy(),
            bom_template_path=r"C:\\templates\\bom.sldbomtbt",
            reference_selector=self.selector,
        )
        self.service = DrawingService(self.adapter)
        self.base = self.service.create_view(
            self.path,
            "Sheet1",
            "front",
            r"C:\\models\\fixture.SLDASM",
            "Default",
        )

    def test_projected_view_note_model_annotations_and_bom_have_native_readback(self):
        projected = self.service.create_projected_view(
            self.path, self.base.identity, 0.25, 0.10
        )
        note = self.service.add_note(self.path, projected.identity, "CHECK TORQUE")
        annotations = self.service.import_model_annotations(
            self.path, projected.identity
        )
        bom = self.service.create_bom(self.path, self.base.identity, "Default")
        dimension = self.service.add_dimension(
            self.path,
            self.base.identity,
            "swref1.opaque.reference",
            source_model_path=r"C:\\models\\fixture.SLDASM",
            source_configuration="Default",
            x_mm=40.0,
            y_mm=30.0,
        )
        center_marks = self.service.auto_insert_center_marks(
            self.path, self.base.identity
        )

        section = self.service.create_section_view(
            self.path, self.base.identity, (0.0, -0.04), (0.0, 0.04), 0.23, 0.10, "A"
        )
        self.assertEqual((0.25, 0.10), projected.position)
        self.assertEqual("section", section.view_kind)
        self.assertEqual((0.23, 0.10), section.position)
        self.assertEqual("note", note.annotation_kind)
        self.assertEqual("display_dimension", annotations[0].annotation_kind)
        self.assertEqual(2, len(center_marks))
        self.assertTrue(all(item.annotation_kind == "center_mark" for item in center_marks))
        self.assertEqual(("ITEM NO.", "QTY."), bom.rows[0][:2])
        self.assertEqual(("1", "2", "P-100", "PIN"), bom.rows[1])
        self.assertEqual(("PIN-1@fixture", "PIN-2@fixture"), bom.component_ids[1])
        self.assertEqual("swref1.opaque.reference", dimension.source_ref)
        self.assertEqual(1, len(self.selector.calls))
        update = self.service.update_drawing(
            self.path,
            source_model_path=r"C:\\models\\fixture.SLDASM",
            source_configuration="Default",
        )
        self.assertGreaterEqual(update.view_count, 1)
        self.assertEqual(1, update.dimension_count)
        self.assertEqual(1, update.bom_count)

    def test_attachment_read_error_is_typed_and_never_clean_false(self):
        self.drawing.InsertNote = lambda text: FakeNote(
            FakeBadAttachmentAnnotation("NoteBad", 6, text)
        )
        with self.assertRaisesRegex(Exception, "attachment_read_failed"):
            self.service.add_note(self.path, self.base.identity, "CHECK")

    def test_topology_port_missing_ambiguous_and_stale_refusals_fail_closed(self):
        for reason in (
            "topology_reference_unavailable",
            "ambiguous_topology_reference",
            "stale_topology_reference",
        ):
            with self.subTest(reason=reason):
                def reject(**kwargs):
                    raise DrawingRefusal(reason, kwargs["source_ref"])

                self.selector.select_for_drawing = reject
                before = len(self.drawing.views[0]._display_dimensions)
                with self.assertRaisesRegex(DrawingRefusal, reason):
                    self.service.add_dimension(
                        self.path,
                        self.base.identity,
                        "swref1.opaque.reference",
                        source_model_path=r"C:\\models\\fixture.SLDASM",
                        source_configuration="Default",
                        x_mm=40.0,
                        y_mm=30.0,
                    )
                self.assertEqual(before, len(self.drawing.views[0]._display_dimensions))

    def test_structure_readback_survives_lane_metadata_cache_loss(self):
        bom = self.service.create_bom(self.path, self.base.identity, "Default")
        dimension = self.service.add_dimension(
            self.path,
            self.base.identity,
            "swref1.opaque.reference",
            source_model_path=r"C:\\models\\fixture.SLDASM",
            source_configuration="Default",
            x_mm=40.0,
            y_mm=30.0,
        )
        self.adapter._bom_metadata.clear()
        self.adapter._dimension_metadata.clear()
        self.adapter._view_metadata.clear()

        views = self.adapter.list_views(self.path)
        dimensions = self.adapter.list_dimensions(self.path)
        boms = self.adapter.list_boms(self.path)

        self.assertEqual((self.base.identity,), tuple(item.identity for item in views))
        self.assertEqual((dimension.identity,), tuple(item.identity for item in dimensions))
        self.assertEqual((bom.identity,), tuple(item.identity for item in boms))
        self.assertEqual(("PIN-1@fixture", "PIN-2@fixture"), boms[0].component_ids[1])


if __name__ == "__main__":
    unittest.main()
