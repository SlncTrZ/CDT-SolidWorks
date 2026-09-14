import unittest

from cdt_solidworks.mbd.domain import MbdService, PmiKind
from cdt_solidworks.mbd.native import SolidWorksMbdAdapter
from cdt_solidworks.native.models import NativeCallResult


class FakePathPolicy:
    def validate_open(self, value):
        return str(value)


class FakeSpecificAnnotation:
    def __init__(self, text):
        self.text = text

    def GetText(self):
        return self.text


class FakeAnnotation:
    def __init__(self, name, type_code, text, attached_types=(1,), is_dimxpert=False):
        self.name = name
        self.type_code = type_code
        self.specific = FakeSpecificAnnotation(text)
        self.attached_types = attached_types
        self.is_dimxpert = is_dimxpert

    def GetName(self):
        return self.name

    def GetType(self):
        return self.type_code

    def GetSpecificAnnotation(self):
        return self.specific

    def GetAttachedEntityTypes(self):
        return self.attached_types

    def IsDimXpert(self):
        return self.is_dimxpert


class FakeConfiguration:
    Name = "Default"


class FakeConfigurationManager:
    ActiveConfiguration = FakeConfiguration()


class FakeExtension:
    def __init__(self, annotations):
        self.annotations = annotations

    def GetAnnotations(self):
        return tuple(self.annotations)


class FakeModel:
    def __init__(self, annotations):
        self.path = r"C:\\models\\fixture.SLDPRT"
        self.title = "fixture.SLDPRT"
        self.ConfigurationManager = FakeConfigurationManager()
        self.Extension = FakeExtension(annotations)

    def GetTitle(self):
        return self.title

    def GetType(self):
        return 1

    def GetPathName(self):
        return self.path


class FakeApp:
    def __init__(self, model):
        self.model = model


class FakeApi:
    def __init__(self, model):
        self.model = model

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if args else (member() if callable(member) else member)

    def get_open_document(self, app, path):
        return self.model

    def open_document(self, *args, **kwargs):
        raise AssertionError("already-open fixture should not be reopened")

    def document_type(self, model):
        return model.GetType()

    def document_path(self, model):
        return model.GetPathName()

    def active_configuration(self, model):
        return model.ConfigurationManager.ActiveConfiguration.Name

    def close_document(self, app, title):
        raise AssertionError("caller-owned document must not be closed")


class FakeSession:
    def __init__(self, api, app):
        self.api = api
        self.app = app

    def execute(self, operation, *, stage, timeout, mutation):
        return NativeCallResult.success(operation(self.app), call_id=stage)


class SolidWorksMbdAdapterTests(unittest.TestCase):
    def test_queries_typed_gtol_datum_and_reference_dimension(self):
        annotations = [
            FakeAnnotation("GTol1", 5, "POSITION 0.1 A B C"),
            FakeAnnotation("Datum1", 2, "A"),
            FakeAnnotation("D1@Sketch1", 4, "25.00"),
            FakeAnnotation("DX1@DimXpert", 4, "10.00", is_dimxpert=True),
        ]
        model = FakeModel(annotations)
        session = FakeSession(FakeApi(model), FakeApp(model))
        service = MbdService(
            SolidWorksMbdAdapter(
                session,
                path_policy=FakePathPolicy(),
                default_timeout=5.0,
            )
        )
        result = service.query_pmi(model.path, "Default")
        self.assertEqual(
            (
                PmiKind.GTOL,
                PmiKind.DATUM,
                PmiKind.REFERENCE_DIMENSION,
                PmiKind.DIMXPERT,
            ),
            tuple(item.kind for item in result.annotations),
        )
        self.assertEqual("POSITION 0.1 A B C", result.annotations[0].text)

    def test_dangling_annotation_is_detected_from_attached_entity_type_zero(self):
        model = FakeModel([FakeAnnotation("GTol1", 5, "POSITION 0.1 A", attached_types=(0,))])
        session = FakeSession(FakeApi(model), FakeApp(model))
        service = MbdService(
            SolidWorksMbdAdapter(session, path_policy=FakePathPolicy())
        )
        with self.assertRaisesRegex(Exception, "dangling_pmi"):
            service.query_pmi(model.path, "Default")


if __name__ == "__main__":
    unittest.main()
