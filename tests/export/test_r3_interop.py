import unittest

from cdt_solidworks.export.domain import (
    ImportFormat,
    ImportPostconditionError,
    ImportRequest,
    ImportService,
)
from cdt_solidworks.export.native import SolidWorksImporter
from cdt_solidworks.native.models import NativeCallResult


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)

    def validate_save(self, value):
        return str(value)

    def allows(self, value):
        return True


class Ref:
    def __init__(self, value=0):
        self.value = value


class FakePythonCom:
    VT_BYREF = 1
    VT_I4 = 2


class FakeClient:
    @staticmethod
    def VARIANT(_kind, value):
        return Ref(value)


class FakeImportedModel:
    def __init__(self):
        self.title = "foreign.SLDPRT"
        self.path = ""
        self.saved = False

    def GetTitle(self):
        return self.title

    def GetType(self):
        return 1


class FakeApp:
    def __init__(self, model):
        self.model = model
        self.import_data = object()

    def GetImportFileData(self, source):
        return self.import_data

    def LoadFile4(self, source, options, import_data, errors):
        self.last_load = (source, options, import_data)
        errors.value = 0
        return self.model


class FakeApi:
    _pythoncom = FakePythonCom()
    _client = FakeClient()

    def __init__(self, model):
        self.model = model
        self.saved_targets = []
        self.closed = []

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if args else (member() if callable(member) else member)

    def document_type(self, model):
        return model.GetType()

    def bodies(self, model, body_type, visible_only):
        return (object(),) if body_type == 0 else ()

    def components(self, model, top_level_only):
        return ()

    def save_as(self, model, target):
        self.saved_targets.append(target)
        model.path = target
        model.saved = True
        return True, 0, 0

    def document_path(self, model):
        return model.path

    def close_document(self, app, title):
        self.closed.append(title)
        return True

    def null_dispatch(self):
        return object()


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app

    def execute(self, operation, *, stage, timeout, mutation):
        return NativeCallResult.success(operation(self.app), call_id=stage)


class SolidWorksImporterTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeImportedModel()
        self.api = FakeApi(self.model)
        self.app = FakeApp(self.model)
        self.importer = SolidWorksImporter(
            FakeSession(self.api, self.app),
            path_policy=FakePathPolicy(),
            default_timeout=10.0,
        )
        self.service = ImportService(self.importer, FakePathPolicy())

    def test_step_import_requires_geometry_and_persisted_native_target(self):
        result = self.service.import_model(
            ImportRequest(
                source_path=r"C:\\exchange\\fixture.step",
                target_document_id=r"C:\\models\\fixture.SLDPRT",
                format=ImportFormat.STEP,
            )
        )
        self.assertEqual(1, result.solid_body_count)
        self.assertEqual(r"C:\\models\\fixture.SLDPRT", result.target_document_id)
        self.assertEqual([r"C:\\models\\fixture.SLDPRT"], self.api.saved_targets)

    def test_import_without_geometry_is_not_success(self):
        self.api.bodies = lambda *args: ()
        with self.assertRaisesRegex(ImportPostconditionError, "import_geometry_missing"):
            self.service.import_model(
                ImportRequest(
                    source_path=r"C:\\exchange\\fixture.iges",
                    target_document_id=r"C:\\models\\fixture.SLDPRT",
                    format=ImportFormat.IGES,
                )
            )


if __name__ == "__main__":
    unittest.main()
