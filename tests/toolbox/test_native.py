from pathlib import Path
from types import SimpleNamespace

from cdt_solidworks.native.models import NativeCallResult
from cdt_solidworks.toolbox.native import ToolboxNativeAdapter


class _PropertyManager:
    def GetNames(self):
        return ("Part Number", "Description")

    def Get(self, name):
        return {"Part Number": "BOLT-025-1", "Description": "Hex Bolt"}[name]


class _Config:
    CustomPropertyManager = _PropertyManager()


class _Model:
    def __init__(self, path: str) -> None:
        self.path = path

    def GetConfigurationNames(self):
        return ("1/4-20 x 1", "1/4-20 x 2")

    def GetConfigurationByName(self, name):
        return _Config() if name in self.GetConfigurationNames() else None

    def GetTitle(self):
        return Path(self.path).name


class _App:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.addin = object()
        self.preference = 123

    def GetAddInObject(self, guid):
        assert guid == "{ED783340-D5DB-11d4-BD5A-00C04F019809}"
        return self.addin

    def GetUserPreferenceStringValue(self, preference):
        assert preference == self.preference
        return str(self.root)


class _Api:
    def __init__(self) -> None:
        self._client = SimpleNamespace(
            constants=SimpleNamespace(swHoleWizardToolBoxFolder=123)
        )
        self.opened = []
        self.closed = []

    @staticmethod
    def _member(obj, name, *args):
        member = getattr(obj, name)
        return member(*args) if callable(member) else member

    @staticmethod
    def revision_number(app):
        return "34.0.0"

    @staticmethod
    def get_open_document(app, path):
        return None

    def open_document(self, app, path, doc_type, *, read_only, silent, configuration):
        assert doc_type == 1
        assert read_only and silent
        self.opened.append((path, configuration))
        return _Model(path), 0, 0

    def close_document(self, app, title):
        self.closed.append(title)
        return True

    @staticmethod
    def document_title(model):
        return model.GetTitle()


class _Session:
    def __init__(self, root: Path) -> None:
        self.api = _Api()
        self.app = _App(root)
        self.calls = []

    def execute(self, operation, *, stage, timeout, mutation=False):
        self.calls.append((stage, mutation))
        return NativeCallResult.success(
            operation(self.app), call_id=f"call-{len(self.calls)}", dispatched=True
        )


def _fixture(root: Path) -> Path:
    part = root / "Browser" / "ANSI Inch" / "hex bolt.sldprt"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"vendor-component")
    database = root / "lang" / "english" / "swbrowser.sldedb"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"database")
    return part


def test_probe_reads_addin_root_database_and_version(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))
    probe = adapter.probe()
    assert probe.available is True
    assert probe.addin_loaded is True
    assert Path(probe.root_path or "") == root.resolve()
    assert Path(probe.database_path or "").name == "swbrowser.sldedb"
    assert probe.license_state == "available"


def test_probe_falls_back_to_stable_toolbox_preference_enum_without_makepy_constants(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root)
    session.api._client.constants = SimpleNamespace()
    session.app.preference = 52
    adapter = ToolboxNativeAdapter(session)

    probe = adapter.probe()

    assert probe.available is True
    assert Path(probe.root_path or "") == root.resolve()


def test_catalog_scans_bounded_vendor_parts_read_only(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    part = _fixture(root)
    session = _Session(root)
    adapter = ToolboxNativeAdapter(session)
    result = adapter.catalog_query("ANSI Inch", "hex bolt", "1/4-20 x 1", 10)
    assert len(result) == 1
    item = result[0]
    assert item.source_path == str(part.resolve())
    assert item.configuration == "1/4-20 x 1"
    assert item.part_number == "BOLT-025-1"
    assert ("Description", "Hex Bolt") in item.properties
    assert session.api.opened
    assert session.api.closed


def test_probe_fails_closed_when_addin_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root)
    session.app.addin = None
    adapter = ToolboxNativeAdapter(session)
    probe = adapter.probe()
    assert probe.available is False
    assert probe.reason == "toolbox_addin_unavailable_or_unlicensed"
    assert probe.license_state == "unknown"
