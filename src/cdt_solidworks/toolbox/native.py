"""Bounded read-only SOLIDWORKS Toolbox discovery and project-copy adapter."""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import sqlite3
import stat
from typing import Any, Callable, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document

from .domain import (
    ToolboxCatalogItem,
    ToolboxPostconditionError,
    ToolboxProbeSnapshot,
    ToolboxRefusal,
)


T = TypeVar("T")
_SW_DOC_PART = 1
_SW_SPECIFY_CONFIGURATION = 3
_SW_SET_VALUE_SUCCESS = 0
_SW_HOLE_WIZARD_TOOLBOX_FOLDER = 52
_TOOLBOX_ROOT_PARENT_LIMIT = 3
_TOOLBOX_BROWSER_GUID = "{ED783340-D5DB-11d4-BD5A-00C04F019809}"
_PART_NUMBER_KEYS = ("Part Number", "PartNumber", "PART NUMBER")
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_DB_VALUE_FIELD = re.compile(r"\[([^\[\]]+)\]")
_SYSTEM_LENGTH_FACTORS = {
    "INCH": 0.0254,
    "MILLIMETER": 0.001,
    "METER": 1.0,
}


class _ToolboxNativeError(NativeRuntimeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code, "toolbox_native", code if detail is None else f"{code}: {detail}")


