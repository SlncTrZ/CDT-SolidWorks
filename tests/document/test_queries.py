import os
import tempfile
import unittest
from pathlib import Path

from cdt_solidworks.document.models import DocumentType
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.document.service import DocumentService
from cdt_solidworks.native.errors import failure_from_exception
from cdt_solidworks.native.models import NativeCallResult, NativeCallState


class Feature:
    def __init__(self, name, type_name, error_code=0, warning=False, next_feature=None) -> None:
        self.name = name
        self.type_name = type_name
        self.error_code = error_code
        self.warning = warning
        self.next_feature = next_feature


class Body:
    def __init__(self, name) -> None:
        self.name = name


class Component:
    def __init__(self, name, path, suppressed=False) -> None:
        self.name = name
        self.path = path
        self.suppressed = suppressed


class Doc:
    def __init__(self, path, doc_type) -> None:
        self.path = os.path.realpath(path)
        self.title = os.path.basename(path)
        self.doc_type = int(doc_type)
        self.configuration = "Default"
        self.update_stamp = 1
        self.dirty = False
        self.first = None
        self.solid_bodies = []
        self.sheet_bodies = []
        self.components = []
        self.rebuild_ok = True


class App:
    def __init__(self, doc) -> None:
        self.doc = doc


class Api:
    def get_open_document(self, app, path_or_title):
        if os.path.realpath(path_or_title) == app.doc.path or path_or_title == app.doc.title:
            return app.doc
        return None

    def open_document(self, *args, **kwargs):
        raise AssertionError("document is already open in this test")

    def document_path(self, doc):
        return doc.path

    def document_title(self, doc):
        return doc.title

    def document_type(self, doc):
        return doc.doc_type

    def active_configuration(self, doc):
        return doc.configuration

    def update_stamp(self, doc):
        return doc.update_stamp

    def document_dirty(self, doc):
        return doc.dirty

    def first_feature(self, doc):
        return doc.first

    def next_feature(self, feature):
        return feature.next_feature

    def feature_name(self, feature):
        return feature.name

    def feature_type(self, feature):
        return feature.type_name

    def feature_error(self, feature):
        return feature.error_code, feature.warning

    def bodies(self, doc, body_type: int, visible_only: bool):
        return doc.solid_bodies if body_type == 0 else doc.sheet_bodies

    def body_name(self, body):
        return body.name

    def components(self, doc, top_level_only: bool):
        return doc.components

    def component_name(self, component):
        return component.name

    def component_path(self, component):
        return component.path

    def component_suppressed(self, component):
        return component.suppressed

    def force_rebuild(self, doc, top_only: bool):
        return doc.rebuild_ok


class Session:
    def __init__(self, app, api) -> None:
        self.app = app
        self.api = api
        self.session_id = "q-session"
        self.calls = 0

    def execute(self, operation, *, stage, timeout, mutation=False):
        self.calls += 1
        call_id = f"q-{self.calls}"
        try:
            return NativeCallResult.success(operation(self.app), call_id=call_id, dispatched=True)
        except Exception as exc:
            return NativeCallResult.failed(failure_from_exception(exc, stage), call_id=call_id, dispatched=True)


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "fixture.sldprt"
        self.path.write_bytes(b"x")
        self.doc = Doc(self.path, DocumentType.PART)
        self.api = Api()
        self.session = Session(App(self.doc), self.api)
        self.service = DocumentService(self.session, path_policy=DocumentPathPolicy((self.root,)))
        self.context = self.service.adopt_open_document(self.path).value

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_feature_query_returns_native_type_and_error_state(self) -> None:
        second = Feature("Sketch1", "ProfileFeature")
        first = Feature("Boss-Extrude1", "Boss", error_code=5, next_feature=second)
        self.doc.first = first

        result = self.service.list_features(self.context)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(["Boss-Extrude1", "Sketch1"], [item.name for item in result.value])
        self.assertEqual(5, result.value[0].error_code)
        self.assertEqual("Boss", result.value[0].type_name)

    def test_body_query_only_valid_for_part(self) -> None:
        self.doc.solid_bodies = [Body("SolidBody1")]
        self.doc.sheet_bodies = [Body("SurfaceBody1")]
        result = self.service.list_bodies(self.context)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual(["SolidBody1", "SurfaceBody1"], [item.name for item in result.value])

        self.doc.doc_type = int(DocumentType.DRAWING)
        wrong_context = self.service.refresh(self.context).value
        rejected = self.service.list_bodies(wrong_context)
        self.assertEqual(NativeCallState.FAILURE, rejected.state)
        self.assertEqual("document_type_mismatch", rejected.failure.code)

    def test_component_query_only_valid_for_assembly(self) -> None:
        self.doc.doc_type = int(DocumentType.ASSEMBLY)
        self.doc.path = os.path.realpath(self.root / "fixture.sldasm")
        self.doc.title = "fixture.sldasm"
        Path(self.doc.path).write_bytes(b"x")
        self.doc.components = [Component("Bracket-1", r"C:\cad\Bracket.SLDPRT", suppressed=True)]
        context = self.service.adopt_open_document(self.doc.path).value

        result = self.service.list_components(context)
        self.assertEqual(NativeCallState.SUCCESS, result.state)
        self.assertEqual("Bracket-1", result.value[0].name)
        self.assertEqual("Bracket-1", result.value[0].component_id)
        self.assertTrue(result.value[0].suppressed)

    def test_rebuild_failure_is_normalized_as_non_success(self) -> None:
        self.doc.rebuild_ok = False
        result = self.service.rebuild(self.context)
        self.assertEqual(NativeCallState.FAILURE, result.state)
        self.assertEqual("rebuild_failed", result.failure.code)


if __name__ == "__main__":
    unittest.main()
