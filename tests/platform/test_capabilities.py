from __future__ import annotations

import pytest

from cdt_solidworks.platform.capabilities import (
    build_system_capabilities,
    build_system_status,
    default_runtime_context,
)
from cdt_solidworks.platform.models import CapabilityState, DependencyState, RuntimeContext


def test_default_status_is_platform_ready_but_native_dependency_unavailable() -> None:
    context = default_runtime_context()
    status = build_system_status(context)

    assert status.provider == "solidworks"
    assert status.backend == "provider_platform"
    assert status.state == "degraded"
    assert status.dependencies == (
        DependencyState(
            name="solidworks",
            available=False,
            reason="native_dependency_not_registered",
            version=None,
            license=None,
        ),
    )


def test_capabilities_separate_implemented_from_available_without_native_claims() -> None:
    capabilities = build_system_capabilities(default_runtime_context())
    by_name = {item.name: item for item in capabilities.capabilities}

    assert set(by_name) == {
        "provider.help",
        "provider.system_status",
        "provider.system_capabilities",
    }
    assert all(item.implemented is True for item in by_name.values())
    assert all(item.available is True for item in by_name.values())
    assert all(item.backend == "provider_platform" for item in by_name.values())
    assert all(not item.name.startswith("solidworks.native") for item in by_name.values())


def test_duplicate_runtime_capability_fails_instead_of_silent_overwrite() -> None:
    duplicate = CapabilityState(
        name="solidworks.duplicate",
        implemented=True,
        available=True,
        backend="solidworks_com",
    )
    context = RuntimeContext(capabilities=(duplicate, duplicate))

    with pytest.raises(ValueError, match="duplicate capability: solidworks.duplicate"):
        build_system_capabilities(context)


def test_runtime_capability_cannot_override_platform_capability() -> None:
    context = RuntimeContext(
        capabilities=(
            CapabilityState(
                name="provider.help",
                implemented=True,
                available=True,
                backend="solidworks_com",
            ),
        )
    )

    with pytest.raises(ValueError, match="duplicate capability: provider.help"):
        build_system_capabilities(context)
