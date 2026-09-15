from pathlib import Path

import pytest

from cdt_solidworks.toolbox.domain import (
    ToolboxCatalogItem,
    ToolboxProbeSnapshot,
    ToolboxRefusal,
    ToolboxService,
)


class _Adapter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.available = True
        self.license_state = "available"
        self.items = (
            ToolboxCatalogItem(
                standard="ANSI Inch",
                family="hex bolt",
                size="1/4-20 x 1",
                source_path=str(root / "Browser" / "ANSI Inch" / "hex bolt.SLDPRT"),
                configuration="1/4-20 x 1",
                part_number="BOLT-025-1",
                properties=(("Description", "Hex Bolt"),),
            ),
            ToolboxCatalogItem(
                standard="ANSI Inch",
                family="washer",
                size="1/4",
                source_path=str(root / "Browser" / "ANSI Inch" / "washer.SLDPRT"),
                configuration="1/4",
                part_number="WASHER-025",
                properties=(("Description", "Flat Washer"),),
            ),
        )
        self.copy_calls = []

    def probe(self):
        return ToolboxProbeSnapshot(
            available=self.available,
            addin_loaded=self.available,
            root_path=str(self.root),
            database_path=str(self.root / "lang" / "english" / "swbrowser.sldedb"),
            version="2026",
            license_state=self.license_state,
            reason=None if self.available else "toolbox_unavailable",
        )

    def catalog_query(self, standard, family, size, limit):
        values = self.items
        if standard:
            values = tuple(item for item in values if item.standard.casefold() == standard.casefold())
        if family:
            values = tuple(item for item in values if item.family.casefold() == family.casefold())
        if size:
            values = tuple(item for item in values if item.size.casefold() == size.casefold())
        return values[:limit]

    def copy_component(self, item, destination):
        self.copy_calls.append((item, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"toolbox-copy")
        return str(destination)

    def component_properties(self, path, configuration):
        return (("Part Number", "BOLT-025-1"), ("Description", "Hex Bolt"))


def test_probe_requires_available_license_and_library(tmp_path: Path) -> None:
    adapter = _Adapter(tmp_path / "vendor")
    service = ToolboxService(adapter)
    assert service.probe().available is True

    adapter.available = False
    adapter.license_state = "unavailable"
    with pytest.raises(ToolboxRefusal, match="toolbox_unavailable"):
        service.catalog_query(standard="ANSI Inch", family="hex bolt", limit=10)


def test_catalog_is_bounded_and_unsupported_size_is_not_found(tmp_path: Path) -> None:
    service = ToolboxService(_Adapter(tmp_path / "vendor"), max_results=2)
    result = service.catalog_query(standard="ANSI Inch", limit=1)
    assert len(result) == 1
    assert result[0].standard == "ANSI Inch"

    with pytest.raises(ToolboxRefusal, match="toolbox_component_not_found"):
        service.catalog_query(
            standard="ANSI Inch",
            family="hex bolt",
            size="MISSING",
            limit=10,
        )


def test_resolve_always_creates_project_safe_copy_and_never_vendor_target(tmp_path: Path) -> None:
    vendor = tmp_path / "vendor"
    project = tmp_path / "project"
    vendor.mkdir()
    project.mkdir()
    adapter = _Adapter(vendor)
    service = ToolboxService(adapter)

    resolved = service.resolve_component(
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        project_directory=str(project),
    )
    assert Path(resolved.project_path).parent == project.resolve()
    assert Path(resolved.project_path).exists()
    assert Path(resolved.project_path) != Path(resolved.source_path)
    assert adapter.copy_calls

    with pytest.raises(ToolboxRefusal, match="project_directory_inside_toolbox"):
        service.resolve_component(
            standard="ANSI Inch",
            family="hex bolt",
            size="1/4-20 x 1",
            project_directory=str(vendor / "generated"),
        )


def test_component_properties_are_configuration_explicit(tmp_path: Path) -> None:
    adapter = _Adapter(tmp_path / "vendor")
    service = ToolboxService(adapter)
    props = service.component_properties("C:/project/bolt.SLDPRT", "1/4-20 x 1")
    assert ("Part Number", "BOLT-025-1") in props
