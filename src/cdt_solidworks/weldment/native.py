"""Lane-local native weldment adapter for structural-member and cut-list workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cdt_solidworks.native.errors import NativeRuntimeError

from cdt_solidworks.body.native import BodyNativeAdapter

_STRUCTURAL_MEMBER_FEATURE_TYPES = {"WeldMemberFeat"}
_WELDMENT_ENV_FEATURE_TYPE = "WeldmentFeature"
_CUT_LIST_TYPE = "CutListFolder"
_SW_CONNECTED_SEGMENTS_SIMPLE_CUT = 1
_SW_CUSTOM_INFO_TEXT = 30
_SW_CUSTOM_PROPERTY_REPLACE_VALUE = 2
_SW_CUSTOM_PROPERTY_OK = 0


class WeldmentNativeAdapter(BodyNativeAdapter):
    """Bounded weldment adapter with explicit profile roots and sketch-feature identity."""

    def __init__(
        self,
        session: Any,
        *,
        profile_roots: Iterable[str | Path],
        **kwargs: Any,
    ) -> None:
        super().__init__(session, **kwargs)
        self.profile_roots = tuple(
            Path(root).expanduser().resolve(strict=False) for root in profile_roots
        )

    def inspect(
        self, path: str | Path, *, timeout: float | None = None
    ):
        try:
            source = self._validate_part_path(path)
        except Exception as exc:
            return self._local_failure(exc, "weldment_inspect")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                return {"path": source, **self._state(model, update_cut_list=False)}
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="weldment_inspect",
            timeout=self._timeout(timeout),
        )

    def create_structural_member(
        self,
        path: str | Path,
        *,
        sketch_feature_name: str,
        profile_path: str | Path,
        profile_configuration: str = "",
        apply_corner_treatment: bool = True,
        corner_treatment_type: int = 0,
        timeout: float | None = None,
    ):
        try:
            source = self._validate_part_path(path)
            sketch_name = str(sketch_feature_name).strip()
            if not sketch_name:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "weldment_create_structural_member",
                    "sketch_feature_name must not be empty.",
                )
            profile = self._validate_profile_path(profile_path)
            if not isinstance(profile_configuration, str):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "weldment_create_structural_member",
                    "profile_configuration must be a string.",
                )
            if profile_configuration and not profile_configuration.strip():
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "weldment_create_structural_member",
                    "profile_configuration must not be whitespace-only.",
                )
            configuration = profile_configuration.strip()
            if int(corner_treatment_type) < 0:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "weldment_create_structural_member",
                    "corner_treatment_type must be non-negative.",
                )
        except Exception as exc:
            return self._local_failure(exc, "weldment_create_structural_member")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                sketch_feature = self._feature_by_name(model, sketch_name)
                if sketch_feature is None or self.api.feature_type(sketch_feature) != "ProfileFeature":
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "weldment_create_structural_member",
                        "Requested sketch feature identity is not present as a ProfileFeature.",
                    )
                sketch = self.api._member(sketch_feature, "GetSpecificFeature2")
                segments = self._weldment_path_segments(sketch)
                if not segments:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "weldment_create_structural_member",
                        "Structural member path sketch contains no segments.",
                    )

                manager = self.api._member(model, "FeatureManager")
                self._ensure_weldment_environment(model, manager)
                group = self.api._member(manager, "CreateStructuralMemberGroup")
                if group is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "weldment_create_structural_member",
                        "SOLIDWORKS did not create a structural-member group.",
                    )
                group.Segments = self.api.dispatch_array(segments)
                group.ApplyCornerTreatment = bool(apply_corner_treatment)
                if apply_corner_treatment:
                    group.CornerTreatmentType = int(corner_treatment_type)

                before = self._state(model, update_cut_list=False)
                feature = self.api._member(
                    manager,
                    "InsertStructuralWeldment5",
                    profile,
                    _SW_CONNECTED_SEGMENTS_SIMPLE_CUT,
                    False,
                    self.api.dispatch_array((group,)),
                    configuration,
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "weldment_create_structural_member",
                        "SOLIDWORKS did not create the structural-member feature.",
                    )
                self._require_clean_rebuild(model, "weldment_create_structural_member")
                after = self._state(model, update_cut_list=True)
                if after["structural_member_count"] <= before["structural_member_count"]:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "weldment_create_structural_member",
                        "Structural-member feature count did not increase after mutation.",
                    )
                if not after["cut_list_items"]:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "weldment_create_structural_member",
                        "Structural-member mutation did not produce a readable cut list.",
                    )
                self._save(model, "weldment_create_structural_member")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "profile_path": profile,
                    "profile_configuration": configuration,
                    "path_segment_count": len(segments),
                    **after,
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="weldment_create_structural_member",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def set_cut_list_property(
        self,
        path: str | Path,
        *,
        cut_list_name: str,
        property_name: str,
        value: str,
        timeout: float | None = None,
    ):
        try:
            source = self._validate_part_path(path)
            cut_name = str(cut_list_name).strip()
            prop_name = str(property_name).strip()
            prop_value = str(value).strip()
            if not cut_name or not prop_name or not prop_value:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "weldment_set_cut_list_property",
                    "Cut-list identity, property name, and value must not be empty.",
                )
        except Exception as exc:
            return self._local_failure(exc, "weldment_set_cut_list_property")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                feature = self._feature_by_name(model, cut_name)
                if feature is None or self.api.feature_type(feature) != _CUT_LIST_TYPE:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "weldment_set_cut_list_property",
                        "Requested cut-list feature identity is not present.",
                    )
                manager = self.api._member(feature, "CustomPropertyManager")
                if manager is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "weldment_set_cut_list_property",
                        "Cut-list feature has no custom-property manager.",
                    )
                before = self._custom_properties(manager)
                if prop_name in before:
                    status = int(self.api._member(manager, "Set2", prop_name, prop_value))
                else:
                    status = int(
                        self.api._member(
                            manager,
                            "Add3",
                            prop_name,
                            _SW_CUSTOM_INFO_TEXT,
                            prop_value,
                            _SW_CUSTOM_PROPERTY_REPLACE_VALUE,
                        )
                    )
                if status != _SW_CUSTOM_PROPERTY_OK:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "weldment_set_cut_list_property",
                        "SOLIDWORKS rejected the cut-list property mutation.",
                        details={"status": status},
                    )
                self._require_clean_rebuild(model, "weldment_set_cut_list_property")
                after = self._custom_properties(manager)
                if after.get(prop_name) != prop_value:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "weldment_set_cut_list_property",
                        "Cut-list property read-back does not match the requested value.",
                    )
                self._save(model, "weldment_set_cut_list_property")
                return {
                    "path": source,
                    "cut_list_name": cut_name,
                    "property_name": prop_name,
                    "value": prop_value,
                    "properties": after,
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="weldment_set_cut_list_property",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def _weldment_path_segments(self, sketch: Any) -> tuple[Any, ...]:
        raw_segments = self.api._member(sketch, "GetSketchSegments")
        segments = self._as_tuple(raw_segments)
        usable: list[Any] = []
        for segment in segments:
            try:
                construction = bool(self.api._member(segment, "ConstructionGeometry"))
            except Exception:
                construction = False
            if not construction:
                usable.append(segment)
        return tuple(usable)

    def _ensure_weldment_environment(self, model: Any, manager: Any) -> None:
        feature = self.api.first_feature(model)
        count = 0
        while feature is not None:
            count += 1
            if count > self.max_features:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "weldment_feature_lookup",
                    "Weldment feature lookup exceeded its bounded item limit.",
                )
            if self.api.feature_type(feature) == _WELDMENT_ENV_FEATURE_TYPE:
                return
            feature = self.api.next_feature(feature)
        created = self.api._member(manager, "InsertWeldmentFeature")
        if created is None:
            raise NativeRuntimeError(
                "cad_mutation_failed",
                "weldment_create_structural_member",
                "SOLIDWORKS did not create the weldment environment feature.",
            )

    def _state(self, model: Any, *, update_cut_list: bool) -> dict[str, Any]:
        feature = self.api.first_feature(model)
        structural_count = 0
        has_weldment_environment = False
        cut_items: list[dict[str, Any]] = []
        count = 0
        while feature is not None:
            count += 1
            if count > self.max_features:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "weldment_feature_lookup",
                    "Weldment feature lookup exceeded its bounded item limit.",
                )
            type_name = self.api.feature_type(feature)
            if type_name == _WELDMENT_ENV_FEATURE_TYPE:
                has_weldment_environment = True
            elif type_name in _STRUCTURAL_MEMBER_FEATURE_TYPES:
                structural_count += 1
            elif type_name == _CUT_LIST_TYPE:
                folder = self.api._member(feature, "GetSpecificFeature2")
                if folder is not None:
                    if update_cut_list:
                        try:
                            self.api._member(folder, "SetAutomaticCutList", True)
                            self.api._member(folder, "SetAutomaticUpdate", True)
                            self.api._member(folder, "UpdateCutList")
                        except Exception:
                            pass
                    quantity = int(self.api._member(folder, "GetBodyCount"))
                    if quantity > 0:
                        manager = self.api._member(feature, "CustomPropertyManager")
                        cut_items.append(
                            {
                                "name": self.api.feature_name(feature),
                                "quantity": quantity,
                                "properties": self._custom_properties(manager),
                            }
                        )
            feature = self.api.next_feature(feature)
        return {
            "has_weldment": has_weldment_environment or structural_count > 0,
            "structural_member_count": structural_count,
            "cut_list_items": cut_items,
        }

    def _custom_properties(self, manager: Any) -> dict[str, str]:
        if manager is None:
            return {}
        names = self._as_tuple(self.api._member(manager, "GetNames"))
        properties: dict[str, str] = {}
        for raw_name in names:
            name = str(raw_name or "").strip()
            if not name:
                continue
            value = self.api._member(manager, "Get", name)
            properties[name] = str(value or "")
        return properties

    def _feature_by_name(self, model: Any, expected_name: str) -> Any | None:
        feature = self.api.first_feature(model)
        count = 0
        while feature is not None:
            count += 1
            if count > self.max_features:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "weldment_sketch_lookup",
                    "Weldment sketch lookup exceeded its bounded item limit.",
                )
            if self.api.feature_name(feature) == expected_name:
                return feature
            feature = self.api.next_feature(feature)
        return None

    def _validate_profile_path(self, path: str | Path) -> str:
        candidate = Path(path).expanduser().resolve(strict=False)
        if candidate.suffix.lower() != ".sldlfp":
            raise NativeRuntimeError(
                "cad_validation_error",
                "weldment_profile_validation",
                "Weldment profile must use the .sldlfp extension.",
            )
        if not self.profile_roots:
            raise NativeRuntimeError(
                "path_policy_unconfigured",
                "weldment_profile_validation",
                "Weldment structural-member creation is disabled until profile roots are configured.",
            )
        if not any(self._is_within(candidate, root) for root in self.profile_roots):
            raise NativeRuntimeError(
                "path_outside_allowed_root",
                "weldment_profile_validation",
                "Weldment profile path is outside the configured profile roots.",
            )
        if not candidate.is_file():
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "weldment_profile_validation",
                "Weldment profile file does not exist.",
            )
        return str(candidate)

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)
