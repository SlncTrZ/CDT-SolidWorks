"""Lane-local SOLIDWORKS COM adapter for bounded engineering evaluation."""

from __future__ import annotations

import math
from pathlib import PureWindowsPath
from typing import Any, Callable, Protocol, TypeVar

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
                        self._api._member(component, "GetBox"), 6, "component_box"
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
        self, document_id: str, first_ref: str, second_ref: str
    ) -> MeasureSnapshot:
        raise EvaluationRefusal(
            "unsupported_capability", "solidworks.evaluation.measure_requires_identity_binding"
        )

    def interferences(
        self, assembly_id: str, configuration: str | None = None
    ) -> tuple[InterferenceSnapshot, ...]:
        raise EvaluationRefusal(
            "unsupported_capability",
            "solidworks.evaluation.interference_requires_component_identity_binding",
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
                detail = f"{result.failure.code}@{result.failure.stage}"
            raise EvaluationPostconditionError("native_evaluation_failed", detail)
        return result.value

    @staticmethod
    def _float_tuple(value: Any, expected: int, label: str) -> tuple[float, ...]:
        if value is None:
            raise EvaluationPostconditionError(f"{label}_missing")
        values = tuple(float(item) for item in value)
        if len(values) != expected or not all(math.isfinite(item) for item in values):
            raise EvaluationPostconditionError(f"invalid_{label}")
        return values
