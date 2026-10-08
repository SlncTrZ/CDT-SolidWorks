from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import stat
import threading
import time
from types import SimpleNamespace

import pytest

from cdt_solidworks.native.models import ApplicationOwnership, NativeCallResult
from cdt_solidworks.toolbox import native as toolbox_native
from cdt_solidworks.toolbox.domain import (
    ToolboxCatalogItem,
    ToolboxPostconditionError,
    ToolboxRefusal,
)
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
    con = sqlite3.connect(database)
    try:
        con.execute(
            "CREATE TABLE Standards (Name TEXT, enabled INTEGER, Installed INTEGER, IsToolbox INTEGER, TableNamePrefix TEXT, DefaultUnits TEXT)"
        )
        con.commit()
    finally:
        con.close()
    return part


def _sqlite_fixture(root: Path) -> None:
    browser = root / "Browser"
    bolt = browser / "Ansi Inch" / "Bolts And Screws" / "Hex Head" / "Hex Bolt_AI.SLDPRT"
    washer = browser / "Ansi Inch" / "washers" / "Plain Washers" / "Flat Washer Type B Regular_AI.sldprt"
    bolt.parent.mkdir(parents=True)
    washer.parent.mkdir(parents=True)
    bolt.write_bytes(b"bolt-master")
    washer.write_bytes(b"washer-master")
    database = root / "lang" / "english" / "swbrowser.sldedb"
    database.parent.mkdir(parents=True)
    con = sqlite3.connect(database)
    try:
        con.execute(
            "CREATE TABLE Standards (Name TEXT, enabled INTEGER, Installed INTEGER, IsToolbox INTEGER, TableNamePrefix TEXT, DefaultUnits TEXT)"
        )
        con.execute(
            "INSERT INTO Standards VALUES ('Ansi Inch',1,1,1,'AI_','INCH')"
        )
        for table in ("AI_TYPE_BS", "AI_TYPE_WASHERS"):
            con.execute(
                f'CREATE TABLE "{table}" (enabled INTEGER, Title TEXT, Filename TEXT, ConfigurationTable TEXT, DataTable TEXT, DataTableUnits TEXT, PartNumberID TEXT)'
            )
        con.execute(
            "INSERT INTO AI_TYPE_BS VALUES (1,'Hex Bolt','Ansi Inch\\Bolts And Screws\\Hex Bolt_AI.SLDPRT','+CFG_BS_HBOLT','+DATA_HBOLT','','HBOLT_AI_BS_PN')"
        )
        con.execute(
            "INSERT INTO AI_TYPE_WASHERS VALUES (1,'Regular Flat Washer Type B','Ansi Inch\\washers\\Flat Washer Type B Regular_AI.sldprt','+CFG_WASHERS_FW','+DATA_FW1','','FW1_AI_WASHERS_PN')"
        )
        con.execute(
            "CREATE TABLE AI_CFG_BS_HBOLT (Grid_Item_Number INTEGER, Grid_Item_Name TEXT, Grid_Item_Type TEXT, Controller INTEGER, Dimension TEXT, NoUnitConversion INTEGER, AltDataSource TEXT, ValueList TEXT, RelationField TEXT)"
        )
        con.execute(
            "INSERT INTO AI_CFG_BS_HBOLT VALUES (1,'Size','STRING_COMBO',1,'',0,'','{[size]-[pitch]}','')"
        )
        con.execute(
            "INSERT INTO AI_CFG_BS_HBOLT VALUES (10,'Length','NUMERIC_COMBO',0,'Length@BodySke',0,'+DATA_HBOLT_LENGTHS','{[Length]}','[Size=Controller@Size]')"
        )
        con.execute(
            "CREATE TABLE AI_DATA_HBOLT (SIZE TEXT, PITCH TEXT, DIAMETER TEXT, WIDTH_FLATS TEXT, HEAD_HT TEXT, enabled INTEGER, key INTEGER)"
        )
        con.execute(
            "INSERT INTO AI_DATA_HBOLT VALUES ('1/4','20','0.2500','0.438','0.188',1,1)"
        )
        con.execute(
            "INSERT INTO AI_DATA_HBOLT VALUES ('1/4','28','0.2500','0.438','0.188',1,2)"
        )
        con.execute(
            "CREATE TABLE AI_Cfg_WASHERS_FW (Grid_Item_Number INTEGER, Grid_Item_Name TEXT, Grid_Item_Type TEXT, Controller INTEGER, Dimension TEXT, NoUnitConversion INTEGER, AltDataSource TEXT, ValueList TEXT, RelationField TEXT)"
        )
        con.execute(
            "INSERT INTO AI_CFG_WASHERS_FW VALUES (1,'Size','STRING_COMBO',1,'',0,'','{[size]}','')"
        )
        con.execute(
            "INSERT INTO AI_CFG_WASHERS_FW VALUES (34,'Inside Diameter','Disabled_Greyed',0,'Inside_dia@Sketch1',0,'','{[Insid_dia]}','')"
        )
        con.execute(
            "INSERT INTO AI_CFG_WASHERS_FW VALUES (36,'Outside Diameter','Disabled_Greyed',0,'Outside_dia@Sketch1',0,'','{[Outsid_dia]}','')"
        )
        con.execute(
            "INSERT INTO AI_CFG_WASHERS_FW VALUES (38,'Thickness','Disabled_Greyed',0,'Thickness@Sketch1',0,'','{[Thickness]}','')"
        )
        con.execute(
            "CREATE TABLE AI_DATA_FW1 (SIZE TEXT, INSID_DIA TEXT, OUTSID_DIA TEXT, THICKNESS TEXT, enabled INTEGER, key INTEGER)"
        )
        con.execute(
            "INSERT INTO AI_DATA_FW1 VALUES ('1/4','0.281','0.734','0.063',1,1)"
        )
        con.commit()
    finally:
        con.close()


