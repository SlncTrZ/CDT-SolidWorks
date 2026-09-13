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
