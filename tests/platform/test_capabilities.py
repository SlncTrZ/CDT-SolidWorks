from __future__ import annotations

from cdt_solidworks.platform.capabilities import (
    build_system_capabilities,
    build_system_status,
    default_runtime_context,
)
from cdt_solidworks.platform.models import DependencyState


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