class ToolboxNativeAdapter:
    """Consume installed Toolbox content without writing its vendor library."""

    def __init__(
        self,
        session: Any,
        *,
        timeout: float = 30.0,
        max_files: int = 500,
        max_configurations_per_file: int = 500,
        max_properties: int = 100,
    ) -> None:
        self.session = session
        self.api = session.api
        self.timeout = float(timeout)
        self.max_files = int(max_files)
        self.max_configurations_per_file = int(max_configurations_per_file)
        self.max_properties = int(max_properties)

    def probe(self) -> ToolboxProbeSnapshot:
        return self._run("toolbox_probe", True, self._probe)

    def catalog_query(
        self,
        standard: str | None,
        family: str | None,
        size: str | None,
        limit: int,
    ) -> tuple[ToolboxCatalogItem, ...]:
        def operation(app: Any) -> tuple[ToolboxCatalogItem, ...]:
            probe = self._probe(app)
            if not probe.available or not probe.root_path:
                raise _ToolboxNativeError(probe.reason or "toolbox_unavailable")
            if standard and family and probe.database_path:
                database_results = self._database_catalog_query(
                    probe,
                    standard=standard,
                    family=family,
                    size=size,
                    limit=limit,
                )
                if database_results:
                    return database_results
            browser = Path(probe.root_path) / "Browser"
            if not browser.is_dir():
                raise _ToolboxNativeError("toolbox_browser_missing", str(browser))
            files = self._bounded_part_files(browser)
            results: list[ToolboxCatalogItem] = []
            for path in files:
                relative = path.relative_to(browser)
                if not relative.parts:
                    continue
                item_standard = relative.parts[0]
                item_family = path.stem
                if standard and item_standard.casefold() != standard.casefold():
                    continue
                if family and item_family.casefold() != family.casefold():
                    continue
                for configuration in self._configuration_names(app, path):
                    if size and configuration.casefold() != size.casefold():
                        continue
                    properties = self._read_properties(app, path, configuration)
                    part_number = next(
                        (
                            value
                            for key, value in properties
                            if key in _PART_NUMBER_KEYS and value
                        ),
                        None,
                    )
                    results.append(
                        ToolboxCatalogItem(
                            standard=item_standard,
                            family=item_family,
                            size=configuration,
                            source_path=str(path.resolve(strict=False)),
                            configuration=configuration,
                            part_number=part_number,
                            properties=properties,
                        )
                    )
                    if len(results) >= limit:
                        return tuple(results)
            return tuple(results)

        return self._run("toolbox_catalog_query", True, operation)

    def _database_catalog_query(
        self,
        probe: ToolboxProbeSnapshot,
        *,
        standard: str,
        family: str,
        size: str | None,
        limit: int,
    ) -> tuple[ToolboxCatalogItem, ...]:
        if not probe.database_path or not probe.root_path:
            return ()
        database = Path(probe.database_path).resolve(strict=False)
        if not database.is_file():
            return ()
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error:
            return ()
        connection.row_factory = sqlite3.Row
        try:
            standard_row = connection.execute(
                "SELECT Name, TableNamePrefix, DefaultUnits FROM Standards "
                "WHERE lower(Name)=lower(?) AND enabled=1 AND Installed=1 AND IsToolbox=1 LIMIT 1",
                (standard,),
            ).fetchone()
            if standard_row is None:
                return ()
            prefix = str(standard_row["TableNamePrefix"] or "")
            if not _SQL_IDENTIFIER.fullmatch(prefix.rstrip("_")):
                raise _ToolboxNativeError("toolbox_database_prefix_invalid", prefix)
            table_names = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            )
            table_names_by_casefold = {
                name.casefold(): name for name in table_names
            }
            type_prefix = f"{prefix}TYPE_".casefold()
            type_tables = tuple(
                name
                for name in table_names
                if name.casefold().startswith(type_prefix)
                and _SQL_IDENTIFIER.fullmatch(name)
            )
            for type_table in type_tables:
                columns = {
                    str(row[1]).casefold()
                    for row in connection.execute(f'PRAGMA table_info("{type_table}")')
                }
                required = {
                    "filename",
                    "configurationtable",
                    "datatable",
                    "enabled",
                }
                if not required.issubset(columns):
                    continue
                rows = connection.execute(
                    f'SELECT * FROM "{type_table}" WHERE enabled=1'
                ).fetchall()
                for row in rows:
                    filename = str(row["Filename"] or "").strip()
                    if not filename:
                        continue
                    relative_parts = tuple(
                        part for part in re.split(r"[\\/]+", filename) if part
                    )
                    if not relative_parts:
                        continue
                    item_family = Path(relative_parts[-1]).stem
                    if item_family.casefold() != family.casefold():
                        continue
                    data_table_reference = self._database_table_name(
                        prefix, str(row["DataTable"] or "")
                    )
                    config_table_reference = self._database_table_name(
                        prefix, str(row["ConfigurationTable"] or "")
                    )
                    data_table = table_names_by_casefold.get(
                        data_table_reference.casefold()
                    )
                    config_table = table_names_by_casefold.get(
                        config_table_reference.casefold()
                    )
                    if data_table is None or config_table is None:
                        raise _ToolboxNativeError(
                            "toolbox_database_metadata_invalid", item_family
                        )
                    size_template = self._database_size_template(
                        connection, config_table
                    )
                    if size_template is None:
                        continue
                    source = self._resolve_database_source(
                        root_path=Path(probe.root_path),
                        standard=str(standard_row["Name"]),
                        relative_parts=relative_parts,
                        family=item_family,
                    )
                    if source is None:
                        continue
                    units = str(row["DataTableUnits"] or standard_row["DefaultUnits"] or "")
                    results = self._database_size_items(
                        connection,
                        data_table=data_table,
                        standard=str(standard_row["Name"]),
                        family=item_family,
                        source=source,
                        size_template=size_template,
                        requested_size=size,
                        units=units,
                        limit=limit,
                    )
                    if results:
                        return results
            return ()
        except sqlite3.Error as exc:
            raise _ToolboxNativeError("toolbox_database_read_failed", str(exc)) from exc
        finally:
            connection.close()

    def _resolve_database_source(
        self,
        *,
        root_path: Path,
        standard: str,
        relative_parts: tuple[str, ...],
        family: str,
    ) -> Path | None:
        browser = root_path / "Browser"
        candidate = (browser / Path(*relative_parts)).resolve(strict=False)
        if candidate.is_file():
            return candidate
        standard_root = browser / standard
        if not standard_root.is_dir():
            return None
        matches: list[Path] = []
        for path in self._bounded_part_files(standard_root):
            if path.stem.casefold() == family.casefold():
                matches.append(path.resolve(strict=False))
                if len(matches) > 1:
                    raise _ToolboxNativeError(
                        "toolbox_database_source_ambiguous", family
                    )
        return matches[0] if matches else None

    def _bounded_part_files(self, root: Path) -> tuple[Path, ...]:
        results: list[Path] = []
        for directory, directory_names, file_names in os.walk(root):
            directory_names.sort(key=str.casefold)
            for name in sorted(file_names, key=str.casefold):
                if Path(name).suffix.casefold() != ".sldprt":
                    continue
                path = Path(directory) / name
                if not path.is_file():
                    continue
                results.append(path)
                if len(results) >= self.max_files:
                    return tuple(results)
        return tuple(results)

    @staticmethod
    def _database_table_name(prefix: str, reference: str) -> str:
        normalized = reference.strip()
        if normalized.startswith("+"):
            normalized = f"{prefix}{normalized[1:]}"
        if not _SQL_IDENTIFIER.fullmatch(normalized):
            raise _ToolboxNativeError("toolbox_database_table_invalid", normalized)
        return normalized

    @staticmethod
    def _database_size_template(
        connection: sqlite3.Connection, config_table: str
    ) -> str | None:
        row = connection.execute(
            f'SELECT ValueList FROM "{config_table}" '
            "WHERE Controller=1 ORDER BY Grid_Item_Number LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        template = str(row[0] or "").strip()
        if not template.startswith("{") or not template.endswith("}"):
            return None
        body = template[1:-1]
        if "<" in body or ">" in body or "$" in body:
            return None
        return body

    def _database_size_items(
        self,
        connection: sqlite3.Connection,
        *,
        data_table: str,
        standard: str,
        family: str,
        source: Path,
        size_template: str,
        requested_size: str | None,
        units: str,
        limit: int,
    ) -> tuple[ToolboxCatalogItem, ...]:
        rows = connection.execute(f'SELECT * FROM "{data_table}"').fetchall()
        results: list[ToolboxCatalogItem] = []
        for row in rows:
            values = {str(key).casefold(): row[key] for key in row.keys()}
            enabled = values.get("enabled", 1)
            if enabled is not None and int(enabled) != 1:
                continue
            rendered = self._render_database_value(size_template, values)
            if rendered is None:
                continue
            if requested_size and rendered.casefold() != requested_size.casefold():
                continue
            properties = tuple(
                (str(key), None if row[key] is None else str(row[key]))
                for key in row.keys()
                if str(key).casefold() not in {"enabled", "key"}
            ) + (("Toolbox Units", units),)
            results.append(
                ToolboxCatalogItem(
                    standard=standard,
                    family=family,
                    size=rendered,
                    source_path=str(source),
                    configuration="Default",
                    part_number=None,
                    properties=properties,
                )
            )
            if len(results) >= limit:
                break
        return tuple(results)

    @staticmethod
    def _render_database_value(
        template: str, values: dict[str, Any]
    ) -> str | None:
        missing = False

        def replacement(match: re.Match[str]) -> str:
            nonlocal missing
            key = match.group(1).strip().casefold()
            value = values.get(key)
            if value is None:
                missing = True
                return ""
            return str(value).strip()

        rendered = _DB_VALUE_FIELD.sub(replacement, template).strip()
        if missing or "[" in rendered or "]" in rendered or not rendered:
            return None
        return rendered

    def copy_component(self, item: ToolboxCatalogItem, destination: Path) -> str:
        source = Path(item.source_path).resolve(strict=False)
        target = Path(destination).resolve(strict=False)
        if source == target:
            raise ToolboxRefusal("vendor_library_mutation_forbidden")
        if not source.exists() or not source.is_file():
            raise ToolboxRefusal("toolbox_source_missing", str(source))
        if not target.parent.exists() or not target.parent.is_dir():
            raise ToolboxRefusal("project_directory_missing", str(target.parent))
        if target.exists():
            raise ToolboxRefusal("project_component_exists", str(target))
        try:
            shutil.copy2(source, target)
            target.chmod(target.stat().st_mode | stat.S_IWRITE)
            if not target.exists() or target.stat().st_size != source.stat().st_size:
                raise ToolboxPostconditionError("project_copy_verification_failed", str(target))
            if self._requires_database_materialization(item):
                probe = self.probe()
                assignments = self._database_materialization_plan(probe, item)
                self._materialize_project_copy(
                    target,
                    configuration=item.configuration,
                    assignments=assignments,
                )
            return str(target)
        except Exception:
            if target.exists():
                try:
                    target.chmod(target.stat().st_mode | stat.S_IWRITE)
                    target.unlink()
                except OSError:
                    pass
            raise

    @staticmethod
    def _requires_database_materialization(item: ToolboxCatalogItem) -> bool:
        return item.configuration.casefold() == "default" and any(
            key.casefold() == "toolbox units" for key, _value in item.properties
        )

    def _materialize_project_copy(
        self,
        target: Path,
        *,
        configuration: str,
        assignments: tuple[tuple[str, float], ...],
    ) -> None:
        def operation(app: Any) -> None:
            if self.api.get_open_document(app, str(target)) is not None:
                raise _ToolboxNativeError(
                    "project_component_already_open", str(target)
                )
            model = None
            try:
                model, errors, warnings = self.api.open_document(
                    app,
                    str(target),
                    _SW_DOC_PART,
                    read_only=False,
                    silent=True,
                    configuration=configuration,
                )
                if model is None or int(errors) != 0:
                    raise _ToolboxNativeError(
                        "project_component_open_failed",
                        f"path={target}; errors={int(errors)}; warnings={int(warnings)}",
                    )
                for dimension_name, value in assignments:
                    dimension = self.api._member(model, "Parameter", dimension_name)
                    if dimension is None:
                        raise _ToolboxNativeError(
                            "toolbox_materialization_dimension_missing",
                            dimension_name,
                        )
                    status = int(
                        self.api._member(
                            dimension,
                            "SetSystemValue3",
                            float(value),
                            _SW_SPECIFY_CONFIGURATION,
                            self.api.string_array((configuration,)),
                        )
                    )
                    if status != _SW_SET_VALUE_SUCCESS:
                        raise _ToolboxNativeError(
                            "toolbox_materialization_dimension_set_failed",
                            f"{dimension_name}; native_status={status}",
                        )
                rebuilt = rebuild_document(model, self.api)
                if not rebuilt.success:
                    detail = ",".join(
                        f"{issue.feature_name}:{issue.error_code}"
                        for issue in rebuilt.feature_issues
                        if not issue.is_warning
                    )
                    raise _ToolboxNativeError(
                        "toolbox_materialization_rebuild_failed",
                        detail or "native_rebuild_failed",
                    )
                saved, save_errors, save_warnings = self.api.save_document(model)
                if not saved or int(save_errors) != 0:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_save_failed",
                        f"errors={int(save_errors)}; warnings={int(save_warnings)}",
                    )
                self.api.close_document(app, self.api.document_title(model))
                model = None
                reopened, errors, warnings = self.api.open_document(
                    app,
                    str(target),
                    _SW_DOC_PART,
                    read_only=True,
                    silent=True,
                    configuration=configuration,
                )
                model = reopened
                if model is None or int(errors) != 0:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_reopen_failed",
                        f"errors={int(errors)}; warnings={int(warnings)}",
                    )
                for dimension_name, expected in assignments:
                    dimension = self.api._member(model, "Parameter", dimension_name)
                    if dimension is None:
                        raise _ToolboxNativeError(
                            "toolbox_materialization_dimension_missing_after_reopen",
                            dimension_name,
                        )
                    values = self.api._member(
                        dimension,
                        "GetSystemValue3",
                        _SW_SPECIFY_CONFIGURATION,
                        self.api.string_array((configuration,)),
                    )
                    if values is None:
                        raise _ToolboxNativeError(
                            "toolbox_materialization_readback_missing",
                            dimension_name,
                        )
                    if isinstance(values, (tuple, list)):
                        actual = float(values[0]) if values else None
                    else:
                        actual = float(values)
                    if actual is None or abs(actual - expected) > 1e-9:
                        raise _ToolboxNativeError(
                            "toolbox_materialization_readback_mismatch",
                            f"{dimension_name}; expected={expected}; actual={actual}",
                        )
            finally:
                if model is not None:
                    try:
                        self.api.close_document(app, self.api.document_title(model))
                    except Exception:
                        pass

        result = self.session.execute(
            operation,
            stage="toolbox_component_materialize",
            timeout=self.timeout,
            mutation=True,
        )
        if result.state is NativeCallState.SUCCESS:
            return
        failure = result.failure
        detail = failure.message if failure is not None else result.state.value
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            raise ToolboxPostconditionError(
                "native_state_uncertain", f"call_id={result.call_id}; {detail}"
            )
        code = failure.code if failure is not None else result.state.value
        raise ToolboxRefusal(code, detail)

    def _database_materialization_plan(
        self,
        probe: ToolboxProbeSnapshot,
        item: ToolboxCatalogItem,
    ) -> tuple[tuple[str, float], ...]:
        if not probe.database_path:
            raise _ToolboxNativeError("toolbox_database_missing")
        database = Path(probe.database_path).resolve(strict=False)
        properties = {
            key.casefold(): value
            for key, value in item.properties
            if value is not None
        }
        units = str(properties.get("toolbox units") or "").strip().upper()
        factor = _SYSTEM_LENGTH_FACTORS.get(units)
        if factor is None:
            raise _ToolboxNativeError(
                "toolbox_materialization_units_unsupported", units or "missing"
            )
        try:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise _ToolboxNativeError("toolbox_database_read_failed", str(exc)) from exc
        connection.row_factory = sqlite3.Row
        try:
            standard_row = connection.execute(
                "SELECT Name, TableNamePrefix FROM Standards "
                "WHERE lower(Name)=lower(?) AND enabled=1 AND Installed=1 AND IsToolbox=1 LIMIT 1",
                (item.standard,),
            ).fetchone()
            if standard_row is None:
                raise _ToolboxNativeError(
                    "toolbox_materialization_standard_missing", item.standard
                )
            prefix = str(standard_row["TableNamePrefix"] or "")
            if not _SQL_IDENTIFIER.fullmatch(prefix.rstrip("_")):
                raise _ToolboxNativeError("toolbox_database_prefix_invalid", prefix)
            table_names = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            )
            table_names_by_casefold = {
                name.casefold(): name for name in table_names
            }
            config_tables: set[str] = set()
            type_prefix = f"{prefix}TYPE_".casefold()
            for type_table in table_names:
                if not type_table.casefold().startswith(type_prefix):
                    continue
                if not _SQL_IDENTIFIER.fullmatch(type_table):
                    continue
                columns = {
                    str(row[1]).casefold()
                    for row in connection.execute(f'PRAGMA table_info("{type_table}")')
                }
                if not {"filename", "configurationtable", "enabled"}.issubset(columns):
                    continue
                for row in connection.execute(
                    f'SELECT * FROM "{type_table}" WHERE enabled=1'
                ):
                    filename = str(row["Filename"] or "").strip()
                    if not filename:
                        continue
                    relative_parts = tuple(
                        part for part in re.split(r"[\\/]+", filename) if part
                    )
                    if not relative_parts:
                        continue
                    family = Path(relative_parts[-1]).stem
                    if family.casefold() != item.family.casefold():
                        continue
                    reference = self._database_table_name(
                        prefix, str(row["ConfigurationTable"] or "")
                    )
                    config_table = table_names_by_casefold.get(reference.casefold())
                    if config_table is None:
                        raise _ToolboxNativeError(
                            "toolbox_database_metadata_invalid", item.family
                        )
                    config_tables.add(config_table)
            if not config_tables:
                raise _ToolboxNativeError(
                    "toolbox_materialization_metadata_missing", item.family
                )
            if len(config_tables) != 1:
                raise _ToolboxNativeError(
                    "toolbox_materialization_metadata_ambiguous", item.family
                )
            config_table = next(iter(config_tables))
            assignments: list[tuple[str, float]] = []
            rows = connection.execute(
                f'SELECT * FROM "{config_table}" ORDER BY Grid_Item_Number'
            ).fetchall()
            for row in rows:
                dimension = str(row["Dimension"] or "").strip()
                if not dimension:
                    continue
                grid_name = str(row["Grid_Item_Name"] or dimension).strip() or dimension
                if dimension.casefold() == "suppression":
                    raise _ToolboxNativeError(
                        "toolbox_materialization_requires_additional_configuration",
                        grid_name,
                    )
                alt_source = str(row["AltDataSource"] or "").strip()
                relation = str(row["RelationField"] or "").strip()
                if alt_source or relation:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_requires_additional_configuration",
                        grid_name,
                    )
                if int(row["NoUnitConversion"] or 0) != 0:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_dimension_units_unsupported",
                        grid_name,
                    )
                template = str(row["ValueList"] or "").strip()
                if template.startswith("{") and template.endswith("}"):
                    template = template[1:-1]
                rendered = self._render_database_value(template, properties)
                if rendered is None:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_value_missing", grid_name
                    )
                try:
                    value = float(rendered) * factor
                except ValueError as exc:
                    raise _ToolboxNativeError(
                        "toolbox_materialization_value_invalid", grid_name
                    ) from exc
                assignments.append((dimension, value))
            if not assignments:
                raise _ToolboxNativeError(
                    "toolbox_materialization_dimensions_missing", item.family
                )
            return tuple(assignments)
        except sqlite3.Error as exc:
            raise _ToolboxNativeError("toolbox_database_read_failed", str(exc)) from exc
        finally:
            connection.close()

    def component_properties(
        self, path: str, configuration: str
    ) -> tuple[tuple[str, str | None], ...]:
        component = Path(path).resolve(strict=False)

        def operation(app: Any) -> tuple[tuple[str, str | None], ...]:
            if not component.exists() or not component.is_file():
                raise _ToolboxNativeError("toolbox_component_not_found", str(component))
            return self._read_properties(app, component, configuration)

        return self._run("toolbox_component_properties", False, operation)

    def _probe(self, app: Any) -> ToolboxProbeSnapshot:
        addin = self._toolbox_addin(app)
        root = self._toolbox_root(app)
        if not root:
            return ToolboxProbeSnapshot(
                available=False,
                addin_loaded=addin is not None,
                root_path=None,
                database_path=None,
                version=self._version(app, addin),
                license_state="unknown" if addin is None else "available",
                reason="toolbox_root_unavailable",
            )
        root_path = Path(root).resolve(strict=False)
        if not root_path.is_dir():
            return ToolboxProbeSnapshot(
                available=False,
                addin_loaded=addin is not None,
                root_path=str(root_path),
                database_path=None,
                version=self._version(app, addin),
                license_state="unknown" if addin is None else "available",
                reason="toolbox_library_missing",
            )
        databases = tuple(
            path
            for path in sorted(root_path.glob("lang/*/swbrowser.sldedb"), key=lambda item: str(item).casefold())
            if path.is_file()
        )
        database = databases[0] if databases else None
        if addin is None:
            reason = "toolbox_addin_unavailable_or_unlicensed"
        elif database is None:
            reason = "toolbox_database_missing"
        elif not (root_path / "Browser").is_dir():
            reason = "toolbox_browser_missing"
        else:
            reason = None
        return ToolboxProbeSnapshot(
            available=reason is None,
            addin_loaded=addin is not None,
            root_path=str(root_path),
            database_path=None if database is None else str(database.resolve(strict=False)),
            version=self._version(app, addin),
            license_state="available" if addin is not None else "unknown",
            reason=reason,
        )

    def _toolbox_addin(self, app: Any) -> Any | None:
        try:
            addin = self.api._member(app, "GetAddInObject", _TOOLBOX_BROWSER_GUID)
        except Exception:
            addin = None
        if addin is not None:
            return addin
        if getattr(self.session, "ownership", None) is not ApplicationOwnership.PROVIDER_OWNED:
            return None
        try:
            executable = Path(str(self.api._member(app, "GetExecutablePath") or "")).resolve(
                strict=False
            )
        except Exception:
            return None
        if not executable.name:
            return None
        install_directory = executable.parent if executable.suffix.casefold() == ".exe" else executable
        browser_dll = install_directory / "Toolbox" / "SwBrowser.dll"
        if not browser_dll.is_file():
            return None
        try:
            self.api._member(app, "LoadAddIn", str(browser_dll))
            return self.api._member(app, "GetAddInObject", _TOOLBOX_BROWSER_GUID)
        except Exception:
            return None

    def _toolbox_root(self, app: Any) -> str | None:
        client = getattr(self.api, "_client", None)
        constants = getattr(client, "constants", None) if client is not None else None
        preference = getattr(
            constants, "swHoleWizardToolBoxFolder", _SW_HOLE_WIZARD_TOOLBOX_FOLDER
        )
        try:
            value = self.api._member(app, "GetUserPreferenceStringValue", int(preference))
        except Exception:
            return None
        normalized = str(value or "").strip()
        if not normalized:
            return None
        return self._normalize_toolbox_root(normalized)

    @staticmethod
    def _normalize_toolbox_root(value: str) -> str:
        candidate = Path(value).resolve(strict=False)
        base = candidate.parent if candidate.is_file() else candidate
        search_roots = (base, *tuple(base.parents)[:_TOOLBOX_ROOT_PARENT_LIMIT])
        for root in search_roots:
            browser = root / "Browser"
            databases = tuple(root.glob("lang/*/swbrowser.sldedb"))
            if browser.is_dir() and any(path.is_file() for path in databases):
                return str(root.resolve(strict=False))
        return str(candidate)

    def _configuration_names(self, app: Any, path: Path) -> tuple[str, ...]:
        model, opened_here = self._open_part(app, path)
        try:
            value = self.api._member(model, "GetConfigurationNames")
            if value is None:
                return ()
            if isinstance(value, str):
                names = (value,)
            else:
                names = tuple(str(item) for item in value)
            if len(names) > self.max_configurations_per_file:
                raise _ToolboxNativeError(
                    "toolbox_configuration_limit_exceeded", str(path)
                )
            return tuple(name for name in names if name.strip())
        finally:
            self._close_if_needed(app, model, opened_here)

    def _read_properties(
        self, app: Any, path: Path, configuration: str
    ) -> tuple[tuple[str, str | None], ...]:
        model, opened_here = self._open_part(app, path, configuration)
        try:
            config = self.api._member(model, "GetConfigurationByName", configuration)
            if config is None:
                raise _ToolboxNativeError("toolbox_configuration_not_found", configuration)
            manager = self.api._member(config, "CustomPropertyManager")
            if manager is None:
                return ()
            names = self.api._member(manager, "GetNames")
            if names is None:
                return ()
            if isinstance(names, str):
                raw_names = (names,)
            else:
                raw_names = tuple(str(item) for item in names)
            if len(raw_names) > self.max_properties:
                raise _ToolboxNativeError("toolbox_property_limit_exceeded")
            result: list[tuple[str, str | None]] = []
            for name in raw_names:
                value = self.api._member(manager, "Get", name)
                result.append((name, None if value is None else str(value)))
            return tuple(result)
        finally:
            self._close_if_needed(app, model, opened_here)

    def _open_part(
        self, app: Any, path: Path, configuration: str = ""
    ) -> tuple[Any, bool]:
        existing = self.api.get_open_document(app, str(path))
        if existing is not None:
            return existing, False
        model, errors, _warnings = self.api.open_document(
            app,
            str(path),
            _SW_DOC_PART,
            read_only=True,
            silent=True,
            configuration=configuration,
        )
        if model is None or int(errors) != 0:
            raise _ToolboxNativeError(
                "toolbox_component_open_failed", f"path={path}; errors={int(errors)}"
            )
        return model, True

    def _close_if_needed(self, app: Any, model: Any, opened_here: bool) -> None:
        if not opened_here:
            return
        try:
            self.api.close_document(app, self.api.document_title(model))
        except Exception:
            pass

    def _version(self, app: Any, addin: Any | None) -> str | None:
        if addin is not None:
            for member in ("Version", "MajorVersion"):
                try:
                    value = self.api._member(addin, member)
                    if value is not None:
                        return str(value)
                except Exception:
                    continue
        try:
            return str(self.api.revision_number(app))
        except Exception:
            return None

    def _run(
        self,
        stage: str,
        mutation: bool,
        operation: Callable[[Any], T],
    ) -> T:
        result = self.session.execute(
            operation,
            stage=stage,
            timeout=self.timeout,
            mutation=mutation,
        )
        if result.state is NativeCallState.SUCCESS:
            return result.value  # type: ignore[return-value]
        failure = result.failure
        detail = failure.message if failure is not None else result.state.value
        if result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH:
            raise ToolboxPostconditionError(
                "native_state_uncertain", f"call_id={result.call_id}; {detail}"
            )
        code = failure.code if failure is not None else result.state.value
        raise ToolboxRefusal(code, detail)
