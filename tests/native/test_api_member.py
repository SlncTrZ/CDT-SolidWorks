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
