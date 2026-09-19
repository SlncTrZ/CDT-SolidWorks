"""Bounded native CAD operations backed by the live SOLIDWORKS COM session."""

from __future__ import annotations

import math
import os
from pathlib import Path
import uuid
from typing import Any, Sequence

from cdt_solidworks.document.path_policy import DocumentPathPolicy

from .errors import NativeRuntimeError, failure_from_exception
from .models import NativeCallResult
from .rebuild import rebuild_document

_PART_EXT = ".sldprt"
_ASSEMBLY_EXT = ".sldasm"
_STANDARD_PLANE_INDEX = {"front": 0, "top": 1, "right": 2}
_SW_BODY_ADD = 15903
_SW_FM_BASE_FLANGE = 34
_SW_RELIEF_NONE = 4
_SW_CONFIG_CURRENT = 0
_SW_MATE_COINCIDENT = 0
_SW_MATE_ALIGN_ALIGNED = 0


class CadCoreService:
    """Finite semantic CAD surface; callers never supply COM method names or raw API arguments."""

    def __init__(
        self,
        session: Any,
        *,
        path_policy: DocumentPathPolicy | None = None,
        default_timeout: float = 60.0,
        max_features: int = 100_000,
    ) -> None:
        self.session = session
        self.api = session.api
        self.path_policy = path_policy or DocumentPathPolicy()
        self.default_timeout = default_timeout
        self.max_features = max_features

    def create_rectangle_sketch(
        self,
        output_path: str | Path,
        *,
        width_mm: float,
        height_mm: float,
        plane: str = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            target = self._validate_new_path(output_path, _PART_EXT)
            width = self._positive_mm(width_mm, "width_mm")
            height = self._positive_mm(height_mm, "height_mm")
            cx = self._finite_mm(center_x_mm, "center_x_mm")
            cy = self._finite_mm(center_y_mm, "center_y_mm")
            plane_key = self._plane_key(plane)
        except Exception as exc:
            return self._local_failure(exc, "sketch_create_rectangle")

        def operation(app: Any) -> dict[str, Any]:
            model = self._new_document(app, 1)
            try:
                sketch = self._create_rectangle_sketch(
                    model, plane_key, width, height, cx, cy
                )
                self._require_clean_rebuild(model, "sketch_create_rectangle")
                self._save_as(model, target, "sketch_create_rectangle")
                return {"path": target, **sketch}
            finally:
                self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sketch_create_rectangle",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def create_rect_extrude(
        self,
        output_path: str | Path,
        *,
        width_mm: float,
        height_mm: float,
        depth_mm: float,
        plane: str = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            target = self._validate_new_path(output_path, _PART_EXT)
            width = self._positive_mm(width_mm, "width_mm")
            height = self._positive_mm(height_mm, "height_mm")
            depth = self._positive_mm(depth_mm, "depth_mm")
            cx = self._finite_mm(center_x_mm, "center_x_mm")
            cy = self._finite_mm(center_y_mm, "center_y_mm")
            plane_key = self._plane_key(plane)
        except Exception as exc:
            return self._local_failure(exc, "part_create_rect_extrude")

        def operation(app: Any) -> dict[str, Any]:
            model = self._new_document(app, 1)
            try:
                sketch = self._create_rectangle_sketch(
                    model, plane_key, width, height, cx, cy
                )
                feature = self._extrude_selected_sketch(model, depth, merge=True)
                self._require_clean_rebuild(model, "part_create_rect_extrude")
                bodies = self.api.bodies(model, 0, False)
                if len(bodies) != 1:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_create_rect_extrude",
                        "Extrude did not produce exactly one solid body.",
                    )
                self._save_as(model, target, "part_create_rect_extrude")
                return {
                    "path": target,
                    **sketch,
                    "feature_name": self.api.feature_name(feature),
                    "body_count": len(bodies),
                }
            finally:
                self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="part_create_rect_extrude",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def create_empty_part(
        self,
        output_path: str | Path,
        *,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Create, persist, and close one empty native part for internal orchestration."""
        try:
            target = self._validate_new_path(output_path, _PART_EXT)
        except Exception as exc:
            return self._local_failure(exc, "part_create_empty")

        def operation(app: Any) -> dict[str, Any]:
            model = self._new_document(app, 1)
            try:
                self._require_clean_rebuild(model, "part_create_empty")
                self._save_as(model, target, "part_create_empty")
                return {"path": target}
            finally:
                self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="part_create_empty",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def add_rect_extrude(
        self,
        path: str | Path,
        *,
        width_mm: float,
        height_mm: float,
        depth_mm: float,
        plane: str = "front",
        center_x_mm: float = 0.0,
        center_y_mm: float = 0.0,
        merge: bool = True,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_open_path(path, _PART_EXT)
            width = self._positive_mm(width_mm, "width_mm")
            height = self._positive_mm(height_mm, "height_mm")
            depth = self._positive_mm(depth_mm, "depth_mm")
            cx = self._finite_mm(center_x_mm, "center_x_mm")
            cy = self._finite_mm(center_y_mm, "center_y_mm")
            plane_key = self._plane_key(plane)
        except Exception as exc:
            return self._local_failure(exc, "part_add_rect_extrude")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_document(app, source, 1)
            try:
                self._activate_document(app, model, source, "part_add_rect_extrude")
                sketch = self._create_rectangle_sketch(
                    model, plane_key, width, height, cx, cy
                )
                feature = self._extrude_selected_sketch(model, depth, merge=bool(merge))
                self._require_clean_rebuild(model, "part_add_rect_extrude")
                bodies = self.api.bodies(model, 0, False)
                self._save(model, "part_add_rect_extrude")
                return {
                    "path": source,
                    **sketch,
                    "feature_name": self.api.feature_name(feature),
                    "body_count": len(bodies),
                    "merge": bool(merge),
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="part_add_rect_extrude",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def combine_all_bodies(
        self, path: str | Path, *, timeout: float | None = None
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_open_path(path, _PART_EXT)
        except Exception as exc:
            return self._local_failure(exc, "part_combine_all_bodies")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_document(app, source, 1)
            try:
                bodies = self.api.bodies(model, 0, False)
                if len(bodies) < 2:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "part_combine_all_bodies",
                        "Combine requires at least two solid bodies.",
                    )
                selection_manager = self.api._member(model, "SelectionManager")
                self.api._member(model, "ClearSelection2", True)
                for body in bodies:
                    select_data = self.api._member(selection_manager, "CreateSelectData")
                    select_data.Mark = 2
                    if not bool(self.api._member(body, "Select2", True, select_data)):
                        raise NativeRuntimeError(
                            "cad_selection_failed",
                            "part_combine_all_bodies",
                            "A solid body could not be selected for combine.",
                        )
                manager = self.api._member(model, "FeatureManager")
                feature = self.api._member(
                    manager,
                    "InsertCombineFeature",
                    _SW_BODY_ADD,
                    self.api.null_dispatch(),
                    self.api.empty_variant_array(),
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "part_combine_all_bodies",
                        "SOLIDWORKS did not create the Combine feature.",
                    )
                self._require_clean_rebuild(model, "part_combine_all_bodies")
                after = self.api.bodies(model, 0, False)
                if len(after) != 1:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_combine_all_bodies",
                        "Combine did not reduce the part to one solid body.",
                    )
                self._save(model, "part_combine_all_bodies")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "body_count_before": len(bodies),
                    "body_count_after": len(after),
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="part_combine_all_bodies",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def split_by_plane(
        self,
        path: str | Path,
        *,
        plane: str,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_open_path(path, _PART_EXT)
            plane_key = self._plane_key(plane)
        except Exception as exc:
            return self._local_failure(exc, "part_split_by_plane")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_document(app, source, 1)
            try:
                selected_name = self._select_standard_plane(model, plane_key)
                manager = self.api._member(model, "FeatureManager")
                raw = self.api._member(manager, "PreSplitBody2")
                bodies = self._as_tuple(raw)
                if len(bodies) < 2:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "part_split_by_plane",
                        "Selected plane does not split the current solid into multiple bodies.",
                    )
                feature = self.api._member(
                    manager,
                    "PostSplitBody2",
                    self.api.dispatch_array(bodies),
                    False,
                    self.api.dispatch_array((None,) * len(bodies)),
                    self.api.string_array(("",) * len(bodies)),
                    "",
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "part_split_by_plane",
                        "SOLIDWORKS did not create the Split feature.",
                    )
                self._require_clean_rebuild(model, "part_split_by_plane")
                after = self.api.bodies(model, 0, False)
                if len(after) < 2:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_split_by_plane",
                        "Split feature did not leave multiple solid bodies in the part.",
                    )
                self._save(model, "part_split_by_plane")
                return {
                    "path": source,
                    "plane": selected_name,
                    "feature_name": self.api.feature_name(feature),
                    "body_count_after": len(after),
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="part_split_by_plane",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def create_sheet_metal_base_flange(
        self,
        output_path: str | Path,
        *,
        width_mm: float,
        height_mm: float,
        thickness_mm: float,
        bend_radius_mm: float,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            target = self._validate_new_path(output_path, _PART_EXT)
            width = self._positive_mm(width_mm, "width_mm")
            height = self._positive_mm(height_mm, "height_mm")
            thickness = self._positive_mm(thickness_mm, "thickness_mm")
            bend_radius = self._positive_mm(bend_radius_mm, "bend_radius_mm")
        except Exception as exc:
            return self._local_failure(exc, "sheet_metal_create_base_flange")

        def operation(app: Any) -> dict[str, Any]:
            model = self._new_document(app, 1)
            try:
                self._create_rectangle_sketch(model, "front", width, height, 0.0, 0.0)
                manager = self.api._member(model, "FeatureManager")
                data = self.api._member(manager, "CreateDefinition", _SW_FM_BASE_FLANGE)
                if data is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_create_base_flange",
                        "SOLIDWORKS did not create Base Flange feature data.",
                    )
                self.api._member(
                    data,
                    "Initialize",
                    False,
                    False,
                    self.api.null_dispatch(),
                    False,
                    _SW_RELIEF_NONE,
                    False,
                    0.0,
                    0.0,
                    0.0,
                )
                data.OverrideDefaultSheetMetalParameters = True
                data.Thickness = thickness
                data.BendRadius = bend_radius
                feature = self.api._member(manager, "CreateFeature", data)
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_create_base_flange",
                        "SOLIDWORKS did not create the Base Flange feature.",
                    )
                self._require_clean_rebuild(model, "sheet_metal_create_base_flange")
                definition = self.api._member(feature, "GetDefinition")
                actual_thickness = float(definition.Thickness)
                actual_radius = float(definition.BendRadius)
                if not math.isclose(actual_thickness, thickness, rel_tol=0.0, abs_tol=1e-9):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_create_base_flange",
                        "Base Flange thickness read-back does not match the request.",
                    )
                bodies = self.api.bodies(model, 0, False)
                if len(bodies) != 1:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_create_base_flange",
                        "Base Flange did not produce exactly one solid body.",
                    )
                self._save_as(model, target, "sheet_metal_create_base_flange")
                return {
                    "path": target,
                    "feature_name": self.api.feature_name(feature),
                    "feature_type": self.api.feature_type(feature),
                    "thickness_mm": actual_thickness * 1000.0,
                    "bend_radius_mm": actual_radius * 1000.0,
                    "body_count": len(bodies),
                }
            finally:
                self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sheet_metal_create_base_flange",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def create_surface_extrude(
        self,
        output_path: str | Path,
        *,
        line_length_mm: float,
        depth_mm: float,
        plane: str = "front",
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            target = self._validate_new_path(output_path, _PART_EXT)
            length = self._positive_mm(line_length_mm, "line_length_mm")
            depth = self._positive_mm(depth_mm, "depth_mm")
            plane_key = self._plane_key(plane)
        except Exception as exc:
            return self._local_failure(exc, "surface_create_extrude")

        def operation(app: Any) -> dict[str, Any]:
            model = self._new_document(app, 1)
            try:
                self._select_standard_plane(model, plane_key)
                sketch_manager = self.api._member(model, "SketchManager")
                self.api._member(sketch_manager, "InsertSketch", True)
                line = self.api._member(
                    sketch_manager,
                    "CreateLine",
                    -length / 2.0,
                    0.0,
                    0.0,
                    length / 2.0,
                    0.0,
                    0.0,
                )
                if line is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "surface_create_extrude",
                        "SOLIDWORKS did not create the surface profile line.",
                    )
                self.api._member(sketch_manager, "InsertSketch", True)
                manager = self.api._member(model, "FeatureManager")
                self.api._member(
                    manager,
                    "FeatureExtruRefSurface3",
                    True,
                    False,
                    0,
                    0.0,
                    0,
                    0,
                    depth,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    0.0,
                    0.0,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                    False,
                )
                self._require_clean_rebuild(model, "surface_create_extrude")
                surface_bodies = self.api.bodies(model, 1, False)
                solid_bodies = self.api.bodies(model, 0, False)
                if len(surface_bodies) < 1 or solid_bodies:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "surface_create_extrude",
                        "Surface extrusion body read-back does not match the requested surface operation.",
                    )
                surface_feature = self._last_feature_of_type(model, "ExtruRefSurface")
                self._save_as(model, target, "surface_create_extrude")
                return {
                    "path": target,
                    "feature_name": self.api.feature_name(surface_feature),
                    "surface_body_count": len(surface_bodies),
                    "solid_body_count": len(solid_bodies),
                }
            finally:
                self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="surface_create_extrude",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def create_assembly(
        self,
        output_path: str | Path,
        *,
        component_paths: Sequence[str],
        placements_mm: Sequence[Sequence[float]],
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            target = self._validate_new_path(output_path, _ASSEMBLY_EXT)
            components = tuple(self._validate_component_path(path) for path in component_paths)
            placements = self._validate_placements(placements_mm, len(components))
            if not components:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_create",
                    "Assembly requires at least one component path.",
                )
        except Exception as exc:
            return self._local_failure(exc, "assembly_create")

        def operation(app: Any) -> dict[str, Any]:
            opened: list[Any] = []
            model = self._new_document(app, 2)
            try:
                for source in dict.fromkeys(components):
                    doc_type = 2 if Path(source).suffix.lower() == _ASSEMBLY_EXT else 1
                    component_doc, owned = self._open_document(app, source, doc_type, read_only=True)
                    if owned:
                        opened.append(component_doc)
                inserted: list[str] = []
                for source, (x, y, z) in zip(components, placements, strict=True):
                    component = self.api._member(
                        model,
                        "AddComponent5",
                        source,
                        _SW_CONFIG_CURRENT,
                        "",
                        False,
                        "",
                        x,
                        y,
                        z,
                    )
                    if component is None:
                        raise NativeRuntimeError(
                            "cad_mutation_failed",
                            "assembly_create",
                            "SOLIDWORKS did not insert a requested assembly component.",
                        )
                    inserted.append(self.api.component_name(component))
                self._require_clean_rebuild(model, "assembly_create")
                readback = self.api.components(model, True)
                if len(readback) != len(components):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "assembly_create",
                        "Assembly component count read-back does not match the request.",
                    )
                self._save_as(model, target, "assembly_create")
                return {
                    "path": target,
                    "component_count": len(readback),
                    "components": inserted,
                }
            finally:
                self._close_quietly(app, model)
                for doc in opened:
                    self._close_quietly(app, doc)

        return self.session.execute(
            operation,
            stage="assembly_create",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def add_coincident_plane_mate(
        self,
        path: str | Path,
        *,
        component_name: str,
        component_plane: str,
        assembly_plane: str,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_open_path(path, _ASSEMBLY_EXT)
            if not component_name.strip():
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_add_coincident_plane_mate",
                    "component_name must not be empty.",
                )
            component_plane_key = self._plane_key(component_plane)
            assembly_plane_key = self._plane_key(assembly_plane)
        except Exception as exc:
            return self._local_failure(exc, "assembly_add_coincident_plane_mate")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_document(app, source, 2)
            try:
                components = self.api.components(model, True)
                component = next(
                    (item for item in components if self.api.component_name(item) == component_name),
                    None,
                )
                if component is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "assembly_add_coincident_plane_mate",
                        "Requested component identity is not present in the assembly.",
                    )
                part_model = self.api._member(component, "GetModelDoc2")
                if part_model is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "assembly_add_coincident_plane_mate",
                        "Requested component model is not resolved.",
                    )
                component_feature = self._standard_plane_feature(part_model, component_plane_key)
                component_plane_object = self.api._member(component_feature, "GetSpecificFeature2")
                component_context_plane = self.api._member(
                    component, "GetCorresponding", component_plane_object
                )
                assembly_feature = self._standard_plane_feature(model, assembly_plane_key)
                assembly_plane_object = self.api._member(assembly_feature, "GetSpecificFeature2")
                if component_context_plane is None or assembly_plane_object is None:
                    raise NativeRuntimeError(
                        "cad_selection_failed",
                        "assembly_add_coincident_plane_mate",
                        "Mate plane identity could not be resolved in assembly context.",
                    )
                mate_data = self.api._member(model, "CreateMateData", _SW_MATE_COINCIDENT)
                mate_data.EntitiesToMate = self.api.dispatch_array(
                    (component_context_plane, assembly_plane_object)
                )
                mate_data.MateAlignment = _SW_MATE_ALIGN_ALIGNED
                feature = self.api._member(model, "CreateMate", mate_data)
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "assembly_add_coincident_plane_mate",
                        "SOLIDWORKS did not create the coincident mate.",
                    )
                self._require_clean_rebuild(model, "assembly_add_coincident_plane_mate")
                self._save(model, "assembly_add_coincident_plane_mate")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "feature_type": self.api.feature_type(feature),
                    "component": component_name,
                    "component_plane": component_plane_key,
                    "assembly_plane": assembly_plane_key,
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="assembly_add_coincident_plane_mate",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def _new_document(self, app: Any, doc_type: int) -> Any:
        template = str(
            self.api._member(app, "GetDocumentTemplate", doc_type, "", 0, 0.0, 0.0) or ""
        )
        if not template or not Path(template).is_file():
            raise NativeRuntimeError(
                "solidworks_template_missing",
                "document_new",
                "SOLIDWORKS did not provide an existing default document template.",
            )
        model = self.api._member(app, "NewDocument", template, 0, 0.0, 0.0)
        if model is None:
            raise NativeRuntimeError(
                "cad_mutation_failed",
                "document_new",
                "SOLIDWORKS did not create the requested document.",
            )
        return model

    def _open_document(
        self, app: Any, path: str, doc_type: int, *, read_only: bool = False
    ) -> tuple[Any, bool]:
        existing = self.api.get_open_document(app, path)
        if existing is not None:
            return existing, False
        model, errors, warnings = self.api.open_document(
            app,
            path,
            doc_type,
            read_only=read_only,
            silent=True,
            configuration="",
        )
        if model is None or int(errors) != 0:
            raise NativeRuntimeError(
                "document_open_failed",
                "cad_document_open",
                "SOLIDWORKS failed to open the CAD document.",
                details={"errors": int(errors), "warnings": int(warnings)},
            )
        return model, True

    def _activate_document(
        self, app: Any, model: Any, expected_path: str, stage: str
    ) -> None:
        active, errors = self.api.activate_document(app, model)
        if active is None or int(errors) != 0:
            raise NativeRuntimeError(
                "document_activation_failed",
                stage,
                "SOLIDWORKS could not activate the explicit mutation target.",
                details={"errors": int(errors)},
            )
        active_path = str(self.api.document_path(active) or "")
        if not active_path or os.path.normcase(os.path.abspath(active_path)) != os.path.normcase(
            os.path.abspath(expected_path)
        ):
            raise NativeRuntimeError(
                "document_context_mismatch",
                stage,
                "Activated SOLIDWORKS document does not match the explicit mutation target.",
            )

    def _create_rectangle_sketch(
        self,
        model: Any,
        plane: str,
        width_m: float,
        height_m: float,
        center_x_m: float,
        center_y_m: float,
    ) -> dict[str, Any]:
        actual_plane = self._select_standard_plane(model, plane)
        manager = self.api._member(model, "SketchManager")
        self.api._member(manager, "InsertSketch", True)
        segments = self.api._member(
            manager,
            "CreateCenterRectangle",
            center_x_m,
            center_y_m,
            0.0,
            center_x_m + width_m / 2.0,
            center_y_m + height_m / 2.0,
            0.0,
        )
        count = len(self._as_tuple(segments))
        if count < 4:
            raise NativeRuntimeError(
                "cad_mutation_failed",
                "sketch_create_rectangle",
                "SOLIDWORKS did not create the expected rectangle sketch entities.",
            )
        self.api._member(manager, "InsertSketch", True)
        feature = self._last_feature_of_type(model, "ProfileFeature")
        return {
            "plane": actual_plane,
            "sketch_name": self.api.feature_name(feature),
            "entity_count": count,
        }

    def _extrude_selected_sketch(self, model: Any, depth_m: float, *, merge: bool) -> Any:
        manager = self.api._member(model, "FeatureManager")
        feature = self.api._member(
            manager,
            "FeatureExtrusion3",
            True,
            False,
            False,
            0,
            0,
            depth_m,
            0.0,
            False,
            False,
            False,
            False,
            0.0,
            0.0,
            False,
            False,
            False,
            False,
            bool(merge),
            False,
            True,
            0,
            0.0,
            False,
        )
        if feature is None:
            raise NativeRuntimeError(
                "cad_mutation_failed",
                "part_extrude",
                "SOLIDWORKS did not create the extrude feature.",
            )
        return feature

    def _select_standard_plane(self, model: Any, plane: str) -> str:
        feature = self._standard_plane_feature(model, plane)
        self.api._member(model, "ClearSelection2", True)
        if not bool(self.api._member(feature, "Select2", False, 0)):
            raise NativeRuntimeError(
                "cad_selection_failed",
                "plane_select",
                "SOLIDWORKS reference plane could not be selected.",
            )
        return self.api.feature_name(feature)

    def _standard_plane_feature(self, model: Any, plane: str) -> Any:
        target_index = _STANDARD_PLANE_INDEX[plane]
        index = 0
        feature = self.api.first_feature(model)
        while feature is not None:
            if self.api.feature_type(feature) == "RefPlane":
                if index == target_index:
                    return feature
                index += 1
            feature = self.api.next_feature(feature)
        raise NativeRuntimeError(
            "cad_selection_failed",
            "plane_resolve",
            "SOLIDWORKS standard reference plane could not be resolved.",
        )

    def _last_feature_of_type(self, model: Any, type_name: str) -> Any:
        found = None
        feature = self.api.first_feature(model)
        count = 0
        while feature is not None:
            count += 1
            if count > self.max_features:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "feature_lookup",
                    "Feature lookup exceeded its bounded item limit.",
                )
            if self.api.feature_type(feature) == type_name:
                found = feature
            feature = self.api.next_feature(feature)
        if found is None:
            raise NativeRuntimeError(
                "cad_postcondition_failed",
                "feature_lookup",
                "Expected SOLIDWORKS feature was not found after mutation.",
            )
        return found

    def _require_clean_rebuild(self, model: Any, stage: str) -> None:
        result = rebuild_document(model, self.api, max_features=self.max_features)
        if not result.success:
            raise NativeRuntimeError(
                "rebuild_failed",
                stage,
                "SOLIDWORKS rebuild or feature error verification failed.",
                details={
                    "native_rebuild_ok": result.native_rebuild_ok,
                    "feature_error_count": sum(not issue.is_warning for issue in result.feature_issues),
                    "feature_warning_count": sum(issue.is_warning for issue in result.feature_issues),
                },
            )

    def _save_as(self, model: Any, target: str, stage: str) -> None:
        success, errors, warnings = self.api.save_as(model, target)
        if not success or int(errors) != 0 or not Path(target).is_file():
            raise NativeRuntimeError(
                "document_save_as_failed",
                stage,
                "SOLIDWORKS failed to persist the requested native document.",
                details={"errors": int(errors), "warnings": int(warnings)},
            )

    def _save(self, model: Any, stage: str) -> None:
        success, errors, warnings = self.api.save_document(model)
        if not success or int(errors) != 0:
            raise NativeRuntimeError(
                "document_save_failed",
                stage,
                "SOLIDWORKS failed to save the mutated document.",
                details={"errors": int(errors), "warnings": int(warnings)},
            )

    def _close_quietly(self, app: Any, model: Any) -> None:
        if model is None:
            return
        try:
            self.api.close_document(app, self.api.document_title(model))
        except Exception:
            pass

    def _validate_new_path(self, path: str | Path, extension: str) -> str:
        target = self.path_policy.validate_save(path)
        self._require_extension(target, extension)
        if Path(target).exists():
            raise NativeRuntimeError(
                "document_already_exists",
                "path_validation",
                "Create operation refuses to overwrite an existing native document.",
            )
        return target

    def _validate_open_path(self, path: str | Path, extension: str) -> str:
        source = self.path_policy.validate_open(path)
        self._require_extension(source, extension)
        return source

    def _validate_component_path(self, path: str) -> str:
        source = self.path_policy.validate_open(path)
        if Path(source).suffix.lower() not in {_PART_EXT, _ASSEMBLY_EXT}:
            raise NativeRuntimeError(
                "document_extension_mismatch",
                "assembly_create",
                "Assembly components must be native SOLIDWORKS part or assembly documents.",
            )
        return source

    @staticmethod
    def _require_extension(path: str, extension: str) -> None:
        if Path(path).suffix.lower() != extension:
            raise NativeRuntimeError(
                "document_extension_mismatch",
                "path_validation",
                f"Native document path must use the {extension} extension.",
            )

    @staticmethod
    def _positive_mm(value: float, label: str) -> float:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise NativeRuntimeError(
                "cad_validation_error",
                "cad_validation",
                f"{label} must be positive and finite.",
            )
        return numeric / 1000.0

    @staticmethod
    def _finite_mm(value: float, label: str) -> float:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise NativeRuntimeError(
                "cad_validation_error",
                "cad_validation",
                f"{label} must be finite.",
            )
        return numeric / 1000.0

    @staticmethod
    def _plane_key(value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in _STANDARD_PLANE_INDEX:
            raise NativeRuntimeError(
                "cad_validation_error",
                "cad_validation",
                "plane must be front, top, or right.",
            )
        return normalized

    def _validate_placements(
        self, placements: Sequence[Sequence[float]], expected: int
    ) -> tuple[tuple[float, float, float], ...]:
        if len(placements) != expected:
            raise NativeRuntimeError(
                "cad_validation_error",
                "assembly_create",
                "placements_mm count must match component_paths count.",
            )
        normalized = []
        for placement in placements:
            if len(placement) != 3:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "assembly_create",
                    "Each placement must contain exactly x, y, z millimeter coordinates.",
                )
            normalized.append(
                tuple(self._finite_mm(value, "placement coordinate") for value in placement)
            )
        return tuple(normalized)  # type: ignore[return-value]

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def _timeout(self, timeout: float | None) -> float:
        return self.default_timeout if timeout is None else max(0.0, float(timeout))

    @staticmethod
    def _local_failure(exc: Exception, stage: str) -> NativeCallResult[Any]:
        return NativeCallResult.failed(
            failure_from_exception(exc, stage),
            call_id=uuid.uuid4().hex,
            dispatched=False,
        )
