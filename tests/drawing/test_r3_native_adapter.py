import unittest

from cdt_solidworks.drawing.domain import DrawingService
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


class FakeNote:
    def __init__(self, annotation):
        self.annotation = annotation

    def GetAnnotation(self):
        return self.annotation


class FakeGtolFrame:
    def __init__(self, owner):
        self.owner = owner

    def GetSymbolXml(self):
        datums = "".join(self.owner.datums)
        return f"<gtol symbol='POSI' tol='{self.owner.tolerance}'>{datums}</gtol>"


class FakeGtol:
    def __init__(self):
        self.tolerance = ""
        self.datums = ()
        self.annotation = FakeAnnotation("GTOL1", 5, "GTOL1")

    def SetFrameSymbols2(self, *args):
        return args[0] == 1 and args[1] == "<IGTOL-POSI>"

    def SetFrameValues2(self, frame, tol1, tol2, datum1, datum2, datum3):
        self.tolerance = tol1
        self.datums = tuple(item for item in (datum1, datum2, datum3) if item)
        return frame == 1

    def GetFrameCount(self):
        return 1

    def GetFrame(self, index):
        return FakeGtolFrame(self) if index == 1 else None

    def GetAnnotation(self):
        return self.annotation


class FakeFeature:
    Name = "BOM1"


class FakeTable:
    RowCount = 2
    ColumnCount = 2

    def GetFeature(self):
        return FakeFeature()

    def DisplayedText2(self, row, column, include_hidden):
        values = (("ITEM NO.", "QTY."), ("1", "2"))
        return values[row][column]


class FakeSection:
    def __init__(self, label):
        self.label = label

    def GetLabel(self):
        return self.label


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

    def GetNextView(self):
        return self._next

    def GetDisplayMode2(self):
        return 3

    def InsertBomTable5(self, *args):
        return FakeTable()

    def GetSection(self):
        return self._specific if isinstance(self._specific, FakeSection) else None


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

    def InsertGtol(self):
        return FakeGtol()

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
        self.adapter = SolidWorksDrawingAdapter(
            FakeSession(self.api, object()),
            path_policy=FakePathPolicy(),
            bom_template_path=r"C:\\templates\\bom.sldbomtbt",
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
        gtol = self.service.add_position_gtol(
            self.path, self.base.identity, "0.20", ("A", "B")
        )

        section = self.service.create_section_view(
            self.path, self.base.identity, (0.0, -0.04), (0.0, 0.04), 0.23, 0.10, "A"
        )
        self.assertEqual((0.25, 0.10), projected.position)
        self.assertEqual("section", section.view_kind)
        self.assertEqual((0.23, 0.10), section.position)
        self.assertEqual("note", note.annotation_kind)
        self.assertEqual("display_dimension", annotations[0].annotation_kind)
        self.assertEqual("gtol", gtol.annotation_kind)
        self.assertEqual("POSITION|0.20|A|B", gtol.text)
        self.assertEqual(("ITEM NO.", "QTY."), bom.rows[0])


if __name__ == "__main__":
    unittest.main()
