"""Configuration, equation, and custom-property domain contract for Agent C."""

from __future__ import annotations

from dataclasses import dataclass
import math
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
class RebuildReport:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class EquationSnapshot:
    identity: str
    expression: str
    value: float | None
    is_global_variable: bool
    disabled: bool = False


@dataclass(frozen=True)
class MaterialSnapshot:
    database: str
    name: str


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

    def read_configuration_parent(
        self, document_id: str, name: str
    ) -> str | None: ...

    def delete_configuration(self, document_id: str, name: str) -> None: ...

    def rename_configuration(
        self, document_id: str, old_name: str, new_name: str
    ) -> None: ...

    def activate_configuration(self, document_id: str, name: str) -> None: ...

    def read_active_configuration(self, document_id: str) -> str: ...

    def read_dimension(
        self, document_id: str, configuration: str, dimension_name: str
    ) -> float | None: ...

    def set_dimension(
        self,
        document_id: str,
        configuration: str,
        dimension_name: str,
        value: float,
    ) -> None: ...

    def read_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
    ) -> str | None: ...

    def set_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
        value: str,
    ) -> None: ...

    def delete_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
    ) -> None: ...

    def read_feature_state(
        self, document_id: str, configuration: str, feature_id: str
    ) -> str | None: ...

    def set_feature_suppressed(
        self,
        document_id: str,
        configuration: str,
        feature_id: str,
        suppressed: bool,
    ) -> None: ...

    def read_component_state(
        self, document_id: str, configuration: str, component_id: str
    ) -> str | None: ...

    def set_component_suppressed(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        suppressed: bool,
    ) -> None: ...

    def read_component_configuration(
        self, document_id: str, configuration: str, component_id: str
    ) -> str | None: ...

    def set_component_configuration(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        referenced_configuration: str,
    ) -> None: ...

    def read_material(
        self, document_id: str, configuration: str
    ) -> MaterialSnapshot | None: ...

    def set_material(
        self,
        document_id: str,
        configuration: str,
        database: str,
        material_name: str,
    ) -> None: ...

    def list_display_states(
        self, document_id: str, configuration: str
    ) -> tuple[str, ...]: ...

    def create_display_state(
        self, document_id: str, configuration: str, name: str
    ) -> None: ...

    def rename_display_state(
        self,
        document_id: str,
        configuration: str,
        old_name: str,
        new_name: str,
    ) -> None: ...

    def delete_display_state(
        self, document_id: str, configuration: str, name: str
    ) -> None: ...

    def list_equations(self, document_id: str) -> tuple[EquationSnapshot, ...]: ...

    def add_equation(self, document_id: str, expression: str) -> str: ...

    def set_equation(
        self, document_id: str, identity: str, expression: str
    ) -> None: ...

    def delete_equation(self, document_id: str, identity: str) -> None: ...

    def rebuild_document(self, document_id: str) -> RebuildReport: ...


