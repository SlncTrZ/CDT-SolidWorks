"""Lane-local native sheet-metal adapter for base-flange and flat-pattern workflows."""

from __future__ import annotations

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

    @staticmethod
    def unsupported_edge_flange_reason() -> str:
        return (
            "Native Edge Flange requires a persistent edge-identity resolver; selection-name-only "
            "automation is intentionally not exposed because it is not deterministic across rebuilds."
        )
