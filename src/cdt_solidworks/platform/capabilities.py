"""Capability and status semantics for the provider platform."""

from __future__ import annotations

from cdt_solidworks.platform.identity import PROVIDER_ID, PROVIDER_VERSION
from cdt_solidworks.platform.models import (
    CapabilityState,
    DependencyState,
    RuntimeContext,
    SystemCapabilities,
    SystemStatus,
)


_BASE_CAPABILITIES: tuple[CapabilityState, ...] = (
    CapabilityState(
        name="provider.help",
        implemented=True,
        available=True,
        backend="provider_platform",
    ),
    CapabilityState(
        name="provider.system_status",
        implemented=True,
        available=True,
        backend="provider_platform",
    ),
    CapabilityState(
        name="provider.system_capabilities",
        implemented=True,
        available=True,
        backend="provider_platform",
    ),
)


def default_runtime_context() -> RuntimeContext:
    """Return an honest context before any native SolidWorks lane is integrated."""

    return RuntimeContext(
        backend="provider_platform",
        dependencies=(
            DependencyState(
                name="solidworks",
                available=False,
                reason="native_dependency_not_registered",
            ),
        ),
        capabilities=(),
    )


def build_system_status(context: RuntimeContext) -> SystemStatus:
    if context.dependencies and all(item.available for item in context.dependencies):
        state = "ok"
    elif context.dependencies:
        state = "degraded"
    else:
        state = "ok"

    return SystemStatus(
        provider=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        backend=context.backend,
        state=state,
        dependencies=context.dependencies,
    )


def build_system_capabilities(context: RuntimeContext) -> SystemCapabilities:
    merged: dict[str, CapabilityState] = {item.name: item for item in _BASE_CAPABILITIES}
    for capability in context.capabilities:
        merged[capability.name] = capability

    return SystemCapabilities(
        provider=PROVIDER_ID,
        provider_version=PROVIDER_VERSION,
        backend=context.backend,
        capabilities=tuple(merged[name] for name in sorted(merged)),
    )
