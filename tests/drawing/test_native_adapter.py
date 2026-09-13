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

    def GetPathName(self):
        return self.path


class FakeView:
    def __init__(self, name, source_path, configuration=None):
        self.Name = name
        self.ReferencedDocument = FakeReferencedModel(source_path)
        self.ReferencedConfiguration = configuration or "Default"
        self._next = None

    def GetNextView(self):
        return self._next


class FakeDrawing:
    def __init__(self, path):
        self.path = path
        self.title = "fixture.SLDDRW"
        self.sheet_names = ["Sheet1"]
        self.active_sheet = "Sheet1"
        self.views = []
        self.saved = False

    def GetType(self):
        return 3

    def GetPathName(self):
        return self.path if self.saved else ""

    def GetTitle(self):
        return self.title

    def GetSheetNames(self):
        return tuple(self.sheet_names)

    def ActivateSheet(self, name):
        if name not in self.sheet_names:
            return False
        self.active_sheet = name
        return True

    def NewSheet3(self, name, *args):
        if name in self.sheet_names:
            return False
        self.sheet_names.append(name)
        self.active_sheet = name
        return True

    def CreateDrawViewFromModelView3(self, source, view_name, x, y, z):
        view = FakeView(f"Drawing View{len(self.views) + 1}", source)
        self.views.append(view)
        return view

    def GetFirstView(self):
        sheet_view = FakeView("SheetFormat", "")
        current = sheet_view
        for view in self.views:
            current._next = view
            current = view
        return sheet_view


class FakeApp:
    def __init__(self, drawing):
        self.drawing = drawing
        self.template = r"C:\templates\default.drwdot"

    def GetDocumentTemplate(self, *args):
        return self.template

    def NewDocument(self, template, paper_size, width, height):
        return self.drawing


class FakeApi:
    def __init__(self, drawing):
        self.drawing = drawing

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def get_open_document(self, app, path):
        return self.drawing if self.drawing.saved else None

    def open_document(self, app, path, doc_type, **kwargs):
        return (self.drawing, 0, 0) if self.drawing.saved else (None, 1, 0)

    def save_as(self, model, target):
        model.path = target
        model.saved = True
        return True, 0, 0

    def save_document(self, model):
        return True, 0, 0

    def close_document(self, app, title):
        return True

    def document_type(self, model):
        return model.GetType()

    def document_path(self, model):
        return model.GetPathName()

    def document_title(self, model):
        return model.GetTitle()

    def force_rebuild(self, model, top_only):
        return True

    def first_feature(self, model):
        return None


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app

    def execute(self, operation, *, stage, timeout, mutation):
        return NativeCallResult.success(operation(self.app), call_id=stage)


class SolidWorksDrawingAdapterTests(unittest.TestCase):
    def setUp(self):
        self.target = r"C:\drawings\fixture.SLDDRW"
        self.drawing = FakeDrawing(self.target)
        self.app = FakeApp(self.drawing)
        self.api = FakeApi(self.drawing)
        self.session = FakeSession(self.api, self.app)
        self.adapter = SolidWorksDrawingAdapter(
            self.session,
            path_policy=FakePathPolicy(),
            default_timeout=5.0,
        )
        self.service = DrawingService(self.adapter)

    def test_create_drawing_from_explicit_template_and_readback(self):
        result = self.service.create_drawing(self.target, r"C:\templates\a3.drwdot")
        self.assertEqual(self.target, result.path)
        self.assertEqual(("Sheet1",), result.sheet_names)

    def test_create_sheet_is_persisted_and_read_back(self):
        self.service.create_drawing(self.target, r"C:\templates\a3.drwdot")
        sheet = self.service.create_sheet(self.target, "Sheet2")
        self.assertEqual("Sheet2", sheet.name)
        self.assertIn("Sheet2", self.drawing.sheet_names)

    def test_standard_front_view_preserves_source_association(self):
        self.service.create_drawing(self.target, r"C:\templates\a3.drwdot")
        view = self.service.create_view(
            self.target,
            "Sheet1",
            "front",
            r"C:\models\part.SLDPRT",
            None,
        )
        self.assertFalse(view.dangling)
        self.assertEqual(r"C:\models\part.SLDPRT", view.source_model_path)
        self.assertEqual("front", view.view_kind)


if __name__ == "__main__":
    unittest.main()
