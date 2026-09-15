"""Bounded integration plugin namespace for parallel CDT-SolidWorks lanes."""

from .loader import (
    PLUGIN_CONTRACT_VERSION,
    PluginCompositionError,
    PluginRegistration,
    PluginSpec,
    discover_plugins,
    register_plugins,
    validate_plugin_modules,
)

__all__ = [
    "PLUGIN_CONTRACT_VERSION",
    "PluginCompositionError",
    "PluginRegistration",
    "PluginSpec",
    "discover_plugins",
    "register_plugins",
    "validate_plugin_modules",
]