def test_database_catalog_returns_real_hex_bolt_size_instead_of_master_configuration(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))

    result = adapter.catalog_query("Ansi Inch", "hex bolt_ai", "1/4-20", 10)

    assert len(result) == 1
    item = result[0]
    assert item.size == "1/4-20"
    assert item.configuration == "Default"
    assert ("DIAMETER", "0.2500") in item.properties
    assert Path(item.source_path).name.casefold() == "hex bolt_ai.sldprt"


def test_database_catalog_returns_real_washer_size(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))

    result = adapter.catalog_query(
        "Ansi Inch", "flat washer type b regular_ai", "1/4", 10
    )

    assert len(result) == 1
    item = result[0]
    assert item.size == "1/4"
    assert ("INSID_DIA", "0.281") in item.properties
    assert ("THICKNESS", "0.063") in item.properties


def test_database_materialization_plan_maps_washer_dimensions_to_system_units(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))
    item = adapter.catalog_query(
        "Ansi Inch", "flat washer type b regular_ai", "1/4", 10
    )[0]

    plan = adapter._database_materialization_plan(adapter.probe(), item)

    assert plan == (
        ("Inside_dia@Sketch1", 0.281 * 0.0254),
        ("Outside_dia@Sketch1", 0.734 * 0.0254),
        ("Thickness@Sketch1", 0.063 * 0.0254),
    )


def test_copy_component_makes_project_copy_writable_without_changing_vendor_mode(tmp_path: Path) -> None:
    root = tmp_path / "Toolbox"
    source = _fixture(root)
    source.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    destination = tmp_path / "project" / "copy.SLDPRT"
    destination.parent.mkdir()
    adapter = ToolboxNativeAdapter(_Session(root))
    item = ToolboxCatalogItem(
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        source_path=str(source),
        configuration="1/4-20 x 1",
    )

    copied = Path(adapter.copy_component(item, destination))

    assert source.stat().st_mode & stat.S_IWUSR == 0
    assert copied.stat().st_mode & stat.S_IWUSR


