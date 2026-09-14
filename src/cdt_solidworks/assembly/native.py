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
    ComponentPatternSnapshot,
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
_SW_FM_LOCAL_LINEAR_PATTERN = 108
_SW_PATTERN_SPACING_AND_INSTANCES = 0

_STANDARD_PLANE_INDEX = {"front": 0, "top": 1, "right": 2}
_SELECT_ENTITY_TYPE = {"edge": 1, "face": 2, "vertex": 3}
_WIDTH_CONSTRAINT = {"centered": 0, "free": 1}
_SLOT_CONSTRAINT = {"free": 0, "centered": 1}
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
    ) -> str:
        source = str(source_path)

        def operation(app: Any) -> str:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            before_target_ids = {
                self.api.component_name(item)
                for item in self.api.components(assembly, False)
                if self.api.component_path(item) == source
            }
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

            candidates = tuple(
                item
                for item in self.api.components(assembly, False)
                if self.api.component_path(item) == source
                and (
                    configuration is None
                    or str(
                        self.api._member(item, "ReferencedConfiguration") or ""
                    )
                    == configuration
                )
            )
            candidate_ids = tuple(self.api.component_name(item) for item in candidates)
            if component_id in candidate_ids:
                return component_id
            new_ids = tuple(
                candidate_id
                for candidate_id in candidate_ids
                if candidate_id not in before_target_ids
            )
            if len(new_ids) == 1:
                return new_ids[0]
            if len(candidate_ids) == 1:
                return candidate_ids[0]
            raise _AssemblyNativeError(
                "component_replace_identity_ambiguous",
                f"before={component_id}; candidates={candidate_ids!r}",
            )

        return self._run("assembly_component_replace", True, operation)

    def delete_component(self, assembly_id: str, component_id: str) -> None:
        def operation(app: Any) -> None:
            assembly = self._assembly(app, assembly_id)
            component = self._component(assembly, component_id)
            self._select_component(assembly, component)
            try:
                result = self.api._member(assembly, "DeleteSelections", 0)
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
            try:
                math_transform = self.api._member(
                    math_utility, "CreateTransform", self._double_array(values)
                )
            except Exception as exc:
                raise _AssemblyNativeError(
                    "component_transform_create_dispatch_failed",
                    f"{type(exc).__name__}: {exc}",
                ) from exc
            if math_transform is None:
                raise _AssemblyNativeError("component_transform_create_failed")
            try:
                solved = bool(
                    self.api._member(
                        component, "SetTransformAndSolve2", math_transform
                    )
                )
            except Exception as exc:
                raise _AssemblyNativeError(
                    "component_transform_solve_dispatch_failed",
                    f"{type(exc).__name__}: {exc}",
                ) from exc
            if not solved:
                raise _AssemblyNativeError("component_transform_solve_failed")
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

    def create_linear_component_pattern(
        self,
        assembly_id: str,
        seed_component_ids: tuple[str, ...],
        direction_ref: str,
        spacing_m: float,
        total_instances: int,
    ) -> str:
        def operation(app: Any) -> str:
            assembly = self._assembly(app, assembly_id)
            seeds = tuple(
                self._component(assembly, component_id)
                for component_id in seed_component_ids
            )
            direction = self._resolve_selection_reference(assembly, direction_ref)
            manager = self.api._member(assembly, "FeatureManager")
            data = self.api._member(
                manager,
                "CreateDefinition",
                _SW_FM_LOCAL_LINEAR_PATTERN,
            )
            if data is None:
                raise _AssemblyNativeError("component_pattern_data_create_failed")
            data.SeedComponentArray = self.api.dispatch_array(seeds)
            data.D1Axis = direction
            data.D1EndCondition = _SW_PATTERN_SPACING_AND_INSTANCES
            data.D1Spacing = float(spacing_m)
            data.D1TotalInstances = int(total_instances)
            data.D1ReverseDirection = False
            data.D2PatternSeedOnly = True
            feature = self.api._member(manager, "CreateFeature", data)
            if feature is None:
                raise _AssemblyNativeError("component_pattern_create_failed")
            return self.api.feature_name(feature)

        return self._run("assembly_component_pattern_create", True, operation)

    def read_component_pattern(
        self, assembly_id: str, pattern_id: str
    ) -> ComponentPatternSnapshot:
        def operation(app: Any) -> ComponentPatternSnapshot:
            assembly = self._assembly(app, assembly_id)
            feature = self._pattern_feature(assembly, pattern_id)
            data = self.api._member(feature, "GetDefinition")
            if data is None:
                raise _AssemblyNativeError(
                    "component_pattern_definition_missing", pattern_id
                )
            raw_seeds = self.api._member(data, "SeedComponentArray")
            if raw_seeds is None:
                seeds = ()
            elif isinstance(raw_seeds, (tuple, list)):
                seeds = tuple(raw_seeds)
            else:
                seeds = (raw_seeds,)
            seed_ids = tuple(self.api.component_name(seed) for seed in seeds)
            axis = self.api._member(data, "D1Axis")
            return ComponentPatternSnapshot(
                identity=self.api.feature_name(feature),
                seed_component_ids=seed_ids,
                spacing_m=float(self.api._member(data, "D1Spacing")),
                total_instances=int(self.api._member(data, "D1TotalInstances")),
                direction_resolved=axis is not None,
            )

        return self._run("assembly_component_pattern_read", False, operation)

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
            self._configure_mate_data(kind, mate_data, entities, request)
            feature = self.api._member(assembly, "CreateMate", mate_data)
            if feature is None:
                raise _AssemblyNativeError("mate_create_failed", kind.value)
            return self.api.feature_name(feature)

        return self._run("assembly_mate_add", True, operation)

    def _configure_mate_data(
        self,
        kind: MateKind,
        mate_data: Any,
        entities: tuple[Any, ...],
        request: MateRequest,
    ) -> None:
        if kind is MateKind.WIDTH:
            if len(entities) != 4:
                raise _AssemblyNativeError("invalid_width_mate_selection")
            mate_data.WidthSelection = self.api.dispatch_array(entities[:2])
            mate_data.TabSelection = self.api.dispatch_array(entities[2:])
            mate_data.ConstraintType = _WIDTH_CONSTRAINT[request.constraint or "centered"]
            return

        mate_data.EntitiesToMate = self.api.dispatch_array(entities)
        if request.alignment is not None:
            if kind not in _MATE_ALIGNMENT_KINDS:
                raise _AssemblyNativeError("mate_alignment_not_supported", kind.value)
            mate_data.MateAlignment = _MATE_ALIGNMENT[request.alignment]
        if kind is MateKind.CONCENTRIC:
            mate_data.LockRotation = False
        elif kind is MateKind.DISTANCE and request.value is not None:
            mate_data.Distance = float(request.value)
        elif kind is MateKind.ANGLE and request.value is not None:
            mate_data.Angle = float(request.value)
        elif kind is MateKind.SLOT:
            mate_data.Constraint = _SLOT_CONSTRAINT[request.constraint or "centered"]

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
        try:
            parent = self.api._member(component, "GetParent")
            parent_identity = (
                self.api.component_name(parent) if parent is not None else None
            )
        except Exception:
            parent_identity = None
        return ComponentSnapshot(
            identity=self.api.component_name(component),
            source_path=self.api.component_path(component),
            configuration=configuration,
            transform=tuple(float(value) for value in values),
            load_state=self._domain_suppression(
                int(self.api._member(component, "GetSuppression"))
            ),
            fixed=fixed,
            parent_identity=parent_identity,
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
        component_id, reference_kind, name = self._parse_selection_ref(reference)
        if reference_kind == "component":
            assert component_id is not None
            return self._component(assembly, component_id)
        if component_id is None:
            feature = self._standard_plane_feature(assembly, name)
            entity = self.api._member(feature, "GetSpecificFeature2")
            if entity is None:
                raise _AssemblyNativeError(
                    "assembly_plane_resolution_failed", reference
                )
            return entity

        component = self._component(assembly, component_id)
        model = self.api._member(component, "GetModelDoc2")
        if model is None:
            raise _AssemblyNativeError("component_model_not_resolved", component_id)
        if reference_kind == "plane":
            feature = self._standard_plane_feature(model, name)
            native_entity = self.api._member(feature, "GetSpecificFeature2")
        else:
            native_entity = self.api._member(
                model,
                "GetEntityByName",
                name,
                _SELECT_ENTITY_TYPE[reference_kind],
            )
        if native_entity is None:
            raise _AssemblyNativeError(
                "component_entity_missing", reference
            )
        corresponding = self.api._member(component, "GetCorresponding", native_entity)
        if corresponding is None:
            raise _AssemblyNativeError(
                "component_entity_context_resolution_failed", reference
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

    def _pattern_feature(self, assembly: Any, pattern_id: str) -> Any:
        matches: list[Any] = []
        feature = self.api.first_feature(assembly)
        visited = 0
        while feature is not None:
            visited += 1
            if visited > self.max_features:
                raise _AssemblyNativeError("feature_traversal_limit")
            if self.api.feature_name(feature) == pattern_id:
                matches.append(feature)
            feature = self.api.next_feature(feature)
        if not matches:
            raise _AssemblyNativeError("missing_component_pattern", pattern_id)
        if len(matches) > 1:
            raise _AssemblyNativeError("ambiguous_component_pattern", pattern_id)
        if self.api.feature_type(matches[0]) != "LocalLPattern":
            raise _AssemblyNativeError("feature_not_component_pattern", pattern_id)
        return matches[0]

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
        definition = self.api._member(feature, "GetDefinition")
        try:
            error_status = (
                int(self.api._member(definition, "ErrorStatus"))
                if definition is not None
                else None
            )
        except Exception:
            error_status = None
        if suppressed:
            state = MateState.SUPPRESSED
        elif error_status == 5:
            state = MateState.OVER_DEFINED
        elif int(code) != 0:
            state = MateState.DANGLING
        elif error_status in (None, 1):
            state = MateState.SOLVED
        else:
            state = MateState.UNKNOWN
        value = None
        if kind in (MateKind.DISTANCE, MateKind.ANGLE) and definition is not None:
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
            error_status=error_status,
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
    def _parse_selection_ref(reference: str) -> tuple[str | None, str, str]:
        if not isinstance(reference, str) or not reference.strip():
            raise AssemblyRefusal("unsupported_selection_reference")
        parts = reference.split(":")
        if len(parts) == 2 and parts[0] == "component" and parts[1].strip():
            return parts[1], "component", ""
        if len(parts) != 3 or not parts[0].strip() or not parts[2].strip():
            raise AssemblyRefusal("unsupported_selection_reference", reference)
        component = None if parts[0] == "assembly" else parts[0]
        kind = parts[1]
        name = parts[2]
        if kind == "plane":
            if name not in _STANDARD_PLANE_INDEX:
                raise AssemblyRefusal("unsupported_selection_reference", reference)
            return component, kind, name
        if component is not None and kind in _SELECT_ENTITY_TYPE:
            return component, kind, name
        raise AssemblyRefusal("unsupported_selection_reference", reference)

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

    def _double_array(self, values: tuple[float, ...]) -> Any:
        client = getattr(self.api, "_client", None)
        pythoncom = getattr(self.api, "_pythoncom", None)
        if client is None or pythoncom is None:
            raise _AssemblyNativeError("double_array_marshalling_unavailable")
        return client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            tuple(float(value) for value in values),
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