class ConfigurationService:
    """Enforces explicit configuration identity and post-mutation read-back."""

    def __init__(self, adapter: ConfigurationAdapter) -> None:
        self._adapter = adapter

    def list(self, document_id: str) -> tuple[str, ...]:
        self._require_identity("document_id", document_id)
        names = tuple(self._adapter.list_configurations(document_id))
        if any(not isinstance(name, str) or not name.strip() for name in names):
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
        self._require_rebuild(document_id)
        if name not in self.list(document_id):
            raise ConfigurationPostconditionError(
                "configuration_create_readback_missing", name
            )
        if parent is not None:
            actual_parent = self._adapter.read_configuration_parent(document_id, name)
            if actual_parent != parent:
                raise ConfigurationPostconditionError(
                    "configuration_parent_readback_mismatch",
                    f"expected={parent}, actual={actual_parent}",
                )

    def delete(self, document_id: str, name: str) -> None:
        self._require_identity("document_id", document_id)
        self._require_identity("configuration_name", name)
        names = self.list(document_id)
        if name not in names:
            raise ConfigurationRefusal("missing_configuration", name)
        if self._adapter.read_active_configuration(document_id) == name:
            raise ConfigurationRefusal("cannot_delete_active_configuration", name)
        if len(names) <= 1:
            raise ConfigurationRefusal("cannot_delete_last_configuration")
        self._adapter.delete_configuration(document_id, name)
        self._require_rebuild(document_id)
        if name in self.list(document_id):
            raise ConfigurationPostconditionError(
                "configuration_delete_readback_present", name
            )

    def rename(self, document_id: str, old_name: str, new_name: str) -> str:
        self._require_identity("document_id", document_id)
        self._require_identity("configuration_name", old_name)
        self._require_identity("configuration_name", new_name)
        names = self.list(document_id)
        if old_name not in names:
            raise ConfigurationRefusal("missing_configuration", old_name)
        if new_name in names:
            raise ConfigurationRefusal("configuration_exists", new_name)
        was_active = self._adapter.read_active_configuration(document_id) == old_name
        self._adapter.rename_configuration(document_id, old_name, new_name)
        self._require_rebuild(document_id)
        after = self.list(document_id)
        if old_name in after or new_name not in after:
            raise ConfigurationPostconditionError(
                "configuration_rename_readback_mismatch",
                f"old={old_name}, new={new_name}",
            )
        if was_active and self._adapter.read_active_configuration(document_id) != new_name:
            raise ConfigurationPostconditionError(
                "configuration_active_rename_readback_mismatch", new_name
            )
        return new_name

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

    def set_dimension(
        self,
        document_id: str,
        configuration: str,
        dimension_name: str,
        value: float,
    ) -> float:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("dimension_identity", dimension_name)
        normalized = self._finite_value(value, "invalid_dimension_value")
        self._adapter.set_dimension(
            document_id, configuration, dimension_name, normalized
        )
        self._require_rebuild(document_id)
        actual = self._adapter.read_dimension(
            document_id, configuration, dimension_name
        )
        if actual is None or not math.isclose(
            float(actual), normalized, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise ConfigurationPostconditionError("dimension_readback_mismatch")
        return float(actual)

    def set_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
        value: str,
    ) -> str:
        self._require_identity("document_id", document_id)
        self._require_identity("property_identity", property_name)
        if configuration is not None:
            self._require_configuration_target(document_id, configuration)
        if not isinstance(value, str):
            raise ConfigurationRefusal("invalid_property_value")
        self._adapter.set_property(document_id, configuration, property_name, value)
        self._require_rebuild(document_id)
        actual = self._adapter.read_property(document_id, configuration, property_name)
        if actual != value:
            raise ConfigurationPostconditionError(
                "property_readback_mismatch", f"expected={value}, actual={actual}"
            )
        return actual

    def delete_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
    ) -> None:
        self._require_identity("document_id", document_id)
        self._require_identity("property_identity", property_name)
        if configuration is not None:
            self._require_configuration_target(document_id, configuration)
        self._adapter.delete_property(document_id, configuration, property_name)
        self._require_rebuild(document_id)
        if self._adapter.read_property(document_id, configuration, property_name) is not None:
            raise ConfigurationPostconditionError(
                "property_delete_readback_present", property_name
            )

    def set_feature_suppressed(
        self,
        document_id: str,
        configuration: str,
        feature_id: str,
        suppressed: bool,
    ) -> str:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("feature_identity", feature_id)
        self._require_bool("feature_suppression", suppressed)
        self._adapter.set_feature_suppressed(
            document_id, configuration, feature_id, suppressed
        )
        self._require_rebuild(document_id)
        actual = self._adapter.read_feature_state(document_id, configuration, feature_id)
        expected = "suppressed" if suppressed else "resolved"
        if actual != expected:
            raise ConfigurationPostconditionError(
                "feature_suppression_readback_mismatch", str(actual)
            )
        return actual

    def set_component_suppressed(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        suppressed: bool,
    ) -> str:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("component_identity", component_id)
        self._require_bool("component_suppression", suppressed)
        self._adapter.set_component_suppressed(
            document_id, configuration, component_id, suppressed
        )
        self._require_rebuild(document_id)
        actual = self._adapter.read_component_state(
            document_id, configuration, component_id
        )
        expected = "suppressed" if suppressed else "resolved"
        if actual != expected:
            raise ConfigurationPostconditionError(
                "component_suppression_readback_mismatch", str(actual)
            )
        return actual

    def set_component_configuration(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        referenced_configuration: str,
    ) -> str:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("component_identity", component_id)
        self._require_identity("referenced_configuration", referenced_configuration)
        self._adapter.set_component_configuration(
            document_id,
            configuration,
            component_id,
            referenced_configuration,
        )
        self._require_rebuild(document_id)
        actual = self._adapter.read_component_configuration(
            document_id, configuration, component_id
        )
        if actual != referenced_configuration:
            raise ConfigurationPostconditionError(
                "component_configuration_readback_mismatch", str(actual)
            )
        return actual

    def set_material(
        self,
        document_id: str,
        configuration: str,
        database: str,
        material_name: str,
    ) -> MaterialSnapshot:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("material_database", database)
        self._require_identity("material_name", material_name)
        self._adapter.set_material(
            document_id, configuration, database, material_name
        )
        self._require_rebuild(document_id)
        actual = self._adapter.read_material(document_id, configuration)
        if (
            actual is None
            or actual.name != material_name
            or self._material_database_key(actual.database)
            != self._material_database_key(database)
        ):
            raise ConfigurationPostconditionError(
                "material_readback_mismatch",
                f"expected_database={database!r}, expected_name={material_name!r}, actual={actual!r}",
            )
        return actual

    def list_display_states(
        self, document_id: str, configuration: str
    ) -> tuple[str, ...]:
        self._require_configuration_target(document_id, configuration)
        states = tuple(self._adapter.list_display_states(document_id, configuration))
        if any(not isinstance(name, str) or not name.strip() for name in states):
            raise ConfigurationPostconditionError("invalid_display_state_identity")
        if len(set(states)) != len(states):
            raise ConfigurationPostconditionError("duplicate_display_state_identity")
        return states

    def create_display_state(
        self, document_id: str, configuration: str, name: str
    ) -> str:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("display_state_name", name)
        if name in self.list_display_states(document_id, configuration):
            raise ConfigurationRefusal("display_state_exists", name)
        self._adapter.create_display_state(document_id, configuration, name)
        if name not in self.list_display_states(document_id, configuration):
            raise ConfigurationPostconditionError(
                "display_state_create_readback_missing", name
            )
        return name

    def rename_display_state(
        self,
        document_id: str,
        configuration: str,
        old_name: str,
        new_name: str,
    ) -> str:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("display_state_name", old_name)
        self._require_identity("display_state_name", new_name)
        states = self.list_display_states(document_id, configuration)
        if old_name not in states:
            raise ConfigurationRefusal("missing_display_state", old_name)
        if new_name in states:
            raise ConfigurationRefusal("display_state_exists", new_name)
        self._adapter.rename_display_state(
            document_id, configuration, old_name, new_name
        )
        after = self.list_display_states(document_id, configuration)
        if old_name in after or new_name not in after:
            raise ConfigurationPostconditionError(
                "display_state_rename_readback_mismatch",
                f"old={old_name}, new={new_name}",
            )
        return new_name

    def delete_display_state(
        self, document_id: str, configuration: str, name: str
    ) -> None:
        self._require_configuration_target(document_id, configuration)
        self._require_identity("display_state_name", name)
        states = self.list_display_states(document_id, configuration)
        if name not in states:
            raise ConfigurationRefusal("missing_display_state", name)
        if len(states) <= 1:
            raise ConfigurationRefusal("cannot_delete_last_display_state")
        self._adapter.delete_display_state(document_id, configuration, name)
        if name in self.list_display_states(document_id, configuration):
            raise ConfigurationPostconditionError(
                "display_state_delete_readback_present", name
            )

    def list_equations(self, document_id: str) -> tuple[EquationSnapshot, ...]:
        self._require_identity("document_id", document_id)
        equations = tuple(self._adapter.list_equations(document_id))
        identities: list[str] = []
        for equation in equations:
            self._require_identity("equation_identity", equation.identity)
            if not isinstance(equation.expression, str) or "=" not in equation.expression:
                raise ConfigurationPostconditionError(
                    "invalid_equation_expression", equation.identity
                )
            identities.append(equation.identity)
        if len(set(identities)) != len(identities):
            raise ConfigurationPostconditionError("duplicate_equation_identity")
        return equations

    def add_equation(self, document_id: str, expression: str) -> EquationSnapshot:
        self._require_identity("document_id", document_id)
        identity = self._equation_identity(expression)
        if identity in {item.identity for item in self.list_equations(document_id)}:
            raise ConfigurationRefusal("equation_exists", identity)
        returned_identity = self._adapter.add_equation(document_id, expression)
        if returned_identity != identity:
            raise ConfigurationPostconditionError(
                "equation_identity_readback_mismatch", str(returned_identity)
            )
        self._require_rebuild(document_id)
        return self._require_equation(document_id, identity, expression)

    def set_equation(
        self, document_id: str, identity: str, expression: str
    ) -> EquationSnapshot:
        self._require_identity("document_id", document_id)
        self._require_identity("equation_identity", identity)
        expression_identity = self._equation_identity(expression)
        if expression_identity != identity:
            raise ConfigurationRefusal(
                "equation_identity_change_not_allowed",
                f"expected={identity}, actual={expression_identity}",
            )
        self._require_equation(document_id, identity)
        self._adapter.set_equation(document_id, identity, expression)
        self._require_rebuild(document_id)
        return self._require_equation(document_id, identity, expression)

    def delete_equation(self, document_id: str, identity: str) -> None:
        self._require_identity("document_id", document_id)
        self._require_identity("equation_identity", identity)
        self._require_equation(document_id, identity)
        self._adapter.delete_equation(document_id, identity)
        self._require_rebuild(document_id)
        if identity in {item.identity for item in self.list_equations(document_id)}:
            raise ConfigurationPostconditionError(
                "equation_delete_readback_present", identity
            )

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

    def _require_configuration_target(self, document_id: str, configuration: str) -> None:
        self._require_identity("document_id", document_id)
        self._require_identity("configuration_name", configuration)
        if configuration not in self.list(document_id):
            raise ConfigurationRefusal("missing_configuration", configuration)

    def _require_rebuild(self, document_id: str) -> None:
        report = self._adapter.rebuild_document(document_id)
        if not report.ok or report.errors:
            raise ConfigurationPostconditionError(
                "rebuild_failed", "; ".join(report.errors)
            )

    def _require_equation(
        self,
        document_id: str,
        identity: str,
        expected_expression: str | None = None,
    ) -> EquationSnapshot:
        match = next(
            (item for item in self.list_equations(document_id) if item.identity == identity),
            None,
        )
        if match is None:
            raise ConfigurationRefusal("missing_equation", identity)
        if expected_expression is not None:
            actual_canonical = self._canonical_equation_expression(match.expression)
            expected_canonical = self._canonical_equation_expression(expected_expression)
            if actual_canonical != expected_canonical:
                raise ConfigurationPostconditionError(
                    "equation_expression_readback_mismatch", match.expression
                )
        return match

    @staticmethod
    def _canonical_equation_expression(expression: str) -> str:
        if not isinstance(expression, str) or "=" not in expression:
            raise ConfigurationRefusal("invalid_equation_expression")
        left, right = expression.split("=", 1)
        return f"{left.strip()} = {right.strip()}"

    @staticmethod
    def _equation_identity(expression: str) -> str:
        if not isinstance(expression, str) or "=" not in expression:
            raise ConfigurationRefusal("invalid_equation_expression")
        identity = expression.split("=", 1)[0].strip()
        if not identity:
            raise ConfigurationRefusal("invalid_equation_identity")
        return identity

    @staticmethod
    def _material_database_key(value: str) -> str:
        normalized = str(value).strip().replace("\\", "/").rsplit("/", 1)[-1]
        if normalized.casefold().endswith(".sldmat"):
            normalized = normalized[:-7]
        return " ".join(normalized.split()).casefold()

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

    @staticmethod
    def _finite_value(value: float, reason: str) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationRefusal(reason) from exc
        if not math.isfinite(normalized):
            raise ConfigurationRefusal(reason)
        return normalized

    @staticmethod
    def _require_bool(label: str, value: bool) -> None:
        if not isinstance(value, bool):
            raise ConfigurationRefusal(f"invalid_{label}")
