"""Lane-local native weldment adapter for structural-member and cut-list workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cdt_solidworks.native.errors import NativeRuntimeError

from cdt_solidworks.body.native import BodyNativeAdapter

_STRUCTURAL_MEMBER_FEATURE_TYPES = {"WeldMemberFeat", "WeldmentFeature"}
_CUT_LIST_TYPE = "CutListFolder"
_SW_CONNECTED_SEGMENTS_SIMPLE_CUT = 1


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
        roots = tuple(Path(root).expanduser().resolve(strict=False) for root in profile_roots)
        if not roots:
            raise ValueError("profile_roots must contain at least one allowed weldment profile root")
        self.profile_roots = roots

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
                raw_segments = self.api._member(sketch, "GetSketchSegments")
                segments = self._as_tuple(raw_segments)
                if not segments:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "weldment_create_structural_member",
                        "Structural member path sketch contains no segments.",
                    )

                manager = self.api._member(model, "FeatureManager")
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
                    "",
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

    def _state(self, model: Any, *, update_cut_list: bool) -> dict[str, Any]:
        feature = self.api.first_feature(model)
        structural_count = 0
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
            if type_name in _STRUCTURAL_MEMBER_FEATURE_TYPES:
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
                        cut_items.append(
                            {
                                "name": self.api.feature_name(feature),
                                "quantity": quantity,
                            }
                        )
            feature = self.api.next_feature(feature)
        return {
            "has_weldment": structural_count > 0,
            "structural_member_count": structural_count,
            "cut_list_items": cut_items,
        }

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
