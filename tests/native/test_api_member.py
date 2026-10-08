from cdt_solidworks.native.api import WindowsComApi


class _CallableComProxy:
    """Minimal pywin32-like child COM property that must not be invoked."""

    _oleobj_ = object()

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self):
        self.call_count += 1
        raise RuntimeError("COM property proxy was invoked as a method")


class _Owner:
    def __init__(self) -> None:
        self.Extension = _CallableComProxy()

    def GetTitle(self) -> str:
        return "Part1"


def test_member_returns_callable_com_property_without_invoking_it() -> None:
    owner = _Owner()

    value = WindowsComApi._member(owner, "Extension")

    assert value is owner.Extension
    assert owner.Extension.call_count == 0


def test_member_still_invokes_zero_argument_com_method_wrapper() -> None:
    owner = _Owner()

    assert WindowsComApi._member(owner, "GetTitle") == "Part1"


def test_activate_document_uses_explicit_title_and_byref_error() -> None:
    created = []

    class Ref:
        def __init__(self, value):
            self.value = value

    class Client:
        @staticmethod
        def VARIANT(flags, value):
            created.append((flags, value))
            return Ref(value)

    class PythonCom:
        VT_BYREF = 0x4000
        VT_I4 = 3

    class Model:
        @staticmethod
        def GetTitle():
            return "Part1.SLDPRT"

    active = object()

    class App:
        @staticmethod
        def ActivateDoc3(title, use_preferences, option, errors):
            assert title == "Part1.SLDPRT"
            assert use_preferences is False
            assert option == 0
            errors.value = 0
            return active

    api = WindowsComApi()
    api._client = Client()
    api._pythoncom = PythonCom()
    result, errors = api.activate_document(App(), Model())

    assert result is active
    assert errors == 0
    assert created == [(PythonCom.VT_BYREF | PythonCom.VT_I4, 0)]


def test_sketch_reference_entity_uses_typed_byref_integer() -> None:
    class Ref:
        def __init__(self) -> None:
            self.value = 0

    created = []

    class Client:
        @staticmethod
        def VARIANT(flags, value):
            created.append((flags, value))
            return Ref()

    class PythonCom:
        VT_BYREF = 0x4000
        VT_I4 = 3

    class Sketch:
        def GetReferenceEntity(self, entity_type):
            entity_type.value = 4
            return "plane-object"

    api = WindowsComApi()
    api._client = Client()
    api._pythoncom = PythonCom()
    reference, entity_type = api.sketch_reference_entity(Sketch())

    assert reference == "plane-object"
    assert entity_type == 4
    assert created == [(PythonCom.VT_BYREF | PythonCom.VT_I4, 0)]


def test_persistent_reference_normalizes_byte_array_payload() -> None:
    class Extension:
        @staticmethod
        def GetPersistReference3(entity):
            assert entity == "face"
            return (0, 127, 255)

    model = type("Model", (), {})()
    model.Extension = Extension()

    api = WindowsComApi()
    assert api.persistent_reference(model, "face") == bytes((0, 127, 255))


def test_object_by_persistent_reference_uses_ui1_array_and_byref_status() -> None:
    created = []

    class Ref:
        def __init__(self, value):
            self.value = value

    class Client:
        @staticmethod
        def VARIANT(flags, value):
            created.append((flags, value))
            return Ref(value)

    class PythonCom:
        VT_ARRAY = 0x2000
        VT_UI1 = 17
        VT_BYREF = 0x4000
        VT_I4 = 3

    class Extension:
        @staticmethod
        def GetObjectByPersistReference3(payload, error):
            assert payload.value == (1, 2, 3)
            error.value = 2
            return "entity"

    model = type("Model", (), {})()
    model.Extension = Extension()

    api = WindowsComApi()
    api._client = Client()
    api._pythoncom = PythonCom()
    api._initialized = True
    entity, state = api.object_by_persistent_reference(model, bytes((1, 2, 3)))
    assert entity == "entity"
    assert state == 2
    assert created == [
        (PythonCom.VT_ARRAY | PythonCom.VT_UI1, (1, 2, 3)),
        (PythonCom.VT_BYREF | PythonCom.VT_I4, 0),
    ]


def test_object_by_persistent_reference_accepts_tuple_return_shape() -> None:
    class Ref:
        def __init__(self, value): self.value = value

    class Client:
        @staticmethod
        def VARIANT(flags, value): return Ref(value)

    class PythonCom:
        VT_ARRAY = 0x2000
        VT_UI1 = 17
        VT_BYREF = 0x4000
        VT_I4 = 3

    class Extension:
        @staticmethod
        def GetObjectByPersistReference3(payload, error):
            return ("entity", 4)

    model = type("Model", (), {})()
    model.Extension = Extension()

    api = WindowsComApi()
    api._client = Client()
    api._pythoncom = PythonCom()
    api._initialized = True
    assert api.object_by_persistent_reference(model, b"abc") == ("entity", 4)
