from pathlib import Path
from types import SimpleNamespace

from cdt_solidworks.native.models import ApplicationOwnership, NativeCallResult
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
        self.preference_value = root
        self.executable_path = root / "Program" / "SLDWORKS.exe"
        self.load_addin_calls = []

    def GetAddInObject(self, guid):
        assert guid == "{ED783340-D5DB-11d4-BD5A-00C04F019809}"
        return self.addin

    def GetUserPreferenceStringValue(self, preference):
        assert preference == self.preference
        return str(self.preference_value)

    def GetExecutablePath(self):
        return str(self.executable_path)

    def LoadAddIn(self, path):
        self.load_addin_calls.append(str(path))
        if Path(path).is_file() and Path(path).name.casefold() == "swbrowser.dll":
            self.addin = object()
            return 0
        return 1


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
    def __init__(
        self,
        root: Path,
        *,
        ownership: ApplicationOwnership = ApplicationOwnership.PROVIDER_OWNED,
    ) -> None:
        self.api = _Api()
        self.app = _App(root)
        self.ownership = ownership
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


def test_probe_normalizes_language_database_directory_to_toolbox_root(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root)
    session.app.preference_value = root / "lang" / "english"
    adapter = ToolboxNativeAdapter(session)

    probe = adapter.probe()

    assert probe.available is True
    assert Path(probe.root_path or "") == root.resolve()
    assert Path(probe.database_path or "").name == "swbrowser.sldedb"


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


def test_probe_loads_browser_addin_from_executable_file_path_for_provider_owned_session(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root)
    session.app.addin = None
    browser_dll = session.app.executable_path.parent / "Toolbox" / "SwBrowser.dll"
    browser_dll.parent.mkdir(parents=True)
    browser_dll.write_bytes(b"addin")
    adapter = ToolboxNativeAdapter(session)

    probe = adapter.probe()

    assert probe.available is True
    assert probe.addin_loaded is True
    assert session.app.load_addin_calls == [str(browser_dll)]


def test_probe_loads_browser_addin_when_get_executable_path_returns_install_directory(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root)
    session.app.addin = None
    session.app.executable_path = root / "Program"
    browser_dll = session.app.executable_path / "Toolbox" / "SwBrowser.dll"
    browser_dll.parent.mkdir(parents=True)
    browser_dll.write_bytes(b"addin")
    adapter = ToolboxNativeAdapter(session)

    probe = adapter.probe()

    assert probe.available is True
    assert probe.addin_loaded is True
    assert session.app.load_addin_calls == [str(browser_dll)]


def test_probe_does_not_load_browser_addin_for_user_owned_session(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _fixture(root)
    session = _Session(root, ownership=ApplicationOwnership.USER_OWNED)
    session.app.addin = None
    browser_dll = session.app.executable_path.parent / "Toolbox" / "SwBrowser.dll"
    browser_dll.parent.mkdir(parents=True)
    browser_dll.write_bytes(b"addin")
    adapter = ToolboxNativeAdapter(session)

    probe = adapter.probe()

    assert probe.available is False
    assert probe.reason == "toolbox_addin_unavailable_or_unlicensed"
    assert probe.license_state == "unknown"
    assert session.app.load_addin_calls == []
