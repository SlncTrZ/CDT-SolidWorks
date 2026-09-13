"""Lane-local native SOLIDWORKS assembly adapter.

This module reuses the shared serialized ``SolidWorksSession`` and rebuild verifier.
It never owns COM initialization, dispatcher state, application lifetime, or MCP
registration; those remain integration-owned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document

from .domain import (
    AssemblyPostconditionError,
    AssemblyRefusal,
    ComponentLoadState,
    ComponentSnapshot,
    MateKind,
    MateRequest,
    MateSnapshot,
    MateState,
    RebuildReport,
    _MATE_ALIGNMENT_KINDS,
)


T = TypeVar("T")


class _AssemblyNativeError(NativeRuntimeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(
            code,
            "assembly_native",
            code if detail is None else f"{code}: {detail}",
        )
_SW_DOC_PART = 1
_SW_DOC_ASSEMBLY = 2
_SW_COMPONENT_SUPPRESSED = 0
_SW_COMPONENT_LIGHTWEIGHT = 1
_SW_COMPONENT_FULLY_RESOLVED = 2
_SW_COMPONENT_RESOLVED = 3
_SW_COMPONENT_FULLY_LIGHTWEIGHT = 4
_SW_SUPPRESSION_CHANGE_OK = 2
_SW_THIS_CONFIGURATION = 1
_SW_SUPPRESS_FEATURE = 0
_SW_UNSUPPRESS_FEATURE = 1

_STANDARD_PLANE_INDEX = {"front": 0, "top": 1, "right": 2}
_MATE_TYPE = {
    MateKind.COINCIDENT: 0,
    MateKind.CONCENTRIC: 1,
    MateKind.PERPENDICULAR: 2,
    MateKind.PARALLEL: 3,
    MateKind.TANGENT: 4,
    MateKind.DISTANCE: 5,
    MateKind.ANGLE: 6,
    MateKind.WIDTH: 11,
    MateKind.LOCK: 16,
    MateKind.SLOT: 21,
}
_MATE_ALIGNMENT = {"aligned": 0, "anti_aligned": 1, "closest": 2}
_MATE_KIND_BY_NATIVE = {value: key for key, value in _MATE_TYPE.items()}


class AssemblyNativeAdapter:
    """Bounded COM implementation of :class:`AssemblyAdapter`."""

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

    def list_components(
        self, assembly_id: str, recursive: bool
    ) -> tuple[ComponentSnapshot, ...]:
        return self._run(
            "assembly_component_list",
            False,
            lambda app: tuple(
                self._component_snapshot(component)
                for component in self.api.components(
                    self._assembly(app, assembly_id), not recursive
                )
            ),
        )

    def read_component(self, assembly_id: str, component_id: str) -> ComponentSnapshot:
        return self._run(
            "assembly_component_read",
            False,
            lambda app: self._component_snapshot(
                self._component(self._assembly(app, assembly_id), component_id)
            ),
        )

    def insert_component(
        self, assembly_id: str, source_path: str, configuration: str | None
    ) -> str:
        source = str(source_path)

        def operation(app: Any) -> str:
            assembly = self._assembly(app, assembly_id)
            source_doc, opened_here = self._ensure_component_document(app, source, configuration)
            try:
                component = self.api._member(
                    assembly,
                    "AddComponent5",
                    source,
                    0,
                    configuration or "",
                    False,
                    "",
                    0.0,
                    0.0,
                    0.0,
                )
                if component is None:
                    raise _AssemblyNativeError("component_insert_failed", source)
                return self.api.component_name(component)
            finally:
                if opened_here and source_doc is not None:
                    try:
                        self.api.close_document(app, self.api.document_title(source_doc))
                    except Exception:
                        pass

        return self._run("assembly_component_insert", True, operation)

    def replace_component(
        self,
        assembly_id: str,
        component_id: str,
        source_path: str,
        configuration: str | None,
    ) -> None:
        source = str(source_path)

        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            self._select_component(assembly, component)
            try:
                ok = bool(
                    self.api._member(
                        assembly,
                        "ReplaceComponents2",
                        source,
                        configuration or "",
                        False,
                        0,
                        True,
                    )
                )
            finally:
                self.api._member(assembly, "ClearSelection2", True)
            if not ok:
                raise _AssemblyNativeError("component_replace_failed", component_id)

        self._run("assembly_component_replace", True, operation)

    def delete_component(self, assembly_id: str, component_id: str) -> None:
        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            self._select_component(assembly, component)
            try:
                result = self.api._member(assembly, "DeleteSelections")
            finally:
                self.api._member(assembly, "ClearSelection2", True)
            if result is False:
                raise _AssemblyNativeError("component_delete_failed", component_id)

        self._run("assembly_component_delete", True, operation)

    def set_component_transform(
        self, assembly_id: str, component_id: str, transform: tuple[float, ...]
    ) -> None:
        values = tuple(float(value) for value in transform)

        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            math_utility = self.api._member(app, "GetMathUtility")
            math_transform = self.api._member(math_utility, "CreateTransform", values)
            if math_transform is None:
                raise _AssemblyNativeError("component_transform_create_failed")
            component.Transform2 = math_transform
            try:
                self.api._member(assembly, "UpdateBox")
            except Exception:
                pass

        self._run("assembly_component_transform", True, operation)

    def set_component_load_state(
        self, assembly_id: str, component_id: str, state: ComponentLoadState
    ) -> None:
        native_state = self._native_suppression(state)

        def operation(app: Any) -> None:
            component = self._component(self._assembly(app, assembly_id), component_id)
            status = int(self.api._member(component, "SetSuppression2", native_state))
            if status != _SW_SUPPRESSION_CHANGE_OK:
                raise _AssemblyNativeError(
                    "component_suppression_failed", f"native_status={status}"
                )

        self._run("assembly_component_state", True, operation)

    def set_component_fixed(
        self, assembly_id: str, component_id: str, fixed: bool
    ) -> None:
        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            self._select_component(assembly, component)
            try:
                self.api._member(
                    assembly, "FixComponent" if fixed else "UnfixComponent"
                )
            finally:
                self.api._member(assembly, "ClearSelection2", True)

        self._run("assembly_component_fixed", True, operation)

    def set_component_configuration(
        self, assembly_id: str, component_id: str, configuration: str
    ) -> None:
        def operation(app: Any) -> None:
            component = self._component(self._assembly(app, assembly_id), component_id)
            component.ReferencedConfiguration = configuration

        self._run("assembly_component_configuration", True, operation)

    def add_mate(self, assembly_id: str, request: MateRequest) -> str:
        kind = request.kind if isinstance(request.kind, MateKind) else MateKind(request.kind)

        def operation(app: Any) -> str:
            assembly = self._assembly(app, assembly_id)
            entities = tuple(
                self._resolve_selection_reference(assembly, selection_ref)
                for selection_ref in request.selection_refs
            )
            mate_data = self.api._member(
                assembly, "CreateMateData", self._mate_type(kind)
            )
            if mate_data is None:
                raise _AssemblyNativeError("mate_data_create_failed", kind.value)
            mate_data.EntitiesToMate = self.api.dispatch_array(entities)
            if request.alignment is not None:
                if kind not in _MATE_ALIGNMENT_KINDS:
                    raise _AssemblyNativeError(
                        "mate_alignment_not_supported", kind.value
                    )
                mate_data.MateAlignment = _MATE_ALIGNMENT[request.alignment]
            if kind is MateKind.DISTANCE and request.value is not None:
                mate_data.Distance = float(request.value)
            if kind is MateKind.ANGLE and request.value is not None:
                mate_data.Angle = float(request.value)
            feature = self.api._member(assembly, "CreateMate", mate_data)
            if feature is None:
                raise _AssemblyNativeError("mate_create_failed", kind.value)
            return self.api.feature_name(feature)

        return self._run("assembly_mate_add", True, operation)

    def list_mates(self, assembly_id: str) -> tuple[MateSnapshot, ...]:
        return self._run(
            "assembly_mate_list",
            False,
            lambda app: tuple(
                self._mate_snapshot(feature)
                for feature in self._mate_features(self._assembly(app, assembly_id))
            ),
        )

    def read_mate(self, assembly_id: str, mate_id: str) -> MateSnapshot:
        return self._run(
            "assembly_mate_read",
            False,
            lambda app: self._mate_snapshot(
                self._mate_feature(self._assembly(app, assembly_id), mate_id)
            ),
        )

    def set_mate_suppressed(
        self, assembly_id: str, mate_id: str, suppressed: bool
    ) -> None:
        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            feature = self._mate_feature(assembly, mate_id)
            action = _SW_SUPPRESS_FEATURE if suppressed else _SW_UNSUPPRESS_FEATURE
            result = self.api._member(
                feature, "SetSuppression2", action, _SW_THIS_CONFIGURATION, None
            )
            if result is False:
                raise _AssemblyNativeError("mate_suppression_failed", mate_id)

        self._run("assembly_mate_suppression", True, operation)

    def set_mate_value(self, assembly_id: str, mate_id: str, value: float) -> None:
        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            feature = self._mate_feature(assembly, mate_id)
            definition = self.api._member(feature, "GetDefinition")
            if definition is None:
                raise _AssemblyNativeError("mate_definition_missing", mate_id)
            mate = self.api._member(feature, "GetSpecificFeature2")
            kind = self._kind_from_native(int(self.api._member(mate, "Type")))
            if kind is MateKind.DISTANCE:
                definition.Distance = float(value)
            elif kind is MateKind.ANGLE:
                definition.Angle = float(value)
            else:
                raise _AssemblyNativeError("mate_value_not_supported", mate_id)
            ok = bool(
                self.api._member(
                    feature,
                    "ModifyDefinition",
                    definition,
                    assembly,
                    self.api.null_dispatch(),
                )
            )
            if not ok:
                raise _AssemblyNativeError("mate_value_edit_failed", mate_id)

        self._run("assembly_mate_value", True, operation)

    def rebuild_assembly(self, assembly_id: str) -> RebuildReport:
        def operation(app: Any) -> RebuildReport:
            result = rebuild_document(
                self._assembly(app, assembly_id),
                self.api,
                max_features=self.max_features,
            )
            errors = tuple(
                f"{issue.feature_name}:{issue.error_code}"
                for issue in result.feature_issues
                if not issue.is_warning
            )
            return RebuildReport(ok=result.success, errors=errors)

        return self._run("assembly_rebuild", True, operation)

    def _assembly(self, app: Any, assembly_id: str) -> Any:
        model = self.api.get_open_document(app, assembly_id)
        if model is None:
            raise _AssemblyNativeError("assembly_not_open", assembly_id)
        if int(self.api.document_type(model)) != _SW_DOC_ASSEMBLY:
            raise _AssemblyNativeError("document_not_assembly", assembly_id)
        return model

    def _component(self, assembly: Any, component_id: str) -> Any:
        matches = tuple(
            component
            for component in self.api.components(assembly, False)
            if self.api.component_name(component) == component_id
        )
        if not matches:
            raise _AssemblyNativeError("missing_component", component_id)
        if len(matches) > 1:
            raise _AssemblyNativeError("ambiguous_component_identity", component_id)
        return matches[0]

    def _component_snapshot(self, component: Any) -> ComponentSnapshot:
        transform = self.api._member(component, "Transform2")
        values = self.api._member(transform, "ArrayData") if transform is not None else ()
        if values is None:
            values = ()
        try:
            fixed = bool(self.api._member(component, "IsFixed"))
        except Exception:
            fixed = False
        try:
            configuration = str(self.api._member(component, "ReferencedConfiguration") or "") or None
        except Exception:
            configuration = None
        return ComponentSnapshot(
            identity=self.api.component_name(component),
            source_path=self.api.component_path(component),
            configuration=configuration,
            transform=tuple(float(value) for value in values),
            load_state=self._domain_suppression(
                int(self.api._member(component, "GetSuppression"))
            ),
            fixed=fixed,
        )

    def _select_component(self, assembly: Any, component: Any) -> None:
        self.api._member(assembly, "ClearSelection2", True)
        try:
            selection_manager = self.api._member(assembly, "SelectionManager")
            select_data = self.api._member(selection_manager, "CreateSelectData")
            ok = bool(
                self.api._member(component, "Select4", False, select_data, False)
            )
        except Exception as exc:
            raise _AssemblyNativeError(
                "component_selection_failed", self.api.component_name(component)
            ) from exc
        if not ok:
            raise _AssemblyNativeError(
                "component_selection_failed", self.api.component_name(component)
            )

    def _ensure_component_document(
        self, app: Any, source_path: str, configuration: str | None
    ) -> tuple[Any | None, bool]:
        existing = self.api.get_open_document(app, source_path)
        if existing is not None:
            return existing, False
        suffix = Path(source_path).suffix.lower()
        if suffix == ".sldprt":
            doc_type = _SW_DOC_PART
        elif suffix == ".sldasm":
            doc_type = _SW_DOC_ASSEMBLY
        else:
            raise _AssemblyNativeError("unsupported_component_document_type", suffix)
        model, errors, _warnings = self.api.open_document(
            app,
            source_path,
            doc_type,
            read_only=True,
            silent=True,
            configuration=configuration or "",
        )
        if model is None or int(errors) != 0:
            raise _AssemblyNativeError(
                "component_source_open_failed", f"errors={int(errors)}"
            )
        return model, True

    def _resolve_selection_reference(self, assembly: Any, reference: str) -> Any:
        component_id, plane = self._parse_selection_ref(reference)
        if component_id is None:
            feature = self._standard_plane_feature(assembly, plane)
            return self.api._member(feature, "GetSpecificFeature2")
        component = self._component(assembly, component_id)
        model = self.api._member(component, "GetModelDoc2")
        if model is None:
            raise _AssemblyNativeError("component_model_not_resolved", component_id)
        feature = self._standard_plane_feature(model, plane)
        plane_object = self.api._member(feature, "GetSpecificFeature2")
        corresponding = self.api._member(component, "GetCorresponding", plane_object)
        if corresponding is None:
            raise _AssemblyNativeError(
                "component_plane_context_resolution_failed", reference
            )
        return corresponding

    def _standard_plane_feature(self, model: Any, plane: str) -> Any:
        target = _STANDARD_PLANE_INDEX[plane]
        index = 0
        feature = self.api.first_feature(model)
        visited = 0
        while feature is not None:
            visited += 1
            if visited > self.max_features:
                raise _AssemblyNativeError("feature_traversal_limit")
            if self.api.feature_type(feature) == "RefPlane":
                if index == target:
                    return feature
                index += 1
            feature = self.api.next_feature(feature)
        raise _AssemblyNativeError("standard_plane_missing", plane)

    def _mate_features(self, assembly: Any) -> tuple[Any, ...]:
        mate_group = None
        feature = self.api.first_feature(assembly)
        visited = 0
        while feature is not None:
            visited += 1
            if visited > self.max_features:
                raise _AssemblyNativeError("feature_traversal_limit")
            if self.api.feature_type(feature) == "MateGroup":
                mate_group = feature
                break
            feature = self.api.next_feature(feature)
        if mate_group is None:
            return ()
        result: list[Any] = []
        subfeature = self.api._member(mate_group, "GetFirstSubFeature")
        while subfeature is not None:
            result.append(subfeature)
            if len(result) > self.max_features:
                raise _AssemblyNativeError("mate_traversal_limit")
            subfeature = self.api._member(subfeature, "GetNextSubFeature")
        return tuple(result)

    def _mate_feature(self, assembly: Any, mate_id: str) -> Any:
        matches = tuple(
            feature
            for feature in self._mate_features(assembly)
            if self.api.feature_name(feature) == mate_id
        )
        if not matches:
            raise _AssemblyNativeError("missing_mate", mate_id)
        if len(matches) > 1:
            raise _AssemblyNativeError("ambiguous_mate_identity", mate_id)
        return matches[0]

    def _mate_snapshot(self, feature: Any) -> MateSnapshot:
        mate = self.api._member(feature, "GetSpecificFeature2")
        if mate is None:
            raise _AssemblyNativeError(
                "mate_specific_feature_missing", self.api.feature_name(feature)
            )
        native_kind = int(self.api._member(mate, "Type"))
        kind = self._kind_from_native(native_kind)
        suppressed = self._feature_suppressed_current(feature)
        code, warning = self.api.feature_error(feature)
        if suppressed:
            state = MateState.SUPPRESSED
        elif int(code) == 0:
            state = MateState.SOLVED
        else:
            state = MateState.DANGLING
        value = None
        if kind in (MateKind.DISTANCE, MateKind.ANGLE):
            definition = self.api._member(feature, "GetDefinition")
            if definition is not None:
                attr = "Distance" if kind is MateKind.DISTANCE else "Angle"
                try:
                    value = float(self.api._member(definition, attr))
                except Exception:
                    value = None
        component_ids: list[str] = []
        try:
            count = int(self.api._member(mate, "GetMateEntityCount"))
            for index in range(count):
                entity = self.api._member(mate, "MateEntity", index)
                component = self.api._member(entity, "ReferenceComponent")
                if component is not None:
                    identity = self.api.component_name(component)
                    if identity and identity not in component_ids:
                        component_ids.append(identity)
        except Exception:
            pass
        errors = () if int(code) == 0 or warning else (f"feature_error:{int(code)}",)
        return MateSnapshot(
            identity=self.api.feature_name(feature),
            state=state,
            component_ids=tuple(component_ids),
            degrees_of_freedom=None,
            rebuild_errors=errors,
            kind=kind,
            value=value,
        )

    def _feature_suppressed_current(self, feature: Any) -> bool:
        try:
            value = self.api._member(
                feature, "IsSuppressed2", _SW_THIS_CONFIGURATION, None
            )
        except Exception:
            return False
        if isinstance(value, (tuple, list)):
            return bool(value[0]) if value else False
        return bool(value)

    @staticmethod
    def _parse_selection_ref(reference: str) -> tuple[str | None, str]:
        if not isinstance(reference, str):
            raise AssemblyRefusal("unsupported_selection_reference")
        parts = reference.split(":")
        if len(parts) != 3 or parts[1] != "plane" or parts[2] not in _STANDARD_PLANE_INDEX:
            raise AssemblyRefusal("unsupported_selection_reference", reference)
        component = None if parts[0] == "assembly" else parts[0]
        if component is not None and not component.strip():
            raise AssemblyRefusal("unsupported_selection_reference", reference)
        return component, parts[2]

    @staticmethod
    def _native_suppression(state: ComponentLoadState) -> int:
        return {
            ComponentLoadState.SUPPRESSED: _SW_COMPONENT_SUPPRESSED,
            ComponentLoadState.LIGHTWEIGHT: _SW_COMPONENT_LIGHTWEIGHT,
            ComponentLoadState.RESOLVED: _SW_COMPONENT_RESOLVED,
        }[state]

    @staticmethod
    def _domain_suppression(state: int) -> ComponentLoadState:
        if state == _SW_COMPONENT_SUPPRESSED:
            return ComponentLoadState.SUPPRESSED
        if state in (_SW_COMPONENT_LIGHTWEIGHT, _SW_COMPONENT_FULLY_LIGHTWEIGHT):
            return ComponentLoadState.LIGHTWEIGHT
        if state in (_SW_COMPONENT_FULLY_RESOLVED, _SW_COMPONENT_RESOLVED):
            return ComponentLoadState.RESOLVED
        raise _AssemblyNativeError(
            "unknown_component_suppression_state", str(state)
        )

    @staticmethod
    def _mate_type(kind: MateKind) -> int:
        return _MATE_TYPE[kind]

    @staticmethod
    def _kind_from_native(native_kind: int) -> MateKind:
        try:
            return _MATE_KIND_BY_NATIVE[native_kind]
        except KeyError as exc:
            raise _AssemblyNativeError(
                "unsupported_native_mate_type", str(native_kind)
            ) from exc

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
            raise AssemblyPostconditionError(
                "native_state_uncertain", f"call_id={result.call_id}; {detail}"
            )
        code = failure.code if failure is not None else result.state.value
        raise AssemblyRefusal(code, detail)
