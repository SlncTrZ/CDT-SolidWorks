"""Lane-local native sheet-metal adapter for base-flange and flat-pattern workflows."""

from __future__ import annotations

import math
from pathlib import Path
import uuid
from typing import Any

from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult

from cdt_solidworks.body.native import BodyNativeAdapter

_SW_SUPPRESS_FEATURE = 0
_SW_UNSUPPRESS_FEATURE = 1
_SW_THIS_CONFIGURATION = 1
_SW_HEM_OPEN = 0
_SW_HEM_POSITION = {"inside": 1, "outside": 2}
_SW_RELIEF_NONE = 4
_SW_RELIEF_OBROUND = 3
_SW_FM_EDGE_FLANGE = 37
_SW_FLANGE_OFFSET_BLIND = 1
_SW_FLANGE_POSITION_MATERIAL_INSIDE = 1
_SW_FLANGE_DIM_INNER_VIRTUAL_SHARP = 2
_BOUNDARY_EDGE_SELECTORS = frozenset({"bbox:+x", "bbox:-x", "bbox:+y", "bbox:-y"})
_EDGE_TOLERANCE_M = 1e-7


class SheetMetalNativeAdapter(BodyNativeAdapter):
    """Bounded native sheet-metal surface; deliberately excludes unstable edge identity mapping."""

    def __init__(self, session: Any, **kwargs: Any) -> None:
        super().__init__(session, **kwargs)
        self._cad_core = CadCoreService(
            session,
            path_policy=self.path_policy,
            default_timeout=self.default_timeout,
            max_features=self.max_features,
        )

    def create_base_flange(
        self,
        output_path: str | Path,
        *,
        width_mm: float,
        height_mm: float,
        thickness_mm: float,
        bend_radius_mm: float,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Reuse the accepted native core Base Flange implementation instead of duplicating it."""
        return self._cad_core.create_sheet_metal_base_flange(
            output_path,
            width_mm=width_mm,
            height_mm=height_mm,
            thickness_mm=thickness_mm,
            bend_radius_mm=bend_radius_mm,
            timeout=timeout,
        )

    def inspect(
        self, path: str | Path, *, timeout: float | None = None
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
        except Exception as exc:
            return self._local_failure(exc, "sheet_metal_inspect")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                return {"path": source, **self._state(model)}
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sheet_metal_inspect",
            timeout=self._timeout(timeout),
        )

    def add_edge_flange(
        self,
        path: str | Path,
        *,
        edge_selector: str,
        length_mm: float,
        angle_deg: float = 90.0,
        bend_radius_mm: float | None = None,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Create one bounded blind Edge Flange on a rectangular Base Flange perimeter edge."""
        try:
            source = self._validate_part_path(path)
            selector = str(edge_selector).strip()
            length = float(length_mm)
            angle = float(angle_deg)
            radius = None if bend_radius_mm is None else float(bend_radius_mm)
            if selector not in _BOUNDARY_EDGE_SELECTORS:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_edge_flange",
                    "Edge Flange selector must be one of bbox:+x, bbox:-x, bbox:+y, bbox:-y.",
                )
            if not math.isfinite(length) or length <= 0.0:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_edge_flange",
                    "Edge Flange length must be positive and finite.",
                )
            if not math.isfinite(angle) or not 0.0 < angle < 180.0:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_edge_flange",
                    "Edge Flange angle must be finite and in the range (0, 180).",
                )
            if radius is not None and (not math.isfinite(radius) or radius <= 0.0):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_edge_flange",
                    "Edge Flange bend radius must be positive and finite when supplied.",
                )
        except Exception as exc:
            return self._local_failure(exc, "sheet_metal_add_edge_flange")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            phase = "state_readback"
            try:
                state_before = self._state(model)
                if not state_before["is_sheet_metal"] or state_before["flattened"]:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange requires one formed Base Flange sheet-metal body.",
                    )
                solids = tuple(self.api.bodies(model, 0, False))
                if len(solids) != 1:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Bounded Edge Flange currently requires exactly one solid body.",
                    )
                phase = "edge_resolve"
                edge = self._resolve_boundary_edge(solids[0], selector)
                phase = "profile_sketch"
                angle_rad = math.radians(angle)
                sketch_feature = self.api._member(model, "InsertSketchForEdgeFlange", edge, angle_rad, False)
                if sketch_feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS did not create the Edge Flange profile sketch.",
                    )
                if not bool(self.api._member(sketch_feature, "Select2", False, 0)):
                    raise NativeRuntimeError(
                        "cad_selection_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange profile sketch could not be selected for editing.",
                    )
                self.api._member(model, "EditSketch")
                sketch = self.api._member(model, "GetActiveSketch2")
                if sketch is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS did not enter the Edge Flange profile sketch.",
                    )
                self.api._member(model, "ClearSelection2", True)
                if not bool(self.api._member(edge, "Select4", False, self.api.null_dispatch())):
                    raise NativeRuntimeError(
                        "cad_selection_failed",
                        "sheet_metal_add_edge_flange",
                        "Resolved Edge Flange boundary edge could not be selected in the profile sketch.",
                    )
                sketch_manager = self.api._member(model, "SketchManager")
                if not bool(self.api._member(sketch_manager, "SketchUseEdge", False)):
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS could not project the selected edge into the Edge Flange profile sketch.",
                    )
                segments = self._as_tuple(self.api._member(sketch, "GetSketchSegments"))
                if len(segments) != 1:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Bounded Edge Flange profile must begin with exactly one projected edge segment.",
                        details={"segments": len(segments)},
                    )
                profile_edge = segments[0]
                start_point = self.api._member(profile_edge, "GetStartPoint2")
                end_point = self.api._member(profile_edge, "GetEndPoint2")
                x1 = float(self.api._member(start_point, "X"))
                y1 = float(self.api._member(start_point, "Y"))
                x2 = float(self.api._member(end_point, "X"))
                y2 = float(self.api._member(end_point, "Y"))
                dx = x2 - x1
                dy = y2 - y1
                edge_length = math.hypot(dx, dy)
                if not math.isfinite(edge_length) or edge_length <= _EDGE_TOLERANCE_M:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Projected Edge Flange profile edge is degenerate.",
                    )
                profile_depth = length / 1000.0
                nx = -dy / edge_length
                ny = dx / edge_length
                p3 = (x2 + nx * profile_depth, y2 + ny * profile_depth)
                p4 = (x1 + nx * profile_depth, y1 + ny * profile_depth)
                created_profile = (
                    self.api._member(sketch_manager, "CreateLine", x2, y2, 0.0, p3[0], p3[1], 0.0),
                    self.api._member(sketch_manager, "CreateLine", p3[0], p3[1], 0.0, p4[0], p4[1], 0.0),
                    self.api._member(sketch_manager, "CreateLine", p4[0], p4[1], 0.0, x1, y1, 0.0),
                )
                if any(segment is None for segment in created_profile):
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS did not create the bounded Edge Flange profile geometry.",
                    )
                self.api._member(model, "InsertSketch2", True)

                phase = "insert_flange"
                manager = self.api._member(model, "FeatureManager")
                parent_radius_mm = float(state_before.get("bend_radius_mm") or 0.0)
                effective_radius_mm = radius if radius is not None else parent_radius_mm
                if effective_radius_mm <= 0.0:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange requires a positive parent or explicit bend radius.",
                    )
                definition = self.api._member(manager, "CreateDefinition", _SW_FM_EDGE_FLANGE)
                if definition is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS did not create Edge Flange feature data.",
                    )
                add_edges = self.api._member(definition, "AddEdges", (edge,), (sketch,))
                if add_edges is False:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS rejected the Edge Flange edge/profile binding.",
                    )
                definition.UseDefaultBendRadius = radius is None
                if radius is not None:
                    definition.BendRadius = effective_radius_mm / 1000.0
                definition.BendAngle = angle_rad
                definition.OffsetType = _SW_FLANGE_OFFSET_BLIND
                definition.OffsetDistance = length / 1000.0
                definition.OffsetDimType = _SW_FLANGE_DIM_INNER_VIRTUAL_SHARP
                definition.PositionType = _SW_FLANGE_POSITION_MATERIAL_INSIDE
                definition.UsePositionOffset = False
                definition.UseDefaultBendAllowance = True
                definition.UseDefaultBendRelief = True
                feature = self.api._member(manager, "CreateFeature", definition)
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_edge_flange",
                        "SOLIDWORKS did not create the Edge Flange feature.",
                    )
                phase = "rebuild"
                self._require_clean_rebuild(model, "sheet_metal_add_edge_flange")
                phase = "feature_readback"
                definition = self.api._member(feature, "GetDefinition")
                actual_angle = math.degrees(float(self.api._member(definition, "BendAngle")))
                actual_radius_mm = float(self.api._member(definition, "BendRadius")) * 1000.0
                actual_length_mm = float(self.api._member(definition, "OffsetDistance")) * 1000.0
                if not math.isclose(actual_angle, angle, rel_tol=0.0, abs_tol=1e-6):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange angle read-back does not match the request.",
                    )
                if not math.isclose(actual_length_mm, length, rel_tol=0.0, abs_tol=1e-6):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange length read-back does not match the request.",
                    )
                if not math.isclose(actual_radius_mm, effective_radius_mm, rel_tol=0.0, abs_tol=1e-6):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange bend-radius read-back does not match the request.",
                    )
                state_after = self._state(model)
                if not state_after["is_sheet_metal"] or state_after["flattened"]:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_edge_flange",
                        "Edge Flange did not preserve formed sheet-metal state.",
                    )
                phase = "save"
                self._save(model, "sheet_metal_add_edge_flange")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "edge_selector": selector,
                    "length_mm": actual_length_mm,
                    "angle_deg": actual_angle,
                    "bend_radius_mm": actual_radius_mm,
                    **state_after,
                }
            except NativeRuntimeError:
                raise
            except Exception as exc:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "sheet_metal_add_edge_flange",
                    f"SOLIDWORKS Edge Flange operation failed during {phase}.",
                    details={
                        "phase": phase,
                        "exception_type": type(exc).__name__,
                        "hresult": getattr(exc, "hresult", None),
                        "argerror": getattr(exc, "argerror", None),
                    },
                ) from exc
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sheet_metal_add_edge_flange",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def add_hem(
        self,
        path: str | Path,
        *,
        edge_selector: str,
        length_mm: float,
        gap_mm: float,
        position: str = "outside",
        reverse: bool = False,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        """Create an open hem on one bounded top-boundary selector."""
        try:
            source = self._validate_part_path(path)
            selector = str(edge_selector).strip()
            length = float(length_mm)
            gap = float(gap_mm)
            position_key = str(position).strip().lower()
            if selector not in _BOUNDARY_EDGE_SELECTORS:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_hem",
                    "Hem edge selector must be one of bbox:+x, bbox:-x, bbox:+y, bbox:-y.",
                )
            if not math.isfinite(length) or length <= 0:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_hem",
                    "Hem length must be positive and finite.",
                )
            if not math.isfinite(gap) or gap <= 0:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_hem",
                    "Hem gap must be positive and finite.",
                )
            if position_key not in _SW_HEM_POSITION:
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "sheet_metal_add_hem",
                    "Hem position must be 'inside' or 'outside'.",
                )
        except Exception as exc:
            return self._local_failure(exc, "sheet_metal_add_hem")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            phase = "state_readback"
            try:
                state_before = self._state(model)
                if not state_before["is_sheet_metal"] or state_before["flattened"]:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_add_hem",
                        "Hem requires one formed Base Flange sheet-metal body.",
                    )
                solids = tuple(self.api.bodies(model, 0, False))
                if len(solids) != 1:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_add_hem",
                        "Bounded hem creation currently requires exactly one solid body.",
                    )
                phase = "edge_resolve"
                edge = self._resolve_boundary_edge(solids[0], selector)
                phase = "edge_select"
                self.api._member(model, "ClearSelection2", True)
                selection_manager = self.api._member(model, "SelectionManager")
                select_data = self.api._member(selection_manager, "CreateSelectData")
                select_data.Mark = 0
                if not bool(self.api._member(edge, "Select4", False, select_data)):
                    raise NativeRuntimeError(
                        "cad_selection_failed",
                        "sheet_metal_add_hem",
                        "Resolved hem boundary edge could not be selected.",
                    )
                phase = "bend_allowance_create"
                manager = self.api._member(model, "FeatureManager")
                bend_allowance = self.api._member(manager, "CreateCustomBendAllowance")
                if bend_allowance is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_hem",
                        "SOLIDWORKS did not create custom bend-allowance data for the Hem feature.",
                    )
                phase = "bend_allowance_set"
                k_factor = state_before.get("k_factor")
                bend_allowance.KFactor = (
                    float(k_factor)
                    if k_factor is not None
                    and math.isfinite(float(k_factor))
                    and 0.0 < float(k_factor) <= 1.0
                    else 0.5
                )
                bend_allowance.Type = 2
                phase = "insert_hem"
                bend_radius_m = max(float(state_before.get("bend_radius_mm") or 0.0) / 1000.0, 1e-6)
                miter_gap_m = max(min(gap / 1000.0, bend_radius_m), 1e-6)
                try:
                    feature = self.api._member(
                        manager,
                        "InsertSheetMetalHem2",
                        _SW_HEM_OPEN,
                        _SW_HEM_POSITION[position_key],
                        bool(reverse),
                        length / 1000.0,
                        gap / 1000.0,
                        0.0,
                        bend_radius_m,
                        miter_gap_m,
                        bend_allowance,
                        False,
                        _SW_RELIEF_OBROUND,
                        0,
                        True,
                        1.0,
                        0.0,
                        0.0,
                    )
                except Exception as exc:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_hem",
                        "SOLIDWORKS rejected the Hem creation call.",
                        details={
                            "exception_type": type(exc).__name__,
                            "hresult": getattr(exc, "hresult", None),
                            "argerror": getattr(exc, "argerror", None),
                        },
                    ) from exc
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_add_hem",
                        "SOLIDWORKS did not create the Hem feature.",
                    )
                phase = "rebuild"
                self._require_clean_rebuild(model, "sheet_metal_add_hem")
                phase = "feature_readback"
                definition = self.api._member(feature, "GetDefinition")
                actual_type = int(self.api._member(definition, "Type"))
                actual_position = int(self.api._member(definition, "BendPosition"))
                actual_length_mm = float(self.api._member(definition, "Length")) * 1000.0
                actual_gap_mm = float(self.api._member(definition, "GapDistance")) * 1000.0
                edge_count = int(self.api._member(definition, "GetEdgesCount"))
                if actual_type != _SW_HEM_OPEN or actual_position != _SW_HEM_POSITION[position_key]:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_hem",
                        "Hem type/position read-back does not match the request.",
                    )
                if edge_count != 1:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_hem",
                        "Hem edge-count read-back is not exactly one.",
                    )
                if not math.isclose(actual_length_mm, length, rel_tol=0.0, abs_tol=1e-6):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_hem",
                        "Hem length read-back does not match the request.",
                    )
                if not math.isclose(actual_gap_mm, gap, rel_tol=0.0, abs_tol=1e-6):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_hem",
                        "Hem gap read-back does not match the request.",
                    )
                state_after = self._state(model)
                if not state_after["is_sheet_metal"] or state_after["flattened"]:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_add_hem",
                        "Hem mutation did not preserve formed sheet-metal state.",
                    )
                phase = "save"
                self._save(model, "sheet_metal_add_hem")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "edge_selector": selector,
                    "length_mm": actual_length_mm,
                    "gap_mm": actual_gap_mm,
                    "position": position_key,
                    "reverse": bool(reverse),
                    **state_after,
                }
            except NativeRuntimeError:
                raise
            except Exception as exc:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "sheet_metal_add_hem",
                    f"SOLIDWORKS Hem operation failed during {phase}.",
                    details={
                        "phase": phase,
                        "exception_type": type(exc).__name__,
                        "hresult": getattr(exc, "hresult", None),
                        "argerror": getattr(exc, "argerror", None),
                    },
                ) from exc
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sheet_metal_add_hem",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def set_flattened(
        self,
        path: str | Path,
        *,
        flattened: bool,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
        except Exception as exc:
            return self._local_failure(exc, "sheet_metal_set_flattened")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                base = self._feature_of_type(model, "SMBaseFlange")
                if base is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_set_flattened",
                        "Flat-pattern operation requires a new-style Base Flange sheet-metal part.",
                    )
                flat = self._feature_of_type(model, "FlatPattern")
                if flat is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "sheet_metal_set_flattened",
                        "Sheet-metal part has no FlatPattern feature to suppress or unsuppress.",
                    )
                action = _SW_UNSUPPRESS_FEATURE if bool(flattened) else _SW_SUPPRESS_FEATURE
                changed = bool(
                    self.api._member(
                        flat,
                        "SetSuppression2",
                        action,
                        _SW_THIS_CONFIGURATION,
                        None,
                    )
                )
                if not changed:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "sheet_metal_set_flattened",
                        "SOLIDWORKS refused to change FlatPattern suppression state.",
                    )
                self._require_clean_rebuild(model, "sheet_metal_set_flattened")
                state = self._state(model)
                if state["flattened"] is not bool(flattened):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "sheet_metal_set_flattened",
                        "FlatPattern suppression read-back does not match requested state.",
                    )
                self._save(model, "sheet_metal_set_flattened")
                return {"path": source, **state}
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="sheet_metal_set_flattened",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def _state(self, model: Any) -> dict[str, Any]:
        base = self._feature_of_type(model, "SMBaseFlange")
        flat = self._feature_of_type(model, "FlatPattern")
        if base is None:
            return {
                "is_sheet_metal": False,
                "thickness_mm": None,
                "bend_radius_mm": None,
                "k_factor": None,
                "flattened": False,
                "flat_pattern_name": None,
            }
        definition = self.api._member(base, "GetDefinition")
        thickness_mm = float(self.api._member(definition, "Thickness")) * 1000.0
        radius_mm = float(self.api._member(definition, "BendRadius")) * 1000.0
        k_factor = self._read_k_factor(definition)
        flattened = False if flat is None else not self._is_suppressed(flat)
        return {
            "is_sheet_metal": True,
            "thickness_mm": thickness_mm,
            "bend_radius_mm": radius_mm,
            "k_factor": k_factor,
            "flattened": flattened,
            "flat_pattern_name": None if flat is None else self.api.feature_name(flat),
        }

    def _read_k_factor(self, definition: Any) -> float | None:
        try:
            allowance = self.api._member(definition, "GetCustomBendAllowance")
            if allowance is None:
                return None
            value = float(self.api._member(allowance, "KFactor"))
            return value
        except Exception:
            return None

    def _resolve_boundary_edge(self, body: Any, selector: str) -> Any:
        edges = self._as_tuple(self.api._member(body, "GetEdges"))
        rows: list[tuple[Any, tuple[float, float, float], tuple[float, float, float]]] = []
        points: list[tuple[float, float, float]] = []
        for edge in edges:
            start_vertex = self.api._member(edge, "GetStartVertex")
            end_vertex = self.api._member(edge, "GetEndVertex")
            if start_vertex is None or end_vertex is None:
                continue
            start = self._point3(self.api._member(start_vertex, "GetPoint"))
            end = self._point3(self.api._member(end_vertex, "GetPoint"))
            rows.append((edge, start, end))
            points.extend((start, end))
        if not rows or not points:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "sheet_metal_edge_resolve",
                "Sheet-metal body exposes no bounded linear boundary edges.",
            )
        extrema = {
            "+x": max(point[0] for point in points),
            "-x": min(point[0] for point in points),
            "+y": max(point[1] for point in points),
            "-y": min(point[1] for point in points),
        }
        top_z = max(point[2] for point in points)
        key = selector.removeprefix("bbox:")
        axis = 0 if key.endswith("x") else 1
        target = extrema[key]
        matches = [
            edge
            for edge, start, end in rows
            if abs(start[2] - top_z) <= _EDGE_TOLERANCE_M
            and abs(end[2] - top_z) <= _EDGE_TOLERANCE_M
            and abs(start[axis] - target) <= _EDGE_TOLERANCE_M
            and abs(end[axis] - target) <= _EDGE_TOLERANCE_M
        ]
        if len(matches) != 1:
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "sheet_metal_edge_resolve",
                "Boundary selector did not resolve to exactly one top perimeter edge.",
                details={"selector": selector, "matches": len(matches)},
            )
        return matches[0]

    @staticmethod
    def _point3(value: Any) -> tuple[float, float, float]:
        raw = tuple(float(item) for item in (value or ()))
        if len(raw) != 3 or any(not math.isfinite(item) for item in raw):
            raise NativeRuntimeError(
                "cad_precondition_failed",
                "sheet_metal_edge_resolve",
                "Boundary edge vertex did not return a finite XYZ point.",
            )
        return raw  # type: ignore[return-value]

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)

    def _feature_of_type(self, model: Any, type_name: str) -> Any | None:
        feature = self.api.first_feature(model)
        count = 0
        while feature is not None:
            count += 1
            if count > self.max_features:
                raise NativeRuntimeError(
                    "query_limit_exceeded",
                    "sheet_metal_feature_lookup",
                    "Sheet-metal feature lookup exceeded its bounded item limit.",
                )
            if self.api.feature_type(feature) == type_name:
                return feature
            feature = self.api.next_feature(feature)
        return None

    def _is_suppressed(self, feature: Any) -> bool:
        raw = self.api._member(feature, "IsSuppressed2", _SW_THIS_CONFIGURATION, None)
        if isinstance(raw, (tuple, list)):
            if not raw:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "sheet_metal_suppression_readback",
                    "FlatPattern suppression read-back returned no state.",
                )
            return bool(raw[0])
        return bool(raw)
