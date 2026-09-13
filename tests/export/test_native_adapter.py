import tempfile
import unittest
from pathlib import Path

from cdt_solidworks.export.domain import ExportFormat, ExportRequest
from cdt_solidworks.export.native import (
    FileArtifactInspector,
    SolidWorksExporter,
    SolidWorksGeometryVerifier,
)
from cdt_solidworks.native.models import NativeCallResult


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)

    def validate_save(self, value):
        return str(value)


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


class FakeModel:
    def __init__(self, path=r"C:\fixtures\part.SLDPRT", title="part.SLDPRT", doc_type=1):
        self.path = path
        self.title = title
        self.doc_type = doc_type
        self.cleared = False

    def GetTitle(self):
        return self.title

    def GetType(self):
        return self.doc_type

    def ClearSelection2(self, all_items):
        self.cleared = bool(all_items)


class FakeApp:
    def __init__(self, model):
        self.model = model
        self.activations = []

    def ActivateDoc3(self, title, use_preferences, option, errors):
        self.activations.append((title, use_preferences, option))
        errors.value = 0
        return self.model


class FakeApi:
    _pythoncom = FakePythonCom()
    _client = FakeClient()

    def __init__(self, model):
        self.model = model
        self.saved_targets = []

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        if args:
            return member(*args)
        return member() if callable(member) else member

    def get_open_document(self, app, path):
        return self.model

    def open_document(self, *args, **kwargs):
        raise AssertionError("already-open fixture should not be reopened")

    def document_type(self, model):
        return model.GetType()

    def active_configuration(self, model):
        return "Default"

    def save_as(self, model, target_path):
        self.saved_targets.append(target_path)
        return True, 0, 0

    def close_document(self, app, title):
        raise AssertionError("adapter must not close caller-owned model")


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app
        self.calls = []

    def execute(self, operation, *, stage, timeout, mutation):
        self.calls.append((stage, timeout, mutation))
        return NativeCallResult.success(operation(self.app), call_id="call-1")


class SolidWorksExporterTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.app = FakeApp(self.model)
        self.api = FakeApi(self.model)
        self.session = FakeSession(self.api, self.app)
        self.exporter = SolidWorksExporter(
            self.session,
            path_policy=FakePathPolicy(),
            default_timeout=9.0,
        )

    def test_step_export_activates_document_and_clears_selection(self):
        result = self.exporter.export(
            ExportRequest(
                source_document_id=self.model.path,
                target_path=r"C:\exports\part.step",
                format=ExportFormat.STEP,
                source_configuration="Default",
            )
        )
        self.assertTrue(result.completed)
        self.assertTrue(self.model.cleared)
        self.assertEqual(1, len(self.app.activations))
        self.assertEqual(r"C:\exports\part.step", self.api.saved_targets[-1])
        self.assertEqual(("export_save_as", 9.0, True), self.session.calls[-1])

    def test_step_242_refuses_optimistic_save_as(self):
        result = self.exporter.export(
            ExportRequest(
                source_document_id=self.model.path,
                target_path=r"C:\exports\part.stp",
                format=ExportFormat.STEP_242,
            )
        )
        self.assertFalse(result.completed)
        self.assertIn("step_242_requires_pmi_publish_path", result.errors)
        self.assertEqual([], self.session.calls)

    def test_single_sheet_pdf_requires_export_data_binding(self):
        drawing = FakeModel(
            path=r"C:\fixtures\drawing.SLDDRW",
            title="drawing.SLDDRW",
            doc_type=3,
        )
        self.api.model = drawing
        self.app.model = drawing
        result = self.exporter.export(
            ExportRequest(
                source_document_id=drawing.path,
                target_path=r"C:\exports\drawing.pdf",
                format=ExportFormat.PDF,
                drawing_sheet="Sheet1",
            )
        )
        self.assertFalse(result.completed)
        self.assertIn("single_sheet_pdf_requires_export_data", result.errors)


class FileArtifactInspectorTests(unittest.TestCase):
    def test_pdf_signature_and_byte_size_are_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "drawing.pdf"
            target.write_bytes(b"%PDF-1.7\nfixture")
            inspector = FileArtifactInspector()
            result = inspector.inspect(
                ExportRequest(
                    source_document_id="drawing-1",
                    target_path=str(target),
                    format=ExportFormat.PDF,
                )
            )
            self.assertTrue(result.readable)
            self.assertGreater(result.byte_size, 0)
            self.assertFalse(result.geometry_verified)

    def test_geometry_verifier_is_required_for_step_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "part.step"
            target.write_text("ISO-10303-21;\nEND-ISO-10303-21;", encoding="ascii")
            request = ExportRequest(
                source_document_id="part-1",
                target_path=str(target),
                format=ExportFormat.STEP,
            )
            self.assertFalse(FileArtifactInspector().inspect(request).geometry_verified)
            verified = FileArtifactInspector(geometry_verifier=lambda _request: True).inspect(request)
            self.assertTrue(verified.geometry_verified)


