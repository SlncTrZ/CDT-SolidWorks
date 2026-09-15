"""Bounded read-only SOLIDWORKS Toolbox discovery and project-copy adapter."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Callable, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState

from .domain import (
    ToolboxCatalogItem,
    ToolboxPostconditionError,
    ToolboxProbeSnapshot,
    ToolboxRefusal,
)


T = TypeVar("T")
_SW_DOC_PART = 1
_SW_HOLE_WIZARD_TOOLBOX_FOLDER = 52
_TOOLBOX_ROOT_PARENT_LIMIT = 3
_TOOLBOX_BROWSER_GUID = "{ED783340-D5DB-11d4-BD5A-00C04F019809}"
_PART_NUMBER_KEYS = ("Part Number", "PartNumber", "PART NUMBER")


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
            browser = Path(probe.root_path) / "Browser"
            if not browser.is_dir():
                raise _ToolboxNativeError("toolbox_browser_missing", str(browser))
            files = tuple(
                path
                for path in sorted(browser.rglob("*.sldprt"), key=lambda item: str(item).casefold())
                if path.is_file()
            )
            if len(files) > self.max_files:
                files = files[: self.max_files]
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
        shutil.copy2(source, target)
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            raise ToolboxPostconditionError("project_copy_verification_failed", str(target))
        return str(target)

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