def test_copy_component_preserves_artifact_when_native_state_is_uncertain(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))
    item = adapter.catalog_query(
        "Ansi Inch", "flat washer type b regular_ai", "1/4", 10
    )[0]
    destination = tmp_path / "project" / "washer.SLDPRT"
    destination.parent.mkdir()

    def uncertain_materialize(target, *, configuration, assignments):
        raise ToolboxPostconditionError("native_state_uncertain", "call_id=uncertain-call")

    monkeypatch.setattr(adapter, "_materialize_project_copy", uncertain_materialize)

    with pytest.raises(ToolboxPostconditionError) as caught:
        adapter.copy_component(item, destination)

    assert caught.value.reason == "native_state_uncertain"
    assert destination.is_file()
    assert destination.stat().st_size > 0


def test_copy_component_reserves_destination_atomically_under_concurrency(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "Toolbox"
    source = _fixture(root)
    destination = tmp_path / "project" / "copy.SLDPRT"
    destination.parent.mkdir()
    item = ToolboxCatalogItem(
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        source_path=str(source),
        configuration="1/4-20 x 1",
    )
    adapters = (ToolboxNativeAdapter(_Session(root)), ToolboxNativeAdapter(_Session(root)))
    first_copy_entered = threading.Event()
    release_copy = threading.Event()
    copy_calls = 0
    copy_calls_lock = threading.Lock()
    original_copy2 = toolbox_native.shutil.copy2

    def blocked_copy2(src, dst, *args, **kwargs):
        nonlocal copy_calls
        with copy_calls_lock:
            copy_calls += 1
            current = copy_calls
        if current == 1:
            first_copy_entered.set()
            release_copy.wait(timeout=2.0)
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(toolbox_native.shutil, "copy2", blocked_copy2)

    def run(adapter):
        try:
            return ("success", adapter.copy_component(item, destination))
        except ToolboxRefusal as exc:
            return (exc.reason, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, adapters[0])
        assert first_copy_entered.wait(timeout=1.0)
        second = executor.submit(run, adapters[1])
        time.sleep(0.05)
        release_copy.set()
        outcomes = (first.result(timeout=2.0), second.result(timeout=2.0))

    assert [state for state, _ in outcomes].count("success") == 1
    assert [state for state, _ in outcomes].count("project_component_exists") == 1
    assert copy_calls == 1
    assert destination.read_bytes() == source.read_bytes()


def test_copy_component_cleanup_does_not_delete_foreign_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))
    item = adapter.catalog_query(
        "Ansi Inch", "flat washer type b regular_ai", "1/4", 10
    )[0]
    destination = tmp_path / "project" / "washer.SLDPRT"
    destination.parent.mkdir()

    def replace_then_fail(target, *, configuration, assignments):
        target.unlink()
        target.write_bytes(b"foreign-owner")
        raise ToolboxRefusal("forced_materialization_failure")

    monkeypatch.setattr(adapter, "_materialize_project_copy", replace_then_fail)

    with pytest.raises(ToolboxRefusal) as caught:
        adapter.copy_component(item, destination)

    assert caught.value.reason == "forced_materialization_failure"
    assert destination.read_bytes() == b"foreign-owner"


def test_database_catalog_copy_invokes_materialization_hook(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "Toolbox"
    _sqlite_fixture(root)
    adapter = ToolboxNativeAdapter(_Session(root))
    item = adapter.catalog_query(
        "Ansi Inch", "flat washer type b regular_ai", "1/4", 10
    )[0]
    destination = tmp_path / "project" / "washer.SLDPRT"
    destination.parent.mkdir()
    calls = []

    def fake_materialize(target, *, configuration, assignments):
        calls.append((Path(target), configuration, assignments))

    monkeypatch.setattr(adapter, "_materialize_project_copy", fake_materialize)

    copied = Path(adapter.copy_component(item, destination))

    assert copied == destination.resolve()
    assert len(calls) == 1
    assert calls[0][0] == destination.resolve()
    assert calls[0][1] == "Default"
    assert calls[0][2] == (
        ("Inside_dia@Sketch1", 0.281 * 0.0254),
        ("Outside_dia@Sketch1", 0.734 * 0.0254),
        ("Thickness@Sketch1", 0.063 * 0.0254),
    )


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