if __name__ == "__main__":
    unittest.main()


class _FakeGraphicsGeometry:
    def __init__(self, facets=12):
        self.facets = facets

    def GetFacetCount(self):
        return self.facets


class _FakeGraphicsBody:
    def __init__(self, facets=12):
        self.graphics = _FakeGraphicsGeometry(facets)

    def GetType(self):
        return 7

    def GetGraphicsBody(self):
        return self.graphics


class _FakeFeature:
    def __init__(
        self,
        specific=None,
        next_feature=None,
        *,
        first_subfeature=None,
        next_subfeature=None,
    ):
        self.specific = specific
        self.next_feature = next_feature
        self.first_subfeature = first_subfeature
        self.next_subfeature = next_subfeature

    def GetSpecificFeature2(self):
        return self.specific

    def GetNextFeature(self):
        return self.next_feature

    def GetFirstSubFeature(self):
        return self.first_subfeature

    def GetNextSubFeature(self):
        return self.next_subfeature


class _FakeImportedModel(FakeModel):
    def __init__(self, feature=None):
        super().__init__(path=r"C:\exports\imported.SLDPRT", title="imported.SLDPRT", doc_type=1)
        self.feature = feature

    def FirstFeature(self):
        return self.feature


class _FakeImportApp:
    def __init__(self, model, null_dispatch):
        self.model = model
        self.null_dispatch = null_dispatch
        self.received_import_data = None

    def GetImportFileData(self, _target):
        return None

    def LoadFile4(self, _target, _options, import_data, errors):
        self.received_import_data = import_data
        if import_data is not self.null_dispatch:
            raise TypeError("typed null dispatch required")
        errors.value = 0
        return self.model


class _FakeGeometryApi(FakeApi):
    def __init__(self, model, null_dispatch, *, solid_count=0):
        super().__init__(model)
        self._null_dispatch = null_dispatch
        self.solid_count = solid_count
        self.closed = []

    def null_dispatch(self):
        return self._null_dispatch

    def document_type(self, model):
        return model.GetType()

    def bodies(self, model, body_type, visible_only):
        if body_type == 0 and self.solid_count:
            return tuple(object() for _ in range(self.solid_count))
        return ()

    def first_feature(self, model):
        return model.FirstFeature()

    def next_feature(self, feature):
        return feature.GetNextFeature()

    def close_document(self, app, title):
        self.closed.append(title)


class SolidWorksGeometryVerifierTests(unittest.TestCase):
    def _verifier(self, model, *, solid_count=0):
        null_dispatch = object()
        api = _FakeGeometryApi(model, null_dispatch, solid_count=solid_count)
        app = _FakeImportApp(model, null_dispatch)
        session = FakeSession(api, app)
        verifier = SolidWorksGeometryVerifier(
            session,
            path_policy=FakePathPolicy(),
            default_timeout=11.0,
        )
        return verifier, app, session

    def test_foreign_import_without_import_data_uses_typed_null_dispatch(self):
        model = _FakeImportedModel()
        verifier, app, session = self._verifier(model, solid_count=1)
        result = verifier(
            ExportRequest(
                source_document_id=r"C:\fixtures\part.SLDPRT",
                target_path=r"C:\exports\part.x_t",
                format=ExportFormat.PARASOLID,
            )
        )
        self.assertTrue(result)
        self.assertIs(app.received_import_data, session.api.null_dispatch())

    def test_mesh_format_accepts_graphics_body_only_with_positive_facet_count(self):
        graphics_feature = _FakeFeature(_FakeGraphicsBody(facets=12))
        model = _FakeImportedModel(
            _FakeFeature(first_subfeature=graphics_feature)
        )
        verifier, _app, _session = self._verifier(model)
        result = verifier(
            ExportRequest(
                source_document_id=r"C:\fixtures\part.SLDPRT",
                target_path=r"C:\exports\part.stl",
                format=ExportFormat.STL,
            )
        )
        self.assertTrue(result)

    def test_mesh_format_rejects_empty_graphics_body(self):
        graphics_feature = _FakeFeature(_FakeGraphicsBody(facets=0))
        model = _FakeImportedModel(
            _FakeFeature(first_subfeature=graphics_feature)
        )
        verifier, _app, _session = self._verifier(model)
        result = verifier(
            ExportRequest(
                source_document_id=r"C:\fixtures\part.SLDPRT",
                target_path=r"C:\exports\part.3mf",
                format=ExportFormat.THREE_MF,
            )
        )
        self.assertFalse(result)
