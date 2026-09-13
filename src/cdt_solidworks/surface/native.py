"""Lane-local native surface adapter for knit and thicken operations."""

from __future__ import annotations

import math
from pathlib import Path
import uuid
from typing import Any, Iterable

from cdt_solidworks.native.errors import NativeRuntimeError, failure_from_exception
from cdt_solidworks.native.models import NativeCallResult

from cdt_solidworks.body.native import BodyNativeAdapter

_MIN_KNIT_TOLERANCE_MM = 0.0001
_MAX_KNIT_TOLERANCE_MM = 0.1


class SurfaceNativeAdapter(BodyNativeAdapter):
    """Native surface mutations using documented SOLIDWORKS 2024 feature-manager APIs."""

    def knit(
        self,
        path: str | Path,
        *,
        surface_body_names: Iterable[str],
        tolerance_mm: float = 0.01,
        try_form_solid: bool = True,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
            names = tuple(str(name).strip() for name in surface_body_names)
            tolerance = float(tolerance_mm)
            if len(names) < 2 or len(set(names)) != len(names) or any(not name for name in names):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "surface_knit",
                    "Surface knit requires at least two unique non-empty surface-body names.",
                )
            if (
                not math.isfinite(tolerance)
                or tolerance < _MIN_KNIT_TOLERANCE_MM
                or tolerance > _MAX_KNIT_TOLERANCE_MM
            ):
                raise NativeRuntimeError(
                    "cad_validation_error",
                    "surface_knit",
                    "Knit tolerance must be within SOLIDWORKS' supported 0.0001-0.1 mm range.",
                )
        except Exception as exc:
            return self._local_failure(exc, "surface_knit")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                before_surfaces = tuple(self.api.bodies(model, 1, False))
                before_solids = tuple(self.api.bodies(model, 0, False))
                by_name = {self._body_name(body): body for body in before_surfaces}
                missing = [name for name in names if name not in by_name]
                if missing:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "surface_knit",
                        "Requested surface-body identity is not present in the part.",
                        details={"missing_body": missing[0]},
                    )
                self.api._member(model, "ClearSelection2", True)
                selection_manager = self.api._member(model, "SelectionManager")
                for name in names:
                    select_data = self.api._member(selection_manager, "CreateSelectData")
                    select_data.Mark = 1
                    if not bool(self.api._member(by_name[name], "Select2", True, select_data)):
                        raise NativeRuntimeError(
                            "cad_selection_failed",
                            "surface_knit",
                            "A requested surface body could not be selected for Knit.",
                            details={"body": name},
                        )
                manager = self.api._member(model, "FeatureManager")
                tolerance_m = tolerance / 1000.0
                feature = self.api._member(
                    manager,
                    "InsertSewRefSurface",
                    False,
                    bool(try_form_solid),
                    True,
                    tolerance_m,
                    tolerance_m,
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "surface_knit",
                        "SOLIDWORKS did not create the Surface-Knit feature.",
                    )
                self._require_clean_rebuild(model, "surface_knit")
                after_surfaces = tuple(self.api.bodies(model, 1, False))
                after_solids = tuple(self.api.bodies(model, 0, False))
                if try_form_solid:
                    if len(after_solids) <= len(before_solids) or len(after_surfaces) >= len(before_surfaces):
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "surface_knit",
                            "Knit-to-solid body read-back does not prove a surface-to-solid conversion.",
                        )
                self._save(model, "surface_knit")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "surface_body_count_before": len(before_surfaces),
                    "surface_body_count_after": len(after_surfaces),
                    "solid_body_count_before": len(before_solids),
                    "solid_body_count_after": len(after_solids),
                    "try_form_solid": bool(try_form_solid),
                    "tolerance_mm": tolerance,
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="surface_knit",
            timeout=self._timeout(timeout),
            mutation=True,
        )

    def thicken(
        self,
        path: str | Path,
        *,
        surface_body_name: str,
        thickness_mm: float,
        side: int = 0,
        merge: bool = False,
        timeout: float | None = None,
    ) -> NativeCallResult[dict[str, Any]]:
        try:
            source = self._validate_part_path(path)
            name = str(surface_body_name).strip()
            thickness = float(thickness_mm)
            if not name:
                raise NativeRuntimeError(
                    "cad_validation_error", "surface_thicken", "surface_body_name must not be empty."
                )
            if not math.isfinite(thickness) or thickness <= 0:
                raise NativeRuntimeError(
                    "cad_validation_error", "surface_thicken", "thickness_mm must be positive and finite."
                )
            if int(side) not in {0, 1, 2}:
                raise NativeRuntimeError(
                    "cad_validation_error", "surface_thicken", "side must be 0, 1, or 2."
                )
        except Exception as exc:
            return self._local_failure(exc, "surface_thicken")

        def operation(app: Any) -> dict[str, Any]:
            model, owned = self._open_part(app, source)
            try:
                surfaces = tuple(self.api.bodies(model, 1, False))
                solids_before = tuple(self.api.bodies(model, 0, False))
                body = next((item for item in surfaces if self._body_name(item) == name), None)
                if body is None:
                    raise NativeRuntimeError(
                        "cad_precondition_failed",
                        "surface_thicken",
                        "Requested surface-body identity is not present in the part.",
                    )
                self.api._member(model, "ClearSelection2", True)
                selection_manager = self.api._member(model, "SelectionManager")
                select_data = self.api._member(selection_manager, "CreateSelectData")
                select_data.Mark = 1
                if not bool(self.api._member(body, "Select2", False, select_data)):
                    raise NativeRuntimeError(
                        "cad_selection_failed",
                        "surface_thicken",
                        "Requested surface body could not be selected for Thicken.",
                    )
                manager = self.api._member(model, "FeatureManager")
                feature = self.api._member(
                    manager,
                    "FeatureBossThicken",
                    thickness / 1000.0,
                    int(side),
                    0,
                    False,
                    bool(merge),
                    False,
                    True,
                )
                if feature is None:
                    raise NativeRuntimeError(
                        "cad_mutation_failed",
                        "surface_thicken",
                        "SOLIDWORKS did not create the Thicken feature.",
                    )
                self._require_clean_rebuild(model, "surface_thicken")
                solids_after = tuple(self.api.bodies(model, 0, False))
                if bool(merge) and solids_before:
                    if len(solids_after) < len(solids_before):
                        raise NativeRuntimeError(
                            "cad_postcondition_failed",
                            "surface_thicken",
                            "Merged Thicken unexpectedly reduced solid-body count.",
                        )
                elif len(solids_after) <= len(solids_before):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "surface_thicken",
                        "Thicken solid-body read-back did not increase.",
                    )
                self._save(model, "surface_thicken")
                return {
                    "path": source,
                    "feature_name": self.api.feature_name(feature),
                    "thickness_mm": thickness,
                    "side": int(side),
                    "solid_body_count_before": len(solids_before),
                    "solid_body_count_after": len(solids_after),
                }
            finally:
                if owned:
                    self._close_quietly(app, model)

        return self.session.execute(
            operation,
            stage="surface_thicken",
            timeout=self._timeout(timeout),
            mutation=True,
        )
