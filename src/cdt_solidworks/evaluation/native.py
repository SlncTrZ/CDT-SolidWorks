"""Lane-local SOLIDWORKS COM adapter for bounded engineering evaluation."""

from __future__ import annotations

import math
from pathlib import PureWindowsPath
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState

from .domain import (
    BoundingBox,
    EvaluationPostconditionError,
    EvaluationRefusal,
    GeometrySanity,
    InterferenceSnapshot,
    MassProperties,
    MeasureSnapshot,
)


T = TypeVar("T")

_STANDARD_PLANE_INDEX = {"front": 0, "top": 1, "right": 2}


class _EvaluationNativeError(NativeRuntimeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(
            code,
            "evaluation_native",
            code if detail is None else f"{code}: {detail}",
        )


class ReadPathPolicy(Protocol):
    def validate_open(self, path: str) -> str: ...


class SolidWorksEvaluationAdapter:
    """Reads native engineering facts on the session-owned COM apartment.

    ``document_id`` is deliberately a native document path in this lane-local adapter.
    Integration may later bind a provider document identity to that path without changing
    these engineering semantics.
    """

    _DOC_TYPES = {".sldprt": 1, ".sldasm": 2}

    def __init__(
        self,
        session: Any,
        *,
        path_policy: ReadPathPolicy,
        default_timeout: float = 30.0,
        max_features: int = 100_000,
    ) -> None:
        self._session = session
        self._api = session.api
        self._path_policy = path_policy
        self._default_timeout = float(default_timeout)
        self._max_features = int(max_features)

    def mass_properties(
        self, document_id: str, configuration: str | None = None
    ) -> MassProperties:
        def read(model: Any, doc_type: int) -> MassProperties:
            extension = self._api._member(model, "Extension")
            mass_property = self._api._member(extension, "CreateMassProperty2")
            if mass_property is None:
                raise EvaluationPostconditionError("mass_property_api_unavailable")
            mass_property.UseSystemUnits = True
            center = self._float_tuple(
                self._api._member(mass_property, "CenterOfMass"), 3, "center_of_mass"
            )
            inertia = self._float_tuple(
                self._api._member(mass_property, "GetMomentOfInertia", 0),
                9,
                "moment_of_inertia",
            )
            return MassProperties(
                mass_kg=float(self._api._member(mass_property, "Mass")),
                volume_m3=float(self._api._member(mass_property, "Volume")),
                surface_area_m2=float(self._api._member(mass_property, "SurfaceArea")),
                center_of_mass_m=(center[0], center[1], center[2]),
                inertia_kg_m2=(
                    inertia[0],
                    inertia[4],
                    inertia[8],
                    inertia[1],
                    inertia[2],
                    inertia[5],
                ),
            )

        return self._with_document(
            document_id,
            configuration,
            stage="evaluation_mass_properties",
            reader=read,
        )

    def bounding_box(
        self, document_id: str, configuration: str | None = None
    ) -> BoundingBox:
        def read(model: Any, doc_type: int) -> BoundingBox:
            if doc_type == 1:
                bodies = tuple(self._api.bodies(model, 0, False)) + tuple(
                    self._api.bodies(model, 1, False)
                )
                boxes = tuple(
                    self._float_tuple(
                        self._api._member(body, "GetBodyBox"), 6, "body_box"
                    )
                    for body in bodies
                )
            else:
                components = tuple(self._api.components(model, False))
                boxes = tuple(
                    self._float_tuple(
                        self._api._member(component, "GetBox", False, False),
                        6,
                        "component_box",
                    )
                    for component in components
                    if not self._api.component_suppressed(component)
                )
            if not boxes:
                raise EvaluationPostconditionError("bounding_box_unavailable")
            return BoundingBox(
                min_m=(
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    min(box[2] for box in boxes),
                ),
                max_m=(
                    max(box[3] for box in boxes),
                    max(box[4] for box in boxes),
                    max(box[5] for box in boxes),
                ),
                approximate=True,
            )

        return self._with_document(
            document_id,
            configuration,
            stage="evaluation_bounding_box",
            reader=read,
        )

    def geometry_sanity(
        self, document_id: str, configuration: str | None = None
    ) -> GeometrySanity:
        def read(model: Any, doc_type: int) -> GeometrySanity:
            if doc_type == 1:
                solid_count = len(self._api.bodies(model, 0, False))
                surface_count = len(self._api.bodies(model, 1, False))
                component_count = 0
            else:
                solid_count = 0
                surface_count = 0
                component_count = len(self._api.components(model, False))

            feature_errors = 0
            feature = self._api.first_feature(model)
            visited = 0
            while feature is not None:
                visited += 1
                if visited > self._max_features:
                    raise EvaluationPostconditionError("feature_scan_limit_exceeded")
                code, warning = self._api.feature_error(feature)
                if int(code) != 0 and not bool(warning):
                    feature_errors += 1
                feature = self._api.next_feature(feature)
            return GeometrySanity(
                solid_body_count=solid_count,
                surface_body_count=surface_count,
                component_count=component_count,
                feature_error_count=feature_errors,
            )

        return self._with_document(
            document_id,
            configuration,
            stage="evaluation_geometry_sanity",
            reader=read,
        )

    def measure(
        self, document_id: str, refs: tuple[str, ...]
    ) -> MeasureSnapshot:
        refs = tuple(refs)
        if len(refs) not in (1, 2):
            raise EvaluationRefusal("invalid_measurement_reference_count")
        for reference in refs:
            self._parse_measure_reference(reference)

        def read(model: Any, doc_type: int) -> MeasureSnapshot:
            try:
                extension = self._api._member(model, "Extension")
                measure = self._api._member(extension, "CreateMeasure")
            except Exception as exc:
                raise _EvaluationNativeError("measure_create_failed") from exc
            if measure is None:
                raise _EvaluationNativeError("measure_api_unavailable")
            self._api._member(model, "ClearSelection2", True)
            try:
                for index, reference in enumerate(refs):
                    self._select_measure_reference(
                        model, reference, append=index > 0
                    )
                try:
                    calculated = bool(self._api._member(measure, "Calculate"))
                except Exception as exc:
                    raise _EvaluationNativeError(
                        "measure_calculate_dispatch_failed",
                        f"{type(exc).__name__}: {exc}",
                    ) from exc
                if not calculated:
                    raise _EvaluationNativeError("measurement_calculation_failed")
                try:
                    radius = self._measure_value(measure, "Radius")
                    diameter = self._measure_value(measure, "Diameter")
                    if radius is None and diameter is not None:
                        radius = diameter / 2.0
                    elif diameter is None and radius is not None:
                        diameter = radius * 2.0
                    return MeasureSnapshot(
                        distance_m=self._measure_value(measure, "Distance"),
                        angle_rad=self._measure_value(measure, "Angle"),
                        radius_m=radius,
                        diameter_m=diameter,
                    )
                except Exception as exc:
                    raise _EvaluationNativeError("measure_readback_failed") from exc
            finally:
                self._api._member(model, "ClearSelection2", True)

        return self._with_document(
            document_id,
            None,
            stage="evaluation_measure",
            reader=read,
        )

    def interferences(
        self, assembly_id: str, configuration: str | None = None
    ) -> tuple[InterferenceSnapshot, ...]:
        def read(model: Any, doc_type: int) -> tuple[InterferenceSnapshot, ...]:
            if doc_type != 2:
                raise EvaluationRefusal("document_not_assembly", assembly_id)
            components = tuple(self._api.components(model, False))
            suppressed = {
                self._api.component_name(component)
                for component in components
                if self._api.component_suppressed(component)
            }
            manager = self._api._member(model, "InterferenceDetectionManager")
            if manager is None:
                raise EvaluationPostconditionError(
                    "interference_manager_unavailable"
                )
            manager.TreatCoincidenceAsInterference = False
            manager.IgnoreHiddenBodies = True
            manager.TreatSubAssembliesAsComponents = False
            manager.UseTransform = True
            try:
                raw = self._api._member(manager, "GetInterferences")
                rows = () if raw is None else (
                    tuple(raw) if isinstance(raw, (tuple, list)) else (raw,)
                )
                result: list[InterferenceSnapshot] = []
                for index, row in enumerate(rows):
                    pair_raw = self._api._member(row, "Components")
                    pair = () if pair_raw is None else (
                        tuple(pair_raw)
                        if isinstance(pair_raw, (tuple, list))
                        else (pair_raw,)
                    )
                    if len(pair) != 2:
                        raise EvaluationPostconditionError(
                            "invalid_interference_component_pair"
                        )
                    names = tuple(
                        sorted(self._api.component_name(component) for component in pair)
                    )
                    if (
                        not names[0]
                        or not names[1]
                        or names[0] == names[1]
                        or names[0] in suppressed
                        or names[1] in suppressed
                    ):
                        raise EvaluationPostconditionError(
                            "invalid_interference_component_pair"
                        )
                    volume = float(self._api._member(row, "Volume"))
                    if not math.isfinite(volume) or volume <= 0:
                        raise EvaluationPostconditionError(
                            "invalid_interference_volume"
                        )
                    result.append(
                        InterferenceSnapshot(
                            identity=f"{names[0]}|{names[1]}|{index}",
                            component_a=names[0],
                            component_b=names[1],
                            volume_m3=volume,
                        )
                    )
                return tuple(result)
            finally:
                self._api._member(manager, "Done")

        return self._with_document(
            assembly_id,
            configuration,
            stage="evaluation_interference",
            reader=read,
        )

    def _with_document(
        self,
        document_id: str,
        configuration: str | None,
        *,
        stage: str,
        reader: Callable[[Any, int], T],
    ) -> T:
        source = self._path_policy.validate_open(document_id)
        extension = PureWindowsPath(source).suffix.casefold()
        doc_type = self._DOC_TYPES.get(extension)
        if doc_type is None:
            raise EvaluationRefusal("unsupported_document_extension", extension or "<none>")

        def operation(app: Any) -> T:
            model = self._api.get_open_document(app, source)
            owned = model is None
            if model is None:
                model, errors, warnings = self._api.open_document(
                    app,
                    source,
                    doc_type,
                    read_only=True,
                    silent=True,
                    configuration=configuration or "",
                )
                if model is None or int(errors) != 0:
                    raise EvaluationPostconditionError(
                        "document_open_failed", f"errors={errors}, warnings={warnings}"
                    )
            try:
                if int(self._api.document_type(model)) != doc_type:
                    raise EvaluationPostconditionError("document_type_mismatch")
                if configuration is not None:
                    active = self._api.active_configuration(model)
                    if active != configuration:
                        raise EvaluationPostconditionError(
                            "configuration_mismatch", str(active)
                        )
                return reader(model, doc_type)
            finally:
                if owned:
                    title = str(self._api._member(model, "GetTitle") or "")
                    if title:
                        self._api.close_document(app, title)

        result = self._session.execute(
            operation,
            stage=stage,
            timeout=self._default_timeout,
            mutation=False,
        )
        if result.state is not NativeCallState.SUCCESS or result.value is None:
            detail = result.state.value
            if result.failure is not None:
                detail = (
                    f"{result.failure.code}@{result.failure.stage}: "
                    f"{result.failure.message}"
                )
            raise EvaluationPostconditionError("native_evaluation_failed", detail)
        return result.value

    def _select_measure_reference(
        self, model: Any, reference: str, *, append: bool
    ) -> None:
        kind, name, _index = self._parse_measure_reference(reference)
        try:
            if kind == "plane":
                target = self._standard_plane_feature(model, name)
            else:
                target = self._resolve_measure_reference(model, reference)
            selected = bool(
                self._api._member(target, "Select2", bool(append), 0)
            )
        except (EvaluationRefusal, EvaluationPostconditionError) as exc:
            raise _EvaluationNativeError(
                "measure_reference_resolution_failed", str(exc)
            ) from exc
        except Exception as exc:
            raise _EvaluationNativeError(
                "measure_reference_select_failed", reference
            ) from exc
        if not selected:
            raise _EvaluationNativeError(
                "measure_reference_select_failed", reference
            )

    def _resolve_measure_reference(self, model: Any, reference: str) -> Any:
        kind, name, index = self._parse_measure_reference(reference)
        if kind == "plane":
            feature = self._standard_plane_feature(model, name)
            entity = self._api._member(feature, "GetSpecificFeature2")
            if entity is None:
                raise EvaluationPostconditionError(
                    "measure_reference_resolution_failed", reference
                )
            return entity

        feature = self._feature(model, name)
        sketch = self._api._member(feature, "GetSpecificFeature2")
        if sketch is None:
            raise EvaluationPostconditionError(
                "measure_sketch_missing", name
            )
        member = "GetSketchSegments" if kind == "segment" else "GetSketchPoints2"
        value = self._api._member(sketch, member)
        entities = () if value is None else (
            tuple(value) if isinstance(value, (tuple, list)) else (value,)
        )
        assert index is not None
        if index >= len(entities):
            raise EvaluationPostconditionError(
                "measure_reference_index_out_of_range", reference
            )
        return entities[index]

    def _feature(self, model: Any, name: str) -> Any:
        feature = self._api.first_feature(model)
        visited = 0
        matches: list[Any] = []
        while feature is not None:
            visited += 1
            if visited > self._max_features:
                raise EvaluationPostconditionError("feature_scan_limit_exceeded")
            if self._api.feature_name(feature) == name:
                matches.append(feature)
            feature = self._api.next_feature(feature)
        if not matches:
            raise EvaluationPostconditionError("measure_feature_missing", name)
        if len(matches) > 1:
            raise EvaluationPostconditionError("measure_feature_ambiguous", name)
        return matches[0]

    def _standard_plane_feature(self, model: Any, plane: str) -> Any:
        target = _STANDARD_PLANE_INDEX[plane]
        index = 0
        feature = self._api.first_feature(model)
        visited = 0
        while feature is not None:
            visited += 1
            if visited > self._max_features:
                raise EvaluationPostconditionError("feature_scan_limit_exceeded")
            if self._api.feature_type(feature) == "RefPlane":
                if index == target:
                    return feature
                index += 1
            feature = self._api.next_feature(feature)
        raise EvaluationPostconditionError("standard_plane_missing", plane)

    @staticmethod
    def _parse_measure_reference(reference: str) -> tuple[str, str, int | None]:
        if not isinstance(reference, str) or not reference.strip():
            raise EvaluationRefusal("invalid_measurement_reference")
        parts = reference.split(":")
        if (
            len(parts) == 2
            and parts[0] == "plane"
            and parts[1] in _STANDARD_PLANE_INDEX
        ):
            return "plane", parts[1], None
        if (
            len(parts) == 4
            and parts[0] == "sketch"
            and parts[1].strip()
            and parts[2] in {"segment", "point"}
        ):
            try:
                index = int(parts[3])
            except ValueError as exc:
                raise EvaluationRefusal(
                    "unsupported_measure_reference", reference
                ) from exc
            if index < 0:
                raise EvaluationRefusal(
                    "unsupported_measure_reference", reference
                )
            return parts[2], parts[1], index
        raise EvaluationRefusal("unsupported_measure_reference", reference)

    def _measure_value(self, measure: Any, member: str) -> float | None:
        try:
            value = float(self._api._member(measure, member))
        except (TypeError, ValueError, AttributeError):
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return value

    @staticmethod
    def _float_tuple(value: Any, expected: int, label: str) -> tuple[float, ...]:
        if value is None:
            raise EvaluationPostconditionError(f"{label}_missing")
        values = tuple(float(item) for item in value)
        if len(values) != expected or not all(math.isfinite(item) for item in values):
            raise EvaluationPostconditionError(f"invalid_{label}")
        return values
