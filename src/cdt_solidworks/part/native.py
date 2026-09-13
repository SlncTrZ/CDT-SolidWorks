"""Part Native — Finite runtime for evidence-promoted parametric features.
Wing: Mechanical 90 | Topic: parametric-part | Updated: 2026-09-13
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Protocol, TypeVar

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.part.models import (
    BodySnapshot,
    Bounds3D,
    CutSpec,
    FeatureKind,
    FeatureSnapshot,
)
from cdt_solidworks.part.runtime import DocumentTarget, MutationReceipt, RebuildResult, ResolvedDocument

T = TypeVar("T")
_SW_END_BLIND = 0
_SW_END_THROUGH_ALL = 1
_SW_START_SKETCH_PLANE = 0


@dataclass(frozen=True, slots=True)
class NativePartBinding:
    model: Any
    document_id: str
    revision: int
    units: str
    document_type: str = "part"
    configuration: str | None = None


class SerializedPartExecutor(Protocol):
    def __call__(self, operation: Callable[[Any], T], *, stage: str, mutation: bool) -> T: ...


class PartNativeRuntime:
    """Native implementation for the currently promoted part-feature subset."""

    def __init__(
        self,
        *,
        executor: SerializedPartExecutor,
        binding_resolver: Callable[[Any, DocumentTarget], NativePartBinding],
        member: Callable[..., Any],
        feature_name: Callable[[Any], str],
        feature_type: Callable[[Any], str],
        bodies: Callable[[Any, int, bool], tuple[Any, ...]],
        body_name: Callable[[Any], str],
        rebuild_verifier: Callable[[Any], RebuildResult],
    ) -> None:
        self._execute = executor
        self._resolve_binding = binding_resolver
        self._member = member
        self._feature_name = feature_name
        self._feature_type = feature_type
        self._bodies = bodies
        self._body_name = body_name
        self._rebuild = rebuild_verifier

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        def operation(app: Any) -> ResolvedDocument:
            binding = self._resolve_binding(app, target)
            return ResolvedDocument(
                document_id=binding.document_id,
                revision=binding.revision,
                document_type=binding.document_type,
                units=binding.units,
                configuration=binding.configuration,
            )

        return self._execute(operation, stage="part_resolve_document", mutation=False)

    def create_cut(self, document: ResolvedDocument, spec: CutSpec) -> MutationReceipt:
        def operation(app: Any) -> MutationReceipt:
            binding = self._binding_from_document(app, document)
            model = binding.model
            profile = self._member(model, "FeatureByName", spec.profile.sketch_id)
            if profile is None or self._native_feature_type(profile) != "ProfileFeature":
                raise NativeRuntimeError(
                    "cad_precondition_failed",
                    "part_cut_native",
                    "The requested profile sketch could not be resolved as a native sketch feature.",
                )
            self._member(model, "ClearSelection2", True)
            if not bool(self._member(profile, "Select2", False, 0)):
                raise NativeRuntimeError(
                    "cad_selection_failed",
                    "part_cut_native",
                    "The requested profile sketch could not be selected.",
                )
            manager = self._member(model, "FeatureManager")
            end_condition = _SW_END_THROUGH_ALL if spec.through_all else _SW_END_BLIND
            depth_m = 0.0 if spec.through_all else float(spec.depth_mm or 0.0) / 1000.0
            feature = self._member(
                manager,
                "FeatureCut3",
                True,   # single direction
                False,  # do not flip side to cut
                True,   # reverse default cut direction so it follows the sketch normal
                end_condition,
                _SW_END_BLIND,
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
                False,  # normal cut: non-sheet-metal feature
                False,  # feature scope off
                True,   # auto-select affected bodies
                False,
                True,
                False,
                _SW_START_SKETCH_PLANE,
                0.0,
                False,
            )
            if feature is None:
                raise NativeRuntimeError(
                    "cad_mutation_failed",
                    "part_cut_native",
                    "SOLIDWORKS did not create the requested cut-extrude feature.",
                )
            try:
                feature.Name = spec.name
            except Exception:
                pass
            identity = self._feature_name(feature).strip()
            if not identity:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_cut_native",
                    "Created cut-extrude returned an empty feature identity.",
                )
            return MutationReceipt(identity)

        return self._execute(operation, stage="part_cut_native", mutation=True)

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        def operation(app: Any) -> RebuildResult:
            return self._rebuild(self._binding_from_document(app, document).model)

        return self._execute(operation, stage="part_rebuild_native", mutation=True)

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        def operation(app: Any) -> FeatureSnapshot | None:
            model = self._binding_from_document(app, document).model
            feature = self._member(model, "FeatureByName", feature_id)
            if feature is None:
                return None
            if self._native_feature_type(feature) != "Cut":
                return None
            definition = self._member(feature, "GetDefinition")
            if definition is None:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Cut feature definition was unavailable during native read-back.",
                )
            end_condition = int(self._member(definition, "GetEndCondition", True))
            parameters: dict[str, float | str | bool | int] = {
                "through_all": end_condition == _SW_END_THROUGH_ALL,
            }
            if end_condition == _SW_END_BLIND:
                depth_m = float(self._member(definition, "GetDepth", True))
                if not math.isfinite(depth_m) or depth_m <= 0.0:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_feature_readback",
                        "Blind cut returned an invalid native depth.",
                    )
                parameters["depth_mm"] = depth_m * 1000.0
            elif end_condition != _SW_END_THROUGH_ALL:
                raise NativeRuntimeError(
                    "cad_postcondition_failed",
                    "part_feature_readback",
                    "Cut feature returned an unaccepted end condition.",
                    details={"end_condition": end_condition},
                )
            return FeatureSnapshot(
                feature_id=self._feature_name(feature),
                name=self._feature_name(feature),
                kind=FeatureKind.CUT,
                parameters=parameters,
                suppressed=False,
            )

        return self._execute(operation, stage="part_feature_readback", mutation=False)

    def list_bodies(self, document: ResolvedDocument) -> tuple[BodySnapshot, ...]:
        def operation(app: Any) -> tuple[BodySnapshot, ...]:
            model = self._binding_from_document(app, document).model
            snapshots = []
            for index, body in enumerate(self._bodies(model, 0, False)):
                raw = self._as_tuple(self._member(body, "GetBodyBox"))
                if len(raw) != 6:
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_body_readback",
                        "Native body bounding box did not contain six values.",
                    )
                values = tuple(float(value) * 1000.0 for value in raw)
                if not all(math.isfinite(value) for value in values):
                    raise NativeRuntimeError(
                        "cad_postcondition_failed",
                        "part_body_readback",
                        "Native body bounding box contained a non-finite value.",
                    )
                name = self._body_name(body).strip() or f"body-{index + 1}"
                snapshots.append(
                    BodySnapshot(
                        name,
                        Bounds3D(*values),
                    )
                )
            return tuple(snapshots)

        return self._execute(operation, stage="part_body_readback", mutation=False)

    def list_features(self, document: ResolvedDocument) -> tuple[FeatureSnapshot, ...]:
        raise NativeRuntimeError(
            "unsupported_native_operation",
            "part_features_list",
            "General parametric feature inspection is not promoted by this runtime yet.",
        )

    def _native_feature_type(self, feature: Any) -> str:
        type_name = self._feature_type(feature)
        if type_name == "ICE":
            try:
                underlying = str(self._member(feature, "GetTypeName") or "").strip()
            except Exception:
                underlying = ""
            if underlying:
                return underlying
        return type_name

    def _binding_from_document(self, app: Any, document: ResolvedDocument) -> NativePartBinding:
        binding = self._resolve_binding(
            app,
            DocumentTarget(document.document_id, document.revision, document.units),
        )
        if binding.document_id != document.document_id or binding.units != document.units:
            raise NativeRuntimeError(
                "document_context_mismatch",
                "part_native_binding",
                "Native part identity or units changed during the operation.",
            )
        return binding

    @staticmethod
    def _as_tuple(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, (tuple, list)):
            return tuple(value)
        return (value,)
