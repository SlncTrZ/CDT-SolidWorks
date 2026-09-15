"""Production-safe domain contract for SOLIDWORKS Toolbox consumption."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Protocol


class ToolboxRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class ToolboxPostconditionError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True)
class ToolboxProbeSnapshot:
    available: bool
    addin_loaded: bool
    root_path: str | None
    database_path: str | None
    version: str | None
    license_state: str
    reason: str | None = None


@dataclass(frozen=True)
class ToolboxCatalogItem:
    standard: str
    family: str
    size: str
    source_path: str
    configuration: str
    part_number: str | None = None
    properties: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class ToolboxResolvedComponent:
    standard: str
    family: str
    size: str
    source_path: str
    project_path: str
    configuration: str
    part_number: str | None
    properties: tuple[tuple[str, str | None], ...]


class ToolboxAdapter(Protocol):
    def probe(self) -> ToolboxProbeSnapshot: ...

    def catalog_query(
        self,
        standard: str | None,
        family: str | None,
        size: str | None,
        limit: int,
    ) -> tuple[ToolboxCatalogItem, ...]: ...

    def copy_component(self, item: ToolboxCatalogItem, destination: Path) -> str: ...

    def component_properties(
        self, path: str, configuration: str
    ) -> tuple[tuple[str, str | None], ...]: ...


class ToolboxService:
    """Expose bounded discovery and project copies without vendor-library mutation."""

    def __init__(self, adapter: ToolboxAdapter, *, max_results: int = 50) -> None:
        if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1:
            raise ValueError("max_results must be a positive integer")
        self._adapter = adapter
        self._max_results = max_results

    def probe(self) -> ToolboxProbeSnapshot:
        snapshot = self._adapter.probe()
        if not isinstance(snapshot, ToolboxProbeSnapshot):
            raise ToolboxPostconditionError("invalid_toolbox_probe")
        if snapshot.available and (
            not snapshot.addin_loaded
            or snapshot.license_state != "available"
            or not snapshot.root_path
            or not snapshot.database_path
        ):
            raise ToolboxPostconditionError("toolbox_probe_inconsistent")
        return snapshot

    def catalog_query(
        self,
        *,
        standard: str | None = None,
        family: str | None = None,
        size: str | None = None,
        limit: int = 25,
    ) -> tuple[ToolboxCatalogItem, ...]:
        self._require_available()
        standard = self._optional_identity("standard", standard)
        family = self._optional_identity("family", family)
        size = self._optional_identity("size", size)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ToolboxRefusal("invalid_catalog_limit")
        bounded_limit = min(limit, self._max_results)
        items = tuple(
            self._adapter.catalog_query(standard, family, size, bounded_limit)
        )
        if len(items) > bounded_limit:
            raise ToolboxPostconditionError("toolbox_catalog_limit_exceeded")
        self._validate_items(items)
        if not items and any(value is not None for value in (standard, family, size)):
            raise ToolboxRefusal("toolbox_component_not_found")
        return items

    def resolve_component(
        self,
        *,
        standard: str,
        family: str,
        size: str,
        project_directory: str,
        filename: str | None = None,
    ) -> ToolboxResolvedComponent:
        probe = self._require_available()
        matches = self.catalog_query(
            standard=self._identity("standard", standard),
            family=self._identity("family", family),
            size=self._identity("size", size),
            limit=2,
        )
        if len(matches) != 1:
            raise ToolboxRefusal(
                "toolbox_component_ambiguous" if matches else "toolbox_component_not_found"
            )
        item = matches[0]
        if not isinstance(project_directory, str) or not project_directory.strip():
            raise ToolboxRefusal("invalid_project_directory")
        raw_project = Path(project_directory).expanduser()
        if not raw_project.is_absolute():
            raise ToolboxRefusal("project_directory_not_absolute")
        project_candidate = raw_project.resolve(strict=False)
        vendor = Path(str(probe.root_path)).resolve(strict=False)
        if project_candidate == vendor or vendor in project_candidate.parents:
            raise ToolboxRefusal("project_directory_inside_toolbox")
        project = self._absolute_directory(project_directory)

        output_name = self._output_name(item, filename)
        destination = (project / output_name).resolve(strict=False)
        if destination.parent != project:
            raise ToolboxRefusal("invalid_project_component_filename")
        if destination.exists():
            raise ToolboxRefusal("project_component_exists", str(destination))
        copied = Path(self._adapter.copy_component(item, destination)).resolve(strict=False)
        if copied != destination:
            raise ToolboxPostconditionError(
                "project_copy_path_mismatch", f"expected={destination}; actual={copied}"
            )
        if not copied.exists() or not copied.is_file() or copied.stat().st_size <= 0:
            raise ToolboxPostconditionError("project_copy_missing_or_empty", str(copied))
        if copied == Path(item.source_path).resolve(strict=False):
            raise ToolboxPostconditionError("vendor_library_mutation_detected")
        return ToolboxResolvedComponent(
            standard=item.standard,
            family=item.family,
            size=item.size,
            source_path=item.source_path,
            project_path=str(copied),
            configuration=item.configuration,
            part_number=item.part_number,
            properties=item.properties,
        )

    def component_properties(
        self, path: str, configuration: str
    ) -> tuple[tuple[str, str | None], ...]:
        self._identity("component_path", path)
        self._identity("configuration", configuration)
        properties = tuple(self._adapter.component_properties(path, configuration))
        names: list[str] = []
        for key, value in properties:
            self._identity("property_name", key)
            if value is not None and not isinstance(value, str):
                raise ToolboxPostconditionError("invalid_toolbox_property_value", key)
            names.append(key)
        if len(set(names)) != len(names):
            raise ToolboxPostconditionError("duplicate_toolbox_property")
        return properties

    def _require_available(self) -> ToolboxProbeSnapshot:
        probe = self.probe()
        if not probe.available:
            raise ToolboxRefusal(probe.reason or "toolbox_unavailable")
        return probe

    @classmethod
    def _validate_items(cls, items: tuple[ToolboxCatalogItem, ...]) -> None:
        identities: set[tuple[str, str, str, str]] = set()
        for item in items:
            if not isinstance(item, ToolboxCatalogItem):
                raise ToolboxPostconditionError("invalid_toolbox_catalog_item")
            for label, value in (
                ("standard", item.standard),
                ("family", item.family),
                ("size", item.size),
                ("source_path", item.source_path),
                ("configuration", item.configuration),
            ):
                cls._identity(label, value)
            identity = (
                item.standard.casefold(),
                item.family.casefold(),
                item.size.casefold(),
                str(Path(item.source_path).resolve(strict=False)).casefold(),
            )
            if identity in identities:
                raise ToolboxPostconditionError("duplicate_toolbox_catalog_item")
            identities.add(identity)

    @staticmethod
    def _identity(label: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ToolboxRefusal(f"invalid_{label}")
        return value.strip()

    @classmethod
    def _optional_identity(cls, label: str, value: str | None) -> str | None:
        return None if value is None else cls._identity(label, value)

    @staticmethod
    def _absolute_directory(value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ToolboxRefusal("invalid_project_directory")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ToolboxRefusal("project_directory_not_absolute")
        path = path.resolve(strict=False)
        if not path.exists() or not path.is_dir():
            raise ToolboxRefusal("project_directory_missing", str(path))
        return path

    @staticmethod
    def _output_name(item: ToolboxCatalogItem, filename: str | None) -> str:
        if filename is not None:
            candidate = filename.strip()
            if not candidate or Path(candidate).name != candidate:
                raise ToolboxRefusal("invalid_project_component_filename")
            if Path(candidate).suffix.casefold() != ".sldprt":
                raise ToolboxRefusal("invalid_project_component_extension")
            return candidate
        parts = (item.family, item.size, item.part_number or item.configuration)
        stem = "-".join(parts)
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
        if not stem:
            stem = "toolbox-component"
        return f"{stem[:120]}.SLDPRT"
