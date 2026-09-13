"""Configuration domain contract for lane D."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ConfigurationRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


class ConfigurationPostconditionError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason if detail is None else f"{reason}: {detail}")


@dataclass(frozen=True)
class ConfigurationSnapshot:
    name: str
    dimensions: tuple[tuple[str, float | None], ...]
    properties: tuple[tuple[str, str | None], ...]
    component_states: tuple[tuple[str, str | None], ...]


class ConfigurationAdapter(Protocol):
    def list_configurations(self, document_id: str) -> tuple[str, ...]: ...

    def create_configuration(
        self, document_id: str, name: str, parent: str | None
    ) -> None: ...

    def activate_configuration(self, document_id: str, name: str) -> None: ...

    def read_active_configuration(self, document_id: str) -> str: ...

    def read_dimension(
        self, document_id: str, configuration: str, dimension_name: str
    ) -> float | None: ...

    def read_property(
        self, document_id: str, configuration: str, property_name: str
    ) -> str | None: ...

    def read_component_state(
        self, document_id: str, configuration: str, component_id: str
    ) -> str | None: ...


class ConfigurationService:
    """Ensures activation and configuration-local state are verified by read-back."""

    def __init__(self, adapter: ConfigurationAdapter) -> None:
        self._adapter = adapter

    def list(self, document_id: str) -> tuple[str, ...]:
        self._require_identity("document_id", document_id)
        names = tuple(self._adapter.list_configurations(document_id))
        if any(not name.strip() for name in names):
            raise ConfigurationPostconditionError("invalid_configuration_identity")
        if len(set(names)) != len(names):
            raise ConfigurationPostconditionError("duplicate_configuration_identity")
        return names

    def create(
        self, document_id: str, name: str, parent: str | None = None
    ) -> None:
        self._require_identity("document_id", document_id)
        self._require_identity("configuration_name", name)
        if parent is not None:
            self._require_identity("parent_configuration", parent)
            if parent not in self.list(document_id):
                raise ConfigurationRefusal("missing_parent_configuration", parent)
        if name in self.list(document_id):
            raise ConfigurationRefusal("configuration_exists", name)
        self._adapter.create_configuration(document_id, name, parent)
        if name not in self.list(document_id):
            raise ConfigurationPostconditionError(
                "configuration_create_readback_missing", name
            )

    def activate(self, document_id: str, name: str) -> str:
        self._require_identity("document_id", document_id)
        self._require_identity("configuration_name", name)
        if name not in self.list(document_id):
            raise ConfigurationRefusal("missing_configuration", name)
        self._adapter.activate_configuration(document_id, name)
        active = self._adapter.read_active_configuration(document_id)
        if active != name:
            raise ConfigurationPostconditionError(
                "activation_readback_mismatch", f"expected={name}, actual={active}"
            )
        return active

    def query_state(
        self,
        document_id: str,
        name: str,
        *,
        dimensions: tuple[str, ...] = (),
        properties: tuple[str, ...] = (),
        component_ids: tuple[str, ...] = (),
    ) -> ConfigurationSnapshot:
        self.activate(document_id, name)
        self._validate_keys("dimension", dimensions)
        self._validate_keys("property", properties)
        self._validate_keys("component", component_ids)
        snapshot = ConfigurationSnapshot(
            name=name,
            dimensions=tuple(
                (key, self._adapter.read_dimension(document_id, name, key))
                for key in dimensions
            ),
            properties=tuple(
                (key, self._adapter.read_property(document_id, name, key))
                for key in properties
            ),
            component_states=tuple(
                (key, self._adapter.read_component_state(document_id, name, key))
                for key in component_ids
            ),
        )
        active_after = self._adapter.read_active_configuration(document_id)
        if active_after != name:
            raise ConfigurationPostconditionError(
                "configuration_context_drift",
                f"expected={name}, actual={active_after}",
            )
        return snapshot

    def verify_isolation(
        self,
        document_id: str,
        first_name: str,
        second_name: str,
        *,
        dimensions: tuple[str, ...] = (),
        properties: tuple[str, ...] = (),
        component_ids: tuple[str, ...] = (),
    ) -> tuple[ConfigurationSnapshot, ConfigurationSnapshot]:
        if first_name == second_name:
            raise ConfigurationRefusal("isolation_requires_distinct_configurations")
        first = self.query_state(
            document_id,
            first_name,
            dimensions=dimensions,
            properties=properties,
            component_ids=component_ids,
        )
        second = self.query_state(
            document_id,
            second_name,
            dimensions=dimensions,
            properties=properties,
            component_ids=component_ids,
        )
        first_revisited = self.query_state(
            document_id,
            first_name,
            dimensions=dimensions,
            properties=properties,
            component_ids=component_ids,
        )
        if first_revisited != first:
            raise ConfigurationPostconditionError(
                "configuration_state_leak", first_name
            )
        return first, second

    @staticmethod
    def _require_identity(label: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationRefusal(f"invalid_{label}")

    @staticmethod
    def _validate_keys(label: str, values: tuple[str, ...]) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ConfigurationRefusal(f"invalid_{label}_identity")
        if len(set(values)) != len(values):
            raise ConfigurationRefusal(f"duplicate_{label}_identity")
