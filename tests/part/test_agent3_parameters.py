from __future__ import annotations

import pytest

from cdt_solidworks.part.models import FeatureKind, FeatureSnapshot
from cdt_solidworks.part.runtime import DocumentTarget, MutationReceipt, RebuildResult, ResolvedDocument
from cdt_solidworks.part.service import PartContextError, PartService, PartValidationError


class ParameterRuntime:
    def __init__(self) -> None:
        self.resolved = ResolvedDocument(
            document_id="part-a",
            revision=7,
            document_type="part",
            units="mm",
            configuration="Default",
        )
        self.features = {
            "Boss1": FeatureSnapshot("Boss1", "Boss1", FeatureKind.EXTRUDE, {"depth_mm": 10.0}),
            "CutBlind": FeatureSnapshot("CutBlind", "CutBlind", FeatureKind.CUT, {"through_all": False, "depth_mm": 4.0}),
            "CutAll": FeatureSnapshot("CutAll", "CutAll", FeatureKind.CUT, {"through_all": True}),
            "Fillet1": FeatureSnapshot("Fillet1", "Fillet1", FeatureKind.FILLET, {"radius_mm": 2.0}),
        }
        self.mutations: list[tuple[str, str, float]] = []

    def resolve_document(self, target: DocumentTarget) -> ResolvedDocument:
        return self.resolved

    def get_feature(self, document: ResolvedDocument, feature_id: str) -> FeatureSnapshot | None:
        return self.features.get(feature_id)

    def set_feature_parameter(self, document: ResolvedDocument, feature_id: str, parameter: str, value: float) -> MutationReceipt:
        self.mutations.append((feature_id, parameter, value))
        current = self.features[feature_id]
        parameters = dict(current.parameters)
        parameters[parameter] = value
        self.features[feature_id] = FeatureSnapshot(
            current.feature_id,
            current.name,
            current.kind,
            parameters,
            current.suppressed,
        )
        return MutationReceipt(feature_id)

    def rebuild(self, document: ResolvedDocument) -> RebuildResult:
        return RebuildResult(ok=True)


def test_parameter_query_returns_bounded_snapshot_mapping() -> None:
    service = PartService(ParameterRuntime())
    target = DocumentTarget("part-a", 7, "mm", expected_configuration="Default")
    assert service.get_feature_parameters(target, "Boss1") == {"depth_mm": 10.0}


def test_depth_edit_is_supported_for_extrude_and_blind_cut() -> None:
    runtime = ParameterRuntime()
    service = PartService(runtime)
    target = DocumentTarget("part-a", 7, "mm", expected_configuration="Default")

    boss = service.set_feature_parameter(target, "Boss1", "depth_mm", 25.0)
    cut = service.set_feature_parameter(target, "CutBlind", "depth_mm", 6.0)

    assert boss.parameters["depth_mm"] == pytest.approx(25.0)
    assert cut.parameters["depth_mm"] == pytest.approx(6.0)
    assert runtime.mutations == [("Boss1", "depth_mm", 25.0), ("CutBlind", "depth_mm", 6.0)]


def test_parameter_edit_rejects_wrong_feature_parameter_before_dispatch() -> None:
    runtime = ParameterRuntime()
    service = PartService(runtime)
    target = DocumentTarget("part-a", 7, "mm")

    with pytest.raises(PartValidationError, match="unsupported feature parameter"):
        service.set_feature_parameter(target, "Fillet1", "depth_mm", 3.0)
    with pytest.raises(PartValidationError, match="through-all"):
        service.set_feature_parameter(target, "CutAll", "depth_mm", 3.0)

    assert runtime.mutations == []


def test_parameter_edit_rejects_wrong_configuration_before_dispatch() -> None:
    runtime = ParameterRuntime()
    service = PartService(runtime)
    target = DocumentTarget("part-a", 7, "mm", expected_configuration="Alt")

    with pytest.raises(PartContextError, match="configuration"):
        service.set_feature_parameter(target, "Boss1", "depth_mm", 20.0)

    assert runtime.mutations == []
