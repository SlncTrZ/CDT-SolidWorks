import unittest

from cdt_solidworks.export.domain import ExportFormat, ExportRequest
from cdt_solidworks.export.native import SolidWorksExporter
from cdt_solidworks.native.models import NativeCallResult


class Ref:
    def __init__(self, value=0):
        self.value = value


class FakePythonCom:
    VT_BYREF = 1
    VT_I4 = 2
    VT_DISPATCH = 4


class FakeClient:
    @staticmethod
    def VARIANT(_kind, value):
        return Ref(value)


class FakePdfData:
    def __init__(self):
        self.which = None
        self.sheets = None
        self.ViewPdfAfterSaving = True

    def SetSheets(self, which, sheets):
        self.which = which
        self.sheets = tuple(sheets)
        return True

    def GetSheets(self):
        return self.sheets

    def GetWhichSheets(self):
        return self.which


class FakeSheet:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class FakeExtension:
    def __init__(self, model):
        self.model = model
        self.saved = []

    def SaveAs(self, target, version, options, export_data, errors, warnings):
        self.saved.append((target, version, options, export_data))
        errors.value = 0
        warnings.value = 0
        return True


class FakeDrawing:
    def __init__(self):
        self.path = r"C:\\drawings\\fixture.SLDDRW"
        self.title = "fixture.SLDDRW"
        self.active_sheet = "Sheet1"
        self.Extension = FakeExtension(self)

    def GetTitle(self):
        return self.title

    def GetType(self):
        return 3

    def ActivateSheet(self, name):
        if name not in {"Sheet1", "Sheet2"}:
            return False
        self.active_sheet = name
        return True

    def GetCurrentSheet(self):
        return FakeSheet(self.active_sheet)


class FakeApp:
    def __init__(self, model):
        self.model = model
        self.pdf_data = FakePdfData()

    def GetExportFileData(self, file_type):
        assert file_type == 1
        return self.pdf_data


class FakeApi:
    _pythoncom = FakePythonCom()
    _client = FakeClient()

    def __init__(self, model):
        self.model = model

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if args else (member() if callable(member) else member)

    def get_open_document(self, app, path):
        return self.model

    def open_document(self, *args, **kwargs):
        raise AssertionError("already open")

    def document_type(self, model):
        return model.GetType()

    def active_configuration(self, model):
        return None

    def close_document(self, app, title):
        raise AssertionError("caller-owned drawing must stay open")


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)

    def validate_save(self, value):
        return str(value)


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app

    def execute(self, operation, *, stage, timeout, mutation):
        return NativeCallResult.success(operation(self.app), call_id=stage)


class SingleSheetPdfTests(unittest.TestCase):
    def test_single_sheet_pdf_uses_export_pdf_data_and_exact_sheet(self):
        drawing = FakeDrawing()
        app = FakeApp(drawing)
        api = FakeApi(drawing)
        exporter = SolidWorksExporter(
            FakeSession(api, app),
            path_policy=FakePathPolicy(),
            default_timeout=5.0,
        )
        result = exporter.export(
            ExportRequest(
                source_document_id=drawing.path,
                target_path=r"C:\\exports\\sheet1.pdf",
                format=ExportFormat.PDF,
                drawing_sheet="Sheet1",
            )
        )
        self.assertTrue(result.completed)
        self.assertEqual(3, app.pdf_data.which)
        self.assertEqual(1, len(app.pdf_data.sheets))
        self.assertEqual("Sheet1", app.pdf_data.sheets[0].GetName())
        self.assertFalse(app.pdf_data.ViewPdfAfterSaving)
        self.assertEqual(r"C:\\exports\\sheet1.pdf", drawing.Extension.saved[-1][0])


if __name__ == "__main__":
    unittest.main()
