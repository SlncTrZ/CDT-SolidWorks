"""Lane-local native SOLIDWORKS configuration/equation/property adapter.

All COM work is dispatched through the shared ``SolidWorksSession``. Configuration
identity is explicit; when SOLIDWORKS exposes a current-configuration-only setter,
the adapter activates the requested configuration for the bounded operation and
restores the previous configuration before returning.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document

from .domain import (
    ConfigurationPostconditionError,
    ConfigurationRefusal,
    EquationSnapshot,
    RebuildReport,
)


T = TypeVar("T")


class _ConfigurationNativeError(NativeRuntimeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(
            code,
            "configuration_native",
            code if detail is None else f"{code}: {detail}",
        )
_SW_DOC_PART = 1
_SW_DOC_ASSEMBLY = 2
_SW_SPECIFY_CONFIGURATION = 3
_SW_SET_VALUE_SUCCESS = 0
_SW_SUPPRESS_FEATURE = 0
_SW_UNSUPPRESS_FEATURE = 1
_SW_COMPONENT_SUPPRESSED = 0
_SW_COMPONENT_RESOLVED = 3
_SW_CUSTOM_INFO_TEXT = 30
_SW_CUSTOM_PROPERTY_REPLACE_VALUE = 2


class ConfigurationNativeAdapter:
    """Bounded COM implementation of :class:`ConfigurationAdapter`."""

    def __init__(
        self,
        session: Any,
        *,
        timeout: float = 20.0,
        max_features: int = 100_000,
    ) -> None:
        self.session = session
        self.api = session.api
        self.timeout = float(timeout)
        self.max_features = int(max_features)

    def list_configurations(self, document_id: str) -> tuple[str, ...]:
        return self._run(
            "configuration_list",
            False,
            lambda app: self._configuration_names(self._document(app, document_id)),
        )

    def create_configuration(
        self, document_id: str, name: str, parent: str | None
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            previous = self._active_configuration(model)
            manager = self.api._member(model, "ConfigurationManager")
            created = self.api._member(
                manager,
                "AddConfiguration2",
                name,
                "",
                "",
                0,
                parent or "",
                "",
                True,
            )
            if created is None:
                raise _ConfigurationNativeError("configuration_create_failed", name)
            if self._active_configuration(model) != previous:
                if not bool(self.api._member(model, "ShowConfiguration2", previous)):
                    raise _ConfigurationNativeError(
                        "configuration_restore_failed", previous
                    )
                if self._active_configuration(model) != previous:
                    raise _ConfigurationNativeError(
                        "configuration_restore_readback_mismatch", previous
                    )

        self._run("configuration_create", True, operation)

    def delete_configuration(self, document_id: str, name: str) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            if not bool(self.api._member(model, "DeleteConfiguration2", name)):
                raise _ConfigurationNativeError("configuration_delete_failed", name)

        self._run("configuration_delete", True, operation)

    def rename_configuration(
        self, document_id: str, old_name: str, new_name: str
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            configuration = self.api._member(model, "GetConfigurationByName", old_name)
            if configuration is None:
                raise _ConfigurationNativeError("missing_configuration", old_name)
            configuration.Name = new_name

        self._run("configuration_rename", True, operation)

    def activate_configuration(self, document_id: str, name: str) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            if self._active_configuration(model) == name:
                return
            if not bool(self.api._member(model, "ShowConfiguration2", name)):
                raise _ConfigurationNativeError("configuration_activate_failed", name)

        self._run("configuration_activate", True, operation)

    def read_active_configuration(self, document_id: str) -> str:
        return self._run(
            "configuration_active_read",
            False,
            lambda app: self._active_configuration(self._document(app, document_id)),
        )

    def read_dimension(
        self, document_id: str, configuration: str, dimension_name: str
    ) -> float | None:
        def operation(app: Any) -> float | None:
            model = self._document(app, document_id)
            dimension = self.api._member(model, "Parameter", dimension_name)
            if dimension is None:
                return None
            values = self.api._member(
                dimension,
                "GetSystemValue3",
                _SW_SPECIFY_CONFIGURATION,
                self.api.string_array((configuration,)),
            )
            if values is None:
                return None
            if isinstance(values, (tuple, list)):
                return float(values[0]) if values else None
            return float(values)

        return self._run("configuration_dimension_read", False, operation)

    def set_dimension(
        self,
        document_id: str,
        configuration: str,
        dimension_name: str,
        value: float,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            dimension = self.api._member(model, "Parameter", dimension_name)
            if dimension is None:
                raise _ConfigurationNativeError("missing_dimension", dimension_name)
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
                raise _ConfigurationNativeError(
                    "dimension_set_failed", f"native_status={status}"
                )

        self._run("configuration_dimension_set", True, operation)

    def read_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
    ) -> str | None:
        def operation(app: Any) -> str | None:
            model = self._document(app, document_id)
            manager = self._custom_property_manager(model, configuration)
            names = self.api._member(manager, "GetNames")
            if names is None:
                return None
            if isinstance(names, str):
                names = (names,)
            if property_name not in tuple(str(name) for name in names):
                return None
            # ``Get`` remains available in SOLIDWORKS 2024 and avoids unsafe
            # caller-owned byref buffers in this lane-local late-bound adapter.
            value = self.api._member(manager, "Get", property_name)
            return None if value is None else str(value)

        return self._run("configuration_property_read", False, operation)

    def set_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
        value: str,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            manager = self._custom_property_manager(model, configuration)
            status = int(
                self.api._member(
                    manager,
                    "Add3",
                    property_name,
                    _SW_CUSTOM_INFO_TEXT,
                    value,
                    _SW_CUSTOM_PROPERTY_REPLACE_VALUE,
                )
            )
            if status != 0:
                raise _ConfigurationNativeError(
                    "custom_property_set_failed", f"native_status={status}"
                )

        self._run("configuration_property_set", True, operation)

    def delete_property(
        self,
        document_id: str,
        configuration: str | None,
        property_name: str,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            manager = self._custom_property_manager(model, configuration)
            status = int(self.api._member(manager, "Delete2", property_name))
            if status != 0:
                raise _ConfigurationNativeError(
                    "custom_property_delete_failed", f"native_status={status}"
                )

        self._run("configuration_property_delete", True, operation)

    def read_feature_state(
        self, document_id: str, configuration: str, feature_id: str
    ) -> str | None:
        def operation(app: Any) -> str | None:
            model = self._document(app, document_id)
            feature = self._feature(model, feature_id, required=False)
            if feature is None:
                return None
            value = self.api._member(
                feature,
                "IsSuppressed2",
                _SW_SPECIFY_CONFIGURATION,
                self.api.string_array((configuration,)),
            )
            suppressed = self._single_bool(value)
            return "suppressed" if suppressed else "resolved"

        return self._run("configuration_feature_state_read", False, operation)

    def set_feature_suppressed(
        self,
        document_id: str,
        configuration: str,
        feature_id: str,
        suppressed: bool,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            feature = self._feature(model, feature_id, required=True)
            result = self.api._member(
                feature,
                "SetSuppression2",
                _SW_SUPPRESS_FEATURE if suppressed else _SW_UNSUPPRESS_FEATURE,
                _SW_SPECIFY_CONFIGURATION,
                self.api.string_array((configuration,)),
            )
            if result is False:
                raise _ConfigurationNativeError("feature_suppression_failed", feature_id)

        self._run("configuration_feature_suppression", True, operation)

    def read_component_state(
        self, document_id: str, configuration: str, component_id: str
    ) -> str | None:
        def operation(app: Any) -> str | None:
            model = self._assembly(app, document_id)
            config = self._configuration(model, configuration)
            try:
                state = int(
                    self.api._member(
                        config, "GetComponentSuppressionState", component_id
                    )
                )
            except Exception:
                return None
            return "suppressed" if state == _SW_COMPONENT_SUPPRESSED else "resolved"

        return self._run("configuration_component_state_read", False, operation)

    def set_component_suppressed(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        suppressed: bool,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._assembly(app, document_id)
            self._with_configuration(
                model,
                configuration,
                lambda: self._set_active_component_suppressed(
                    model, component_id, suppressed
                ),
            )

        self._run("configuration_component_suppression", True, operation)

    def read_component_configuration(
        self, document_id: str, configuration: str, component_id: str
    ) -> str | None:
        def operation(app: Any) -> str | None:
            model = self._assembly(app, document_id)
            config = self._configuration(model, configuration)
            value = self.api._member(config, "GetComponentConfigName", component_id)
            return str(value) if value else None

        return self._run("configuration_component_config_read", False, operation)

    def set_component_configuration(
        self,
        document_id: str,
        configuration: str,
        component_id: str,
        referenced_configuration: str,
    ) -> None:
        def operation(app: Any) -> None:
            model = self._assembly(app, document_id)

            def setter() -> None:
                component = self._component(model, component_id)
                component.ReferencedConfiguration = referenced_configuration

            self._with_configuration(model, configuration, setter)

        self._run("configuration_component_config_set", True, operation)

    def list_equations(self, document_id: str) -> tuple[EquationSnapshot, ...]:
        def operation(app: Any) -> tuple[EquationSnapshot, ...]:
            model = self._document(app, document_id)
            manager = self.api._member(model, "GetEquationMgr")
            if manager is None:
                return ()
            result: list[EquationSnapshot] = []
            count = int(self.api._member(manager, "GetCount"))
            for index in range(count):
                expression = self._canonical_equation_expression(
                    str(self.api._member(manager, "Equation", index))
                )
                result.append(
                    EquationSnapshot(
                        identity=self._equation_identity(expression),
                        expression=expression,
                        value=self._optional_float_member(manager, "Value", index),
                        is_global_variable=bool(
                            self.api._member(manager, "GlobalVariable", index)
                        ),
                        disabled=bool(self.api._member(manager, "Disabled", index)),
                    )
                )
            return tuple(result)

        return self._run("configuration_equation_list", False, operation)

    def add_equation(self, document_id: str, expression: str) -> str:
        identity = self._equation_identity(expression)

        def operation(app: Any) -> str:
            model = self._document(app, document_id)
            manager = self.api._member(model, "GetEquationMgr")
            if manager is None:
                raise _ConfigurationNativeError("equation_manager_unavailable")
            index = int(self.api._member(manager, "Add2", -1, expression, True))
            if index < 0:
                raise _ConfigurationNativeError("equation_add_failed", identity)
            self._evaluate_equations(manager)
            return identity

        return self._run("configuration_equation_add", True, operation)

    def set_equation(
        self, document_id: str, identity: str, expression: str
    ) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            manager = self.api._member(model, "GetEquationMgr")
            if manager is None:
                raise _ConfigurationNativeError("equation_manager_unavailable")
            index = self._equation_index(manager, identity)
            delete_status = self.api._member(manager, "Delete", index)
            if isinstance(delete_status, bool):
                delete_ok = delete_status
            else:
                delete_ok = int(delete_status) == 0
            if not delete_ok:
                raise _ConfigurationNativeError("equation_edit_delete_failed", identity)
            inserted = int(self.api._member(manager, "Add2", index, expression, True))
            if inserted != index:
                raise _ConfigurationNativeError(
                    "equation_edit_insert_failed", f"native_index={inserted}"
                )
            self._evaluate_equations(manager)

        self._run("configuration_equation_set", True, operation)

    def delete_equation(self, document_id: str, identity: str) -> None:
        def operation(app: Any) -> None:
            model = self._document(app, document_id)
            manager = self.api._member(model, "GetEquationMgr")
            if manager is None:
                raise _ConfigurationNativeError("equation_manager_unavailable")
            index = self._equation_index(manager, identity)
            status = self.api._member(manager, "Delete", index)
            if isinstance(status, bool):
                ok = status
            else:
                ok = int(status) == 0
            if not ok:
                raise _ConfigurationNativeError("equation_delete_failed", identity)
            self._evaluate_equations(manager)

        self._run("configuration_equation_delete", True, operation)

    def rebuild_document(self, document_id: str) -> RebuildReport:
        def operation(app: Any) -> RebuildReport:
            result = rebuild_document(
                self._document(app, document_id),
                self.api,
                max_features=self.max_features,
            )
            errors = tuple(
                f"{issue.feature_name}:{issue.error_code}"
                for issue in result.feature_issues
                if not issue.is_warning
            )
            return RebuildReport(ok=result.success, errors=errors)

        return self._run("configuration_rebuild", True, operation)

    def _document(self, app: Any, document_id: str) -> Any:
        model = self.api.get_open_document(app, document_id)
        if model is None:
            raise _ConfigurationNativeError("document_not_open", document_id)
        doc_type = int(self.api.document_type(model)) if hasattr(self.api, "document_type") else int(self.api._member(model, "GetType"))
        if doc_type not in (_SW_DOC_PART, _SW_DOC_ASSEMBLY):
            raise _ConfigurationNativeError("unsupported_document_type", str(doc_type))
        return model

    def _assembly(self, app: Any, document_id: str) -> Any:
        model = self._document(app, document_id)
        doc_type = int(self.api.document_type(model)) if hasattr(self.api, "document_type") else int(self.api._member(model, "GetType"))
        if doc_type != _SW_DOC_ASSEMBLY:
            raise _ConfigurationNativeError("document_not_assembly", document_id)
        return model

    def _configuration_names(self, model: Any) -> tuple[str, ...]:
        value = self.api._member(model, "GetConfigurationNames")
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(str(name) for name in value)

    def _configuration(self, model: Any, name: str) -> Any:
        value = self.api._member(model, "GetConfigurationByName", name)
        if value is None:
            raise _ConfigurationNativeError("missing_configuration", name)
        return value

    def _active_configuration(self, model: Any) -> str:
        manager = self.api._member(model, "ConfigurationManager")
        active = self.api._member(manager, "ActiveConfiguration")
        if active is None:
            raise _ConfigurationNativeError("active_configuration_missing")
        return str(self.api._member(active, "Name"))

    def _with_configuration(
        self, model: Any, configuration: str, operation: Callable[[], None]
    ) -> None:
        previous = self._active_configuration(model)
        if previous != configuration:
            if not bool(self.api._member(model, "ShowConfiguration2", configuration)):
                raise _ConfigurationNativeError(
                    "configuration_activate_failed", configuration
                )
        try:
            operation()
        finally:
            if previous != configuration:
                if not bool(self.api._member(model, "ShowConfiguration2", previous)):
                    raise _ConfigurationNativeError(
                        "configuration_restore_failed", previous
                    )

    def _set_active_component_suppressed(
        self, model: Any, component_id: str, suppressed: bool
    ) -> None:
        component = self._component(model, component_id)
        status = int(
            self.api._member(
                component,
                "SetSuppression2",
                _SW_COMPONENT_SUPPRESSED if suppressed else _SW_COMPONENT_RESOLVED,
            )
        )
        if status != 2:
            raise _ConfigurationNativeError(
                "component_suppression_failed", f"native_status={status}"
            )

    def _component(self, model: Any, component_id: str) -> Any:
        components = self.api.components(model, False)
        matches = tuple(
            component
            for component in components
            if self.api.component_name(component) == component_id
        )
        if not matches:
            raise _ConfigurationNativeError("missing_component", component_id)
        if len(matches) > 1:
            raise _ConfigurationNativeError("ambiguous_component_identity", component_id)
        return matches[0]

    def _feature(self, model: Any, name: str, *, required: bool) -> Any | None:
        try:
            feature = self.api._member(model, "FeatureByName", name)
        except Exception:
            feature = None
        if feature is None:
            current = self.api.first_feature(model)
            visited = 0
            while current is not None:
                visited += 1
                if visited > self.max_features:
                    raise _ConfigurationNativeError("feature_traversal_limit")
                if self.api.feature_name(current) == name:
                    feature = current
                    break
                current = self.api.next_feature(current)
        if feature is None and required:
            raise _ConfigurationNativeError("missing_feature", name)
        return feature

    def _custom_property_manager(
        self, model: Any, configuration: str | None
    ) -> Any:
        if configuration is not None:
            config = self._configuration(model, configuration)
            manager = self.api._member(config, "CustomPropertyManager")
        else:
            extension = self.api._member(model, "Extension")
            manager = self.api._member(extension, "CustomPropertyManager", "")
        if manager is None:
            raise _ConfigurationNativeError("custom_property_manager_unavailable")
        return manager

    def _equation_index(self, manager: Any, identity: str) -> int:
        count = int(self.api._member(manager, "GetCount"))
        matches = tuple(
            index
            for index in range(count)
            if self._equation_identity(
                str(self.api._member(manager, "Equation", index))
            )
            == identity
        )
        if not matches:
            raise _ConfigurationNativeError("missing_equation", identity)
        if len(matches) > 1:
            raise _ConfigurationNativeError("ambiguous_equation_identity", identity)
        return matches[0]

    def _evaluate_equations(self, manager: Any) -> None:
        # SOLIDWORKS documents EvaluateAll as returning -1 for both success and
        # failure, so the integer is not a usable success signal. Dispatch errors
        # still propagate; correctness is verified by equation read-back and the
        # service-level rebuild gate after the mutation.
        self.api._member(manager, "EvaluateAll")

    def _optional_float_member(self, obj: Any, name: str, index: int) -> float | None:
        try:
            value = self.api._member(obj, name, index)
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _canonical_equation_expression(expression: str) -> str:
        if "=" not in expression:
            return expression.strip()
        left, right = expression.split("=", 1)
        return f"{left.strip()} = {right.strip()}"

    @staticmethod
    def _equation_identity(expression: str) -> str:
        if "=" not in expression:
            raise ConfigurationPostconditionError(
                "invalid_native_equation_expression", expression
            )
        identity = expression.split("=", 1)[0].strip()
        if not identity:
            raise ConfigurationPostconditionError("invalid_native_equation_identity")
        return identity

    @staticmethod
    def _single_bool(value: Any) -> bool:
        if isinstance(value, (tuple, list)):
            return bool(value[0]) if value else False
        return bool(value)

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
            raise ConfigurationPostconditionError(
                "native_state_uncertain", f"call_id={result.call_id}; {detail}"
            )
        code = failure.code if failure is not None else result.state.value
        raise ConfigurationRefusal(code, detail)
