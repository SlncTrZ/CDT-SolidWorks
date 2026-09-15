"""Deterministic bounded plugin discovery and registration for integration lanes."""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
import re
from dataclasses import dataclass
from time import perf_counter
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping, Sequence

from cdt_solidworks.platform.observability import SafeObserver


PLUGIN_CONTRACT_VERSION = 1
_PLUGIN_PACKAGE = "cdt_solidworks.integration.plugins"
_PLUGIN_MODULE_RE = re.compile(r"^agent[1-6]_[a-z0-9_]+$")
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


class PluginCompositionError(RuntimeError):
    """Fail-closed plugin composition error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, plugin_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.plugin_id = plugin_id


@dataclass(frozen=True)
class PluginSpec:
    module_name: str
    plugin_id: str
    order: int
    register_tools: Callable[[Any, Any], None]
    capability_descriptors: Callable[[Any], Iterable[Mapping[str, Any]]]


@dataclass(frozen=True)
class PluginRegistration:
    plugin_id: str
    order: int
    tool_names: tuple[str, ...]
    capability_names: tuple[str, ...]


def _safe_module_label(module: ModuleType) -> str:
    return str(getattr(module, "__name__", "<unnamed-plugin>"))


def _validate_plugin_module(module: ModuleType) -> PluginSpec:
    module_name = _safe_module_label(module)
    version = getattr(module, "PLUGIN_CONTRACT_VERSION", None)
    if version != PLUGIN_CONTRACT_VERSION:
        raise PluginCompositionError(
            "unsupported_plugin_contract",
            f"Plugin {module_name} must use contract version {PLUGIN_CONTRACT_VERSION}.",
        )

    plugin_id = getattr(module, "PLUGIN_ID", None)
    if not isinstance(plugin_id, str) or _PLUGIN_ID_RE.fullmatch(plugin_id) is None:
        raise PluginCompositionError(
            "invalid_plugin_id",
            f"Plugin {module_name} has an invalid PLUGIN_ID.",
        )

    order = getattr(module, "PLUGIN_ORDER", None)
    if not isinstance(order, int) or isinstance(order, bool) or not 1 <= order <= 6:
        raise PluginCompositionError(
            "invalid_plugin_order",
            f"Plugin {plugin_id} must use PLUGIN_ORDER from 1 through 6.",
            plugin_id=plugin_id,
        )

    register_tools = getattr(module, "register_tools", None)
    descriptors = getattr(module, "capability_descriptors", None)
    if not callable(register_tools) or not callable(descriptors):
        raise PluginCompositionError(
            "invalid_plugin_api",
            f"Plugin {plugin_id} must export register_tools and capability_descriptors callables.",
            plugin_id=plugin_id,
        )

    return PluginSpec(
        module_name=module_name,
        plugin_id=plugin_id,
        order=order,
        register_tools=register_tools,
        capability_descriptors=descriptors,
    )


def validate_plugin_modules(modules: Iterable[ModuleType]) -> tuple[PluginSpec, ...]:
    """Validate plugin contract fields, uniqueness and deterministic order."""

    specs = tuple(_validate_plugin_module(module) for module in modules)
    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    for spec in specs:
        if spec.plugin_id in seen_ids:
            raise PluginCompositionError(
                "duplicate_plugin_id",
                f"Duplicate plugin id: {spec.plugin_id}.",
                plugin_id=spec.plugin_id,
            )
        if spec.order in seen_orders:
            raise PluginCompositionError(
                "duplicate_plugin_order",
                f"Duplicate plugin order: {spec.order}.",
                plugin_id=spec.plugin_id,
            )
        seen_ids.add(spec.plugin_id)
        seen_orders.add(spec.order)
    return tuple(sorted(specs, key=lambda item: (item.order, item.plugin_id, item.module_name)))


def discover_plugins() -> tuple[PluginSpec, ...]:
    """Discover only packaged Agent 1-6 modules under the fixed plugin namespace."""

    try:
        package = importlib.import_module(_PLUGIN_PACKAGE)
    except Exception as exc:  # pragma: no cover - package is part of this distribution
        raise PluginCompositionError(
            "plugin_namespace_unavailable",
            "Packaged plugin namespace could not be loaded.",
        ) from exc

    modules: list[ModuleType] = []
    for module_info in sorted(
        pkgutil.iter_modules(package.__path__, package.__name__ + "."),
        key=lambda item: item.name,
    ):
        short_name = module_info.name.rsplit(".", 1)[-1]
        if _PLUGIN_MODULE_RE.fullmatch(short_name) is None:
            continue
        try:
            modules.append(importlib.import_module(module_info.name))
        except Exception as exc:
            raise PluginCompositionError(
                "plugin_import_failed",
                f"Plugin module {module_info.name} could not be imported.",
            ) from exc
    return validate_plugin_modules(modules)


def _tool_names(server: Any) -> set[str]:
    manager = getattr(server, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        raise PluginCompositionError(
            "tool_manager_unavailable",
            "MCP tool manager is unavailable for plugin composition.",
        )
    return {str(tool.name) for tool in manager.list_tools()}


def _numeric(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if number >= 0 else default
    return default


def _record_observation(
    observer: SafeObserver,
    *,
    tool_name: str,
    result: object | None,
    elapsed_ms: float,
    raised: bool,
) -> None:
    state: object = None
    call_id: str | None = None
    failure_class: str | None = None
    queue_wait_ms = 0.0
    timed_out = False
    reconciled = False

    if isinstance(result, Mapping):
        state = result.get("state")
        raw_call_id = result.get("call_id")
        call_id = raw_call_id if isinstance(raw_call_id, str) else None
        queue_wait_ms = _numeric(result.get("queue_wait_ms"))
        error = result.get("error")
        if isinstance(error, Mapping):
            code = error.get("code")
            native_code = error.get("native_code")
            if isinstance(code, str):
                failure_class = code
            elif isinstance(native_code, str):
                failure_class = native_code
            timed_out = code == "timeout" or (
                isinstance(native_code, str) and native_code.startswith("timeout")
            )
        reconciled = tool_name.endswith("_reconcile") and state == "success"

    if raised:
        outcome = "failure"
        failure_class = "internal_error"
    elif state == "success":
        outcome = "success"
    elif state == "uncertain":
        outcome = "uncertain"
    elif timed_out:
        outcome = "timeout"
    else:
        outcome = "failure"

    observer.record(
        tool_name=tool_name,
        outcome=outcome,
        provider_latency_ms=elapsed_ms,
        dependency_latency_ms=None,
        queue_wait_ms=queue_wait_ms,
        operation_id=call_id,
        failure_class=failure_class,
        timed_out=timed_out,
        reconciled=reconciled,
    )


def _observed_tool(func: Callable[..., Any], *, tool_name: str, observer: SafeObserver):
    signature = inspect.signature(func, eval_str=True)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        raise PluginCompositionError(
            "open_tool_schema",
            f"Plugin tool {tool_name} may not use **kwargs.",
        )

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any):
            started = perf_counter()
            result: object | None = None
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception:
                _record_observation(
                    observer,
                    tool_name=tool_name,
                    result=None,
                    elapsed_ms=(perf_counter() - started) * 1000,
                    raised=True,
                )
                raise
            finally:
                if result is not None:
                    _record_observation(
                        observer,
                        tool_name=tool_name,
                        result=result,
                        elapsed_ms=(perf_counter() - started) * 1000,
                        raised=False,
                    )

        async_wrapper.__signature__ = signature
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any):
        started = perf_counter()
        result: object | None = None
        try:
            result = func(*args, **kwargs)
            return result
        except Exception:
            _record_observation(
                observer,
                tool_name=tool_name,
                result=None,
                elapsed_ms=(perf_counter() - started) * 1000,
                raised=True,
            )
            raise
        finally:
            if result is not None:
                _record_observation(
                    observer,
                    tool_name=tool_name,
                    result=result,
                    elapsed_ms=(perf_counter() - started) * 1000,
                    raised=False,
                )

    wrapper.__signature__ = signature
    return wrapper


class _PluginServerProxy:
    def __init__(self, server: Any, known_tool_names: set[str], observer: SafeObserver) -> None:
        self._server = server
        self._known_tool_names = known_tool_names
        self._observer = observer
        self.registered_names: list[str] = []

    def tool(self, *, name: str, description: str, **kwargs: Any):
        if name in self._known_tool_names:
            raise PluginCompositionError(
                "duplicate_public_tool",
                f"Duplicate public tool: {name}.",
            )

        def decorator(func: Callable[..., Any]):
            if name in self._known_tool_names:
                raise PluginCompositionError(
                    "duplicate_public_tool",
                    f"Duplicate public tool: {name}.",
                )
            wrapped = _observed_tool(func, tool_name=name, observer=self._observer)
            registered = self._server.tool(name=name, description=description, **kwargs)(wrapped)
            self._known_tool_names.add(name)
            self.registered_names.append(name)
            return registered

        return decorator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server, name)


def register_plugins(
    server: Any,
    runtime: Any,
    *,
    plugins: Sequence[PluginSpec] | None = None,
) -> tuple[PluginRegistration, ...]:
    """Register plugins in deterministic order and attach truthful capabilities to runtime."""

    selected = tuple(discover_plugins() if plugins is None else plugins)
    known_tool_names = _tool_names(server)
    observer = getattr(runtime, "observer", None)
    if observer is None:
        observer = SafeObserver()
        if hasattr(runtime, "set_observer"):
            runtime.set_observer(observer)

    registrations: list[PluginRegistration] = []
    for plugin in selected:
        proxy = _PluginServerProxy(server, known_tool_names, observer)
        try:
            plugin.register_tools(proxy, runtime)
        except PluginCompositionError:
            raise
        except Exception as exc:
            raise PluginCompositionError(
                "plugin_registration_failed",
                f"Plugin {plugin.plugin_id} failed during tool registration.",
                plugin_id=plugin.plugin_id,
            ) from exc

        try:
            raw_descriptors = tuple(plugin.capability_descriptors(runtime))
        except Exception as exc:
            raise PluginCompositionError(
                "plugin_capability_failed",
                f"Plugin {plugin.plugin_id} failed while declaring capabilities.",
                plugin_id=plugin.plugin_id,
            ) from exc

        if (
            raw_descriptors
            and not proxy.registered_names
            and any(bool(item.get("available")) for item in raw_descriptors)
        ):
            raise PluginCompositionError(
                "plugin_capability_without_tools",
                f"Plugin {plugin.plugin_id} declared available capabilities without registering public tools.",
                plugin_id=plugin.plugin_id,
            )
        try:
            runtime.register_capability_descriptors(raw_descriptors, source=plugin.plugin_id)
            register_resolver = getattr(runtime, "register_capability_resolver", None)
            if callable(register_resolver) and raw_descriptors:
                register_resolver(
                    lambda provider=plugin.capability_descriptors: provider(runtime),
                    source=plugin.plugin_id,
                )
        except (TypeError, ValueError) as exc:
            raise PluginCompositionError(
                "invalid_plugin_capability",
                f"Plugin {plugin.plugin_id} declared an invalid or duplicate capability.",
                plugin_id=plugin.plugin_id,
            ) from exc

        capability_names = tuple(str(item["name"]) for item in raw_descriptors)
        registrations.append(
            PluginRegistration(
                plugin_id=plugin.plugin_id,
                order=plugin.order,
                tool_names=tuple(proxy.registered_names),
                capability_names=capability_names,
            )
        )
    return tuple(registrations)
