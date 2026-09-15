from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cdt_solidworks.reconstruction.models import ReconstructionStrategy, SourceClass
from cdt_solidworks.reconstruction.service import (
    ReconstructionService,
    ReconstructionUnavailableError,
    ReconstructionValidationError,
)


class _TopologyPort:
    def inspect_source(self, source_path: str) -> dict[str, object]:
        return {
            "body_count": 1,
            "face_count": 12,
            "edge_count": 24,
            "recognized_class": "prismatic_bracket",
            "dimensions_mm": {"width": 80.0, "height": 50.0, "depth": 12.0, "hole_diameter": 10.0},
            "primitives": [
                {"kind": "plane", "confidence": 1.0, "fit_residual_mm": 0.0},
                {"kind": "cylinder", "confidence": 0.995, "fit_residual_mm": 0.002},
            ],
            "unit": "mm",
            "unit_confidence": 1.0,
            "frame_confidence": 0.98,
        }


class _PartPort:
    def __init__(self) -> None:
        self.plans: list[dict[str, object]] = []
        self.edits: list[dict[str, object]] = []

    def rebuild_editable(self, output_path: str, plan: dict[str, object]) -> dict[str, object]:
        self.plans.append(plan)
        return {
            "path": output_path,
            "feature_ids": ["Sketch1", "Boss-Extrude1", "Hole1"],
            "dimensions_mm": {"width": 80.01, "height": 50.0, "depth": 12.0, "hole_diameter": 10.0},
            "reopen_verified": True,
        }

    def reopen_and_edit(self, output_path: str, edit: dict[str, object]) -> dict[str, object]:
        self.edits.append(edit)
        return {"path": output_path, "edited": True, "reopen_verified": True, "readback_mm": edit["value_mm"]}


class _DrawingPort:
    def prepare_handoff(self, output_path: str, metadata: dict[str, object]) -> dict[str, object]:
        return {"source_part": output_path, "benchmark_class": metadata["benchmark_class"], "ready": True}


class _MeshPort:
    def __init__(self, *, confidence: float = 0.97, residual_mm: float = 0.08) -> None:
        self.confidence = confidence
        self.residual_mm = residual_mm

    def inspect_mesh(self, source_path: str) -> dict[str, object]:
        return {
            "triangle_count": 1240,
            "watertight": True,
            "manifold": True,
            "recognized_class": "prismatic_bracket",
            "dimensions_mm": {"width": 80.0, "height": 50.0, "depth": 12.0, "hole_diameter": 10.0},
            "primitives": [
                {"kind": "plane", "confidence": self.confidence, "fit_residual_mm": self.residual_mm},
                {"kind": "cylinder", "confidence": self.confidence, "fit_residual_mm": self.residual_mm},
            ],
            "unit": "mm",
            "unit_confidence": 0.95,
            "frame_confidence": 0.94,
        }


def test_assess_step_hashes_source_and_preserves_unknown_design_semantics(tmp_path: Path) -> None:
    source = tmp_path / "bracket.step"
    source.write_bytes(b"controlled-step-fixture")
    service = ReconstructionService(topology_port=_TopologyPort())

    assessment = service.assess(str(source))

    assert assessment.source.source_class is SourceClass.NEUTRAL_CAD
    assert assessment.source.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert assessment.strategy is ReconstructionStrategy.PARAMETRIC_REBUILD
    assert assessment.body_count == 1
    assert assessment.topology["face_count"] == 12
    assert "original_feature_history" in assessment.missing_design_semantics
    assert assessment.recognized_class == "prismatic_bracket"


def test_step_to_editable_builds_controlled_feature_plan_and_verifies_intended_edit(tmp_path: Path) -> None:
    source = tmp_path / "bracket.stp"
    output = tmp_path / "bracket.SLDPRT"
    source.write_bytes(b"step")
    part = _PartPort()
    service = ReconstructionService(
        topology_port=_TopologyPort(),
        part_port=part,
        drawing_port=_DrawingPort(),
    )

    result = service.step_to_editable(
        str(source),
        str(output),
        benchmark_class="prismatic_bracket",
        tolerance_mm=0.05,
        intended_edit={"feature_id": "Boss-Extrude1", "parameter": "depth_mm", "value_mm": 15.0},
    )

    assert result.benchmark_class == "prismatic_bracket"
    assert result.strategy is ReconstructionStrategy.PARAMETRIC_REBUILD
    assert result.editable_feature_ids == ("Sketch1", "Boss-Extrude1", "Hole1")
    assert result.reopen_verified is True
    assert max(item.deviation_mm for item in result.dimension_ledger) == pytest.approx(0.01)
    assert result.within_tolerance is True
    assert result.intended_edit_verified is True
    assert result.drawing_handoff["ready"] is True
    assert part.plans[0]["ordinary_features_only"] is True
    assert part.edits[0]["parameter"] == "depth_mm"


def test_step_to_editable_fails_closed_when_required_port_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "shaft.step"
    source.write_bytes(b"step")
    service = ReconstructionService(topology_port=_TopologyPort(), part_port=None)

    with pytest.raises(ReconstructionUnavailableError, match="part_port"):
        service.step_to_editable(
            str(source),
            str(tmp_path / "shaft.SLDPRT"),
            benchmark_class="turned_shaft",
            tolerance_mm=0.05,
        )


def test_mesh_to_parametric_rejects_ambiguous_fit_instead_of_claiming_zero_deviation(tmp_path: Path) -> None:
    source = tmp_path / "bracket.stl"
    source.write_bytes(b"mesh")
    service = ReconstructionService(
        mesh_port=_MeshPort(confidence=0.62, residual_mm=0.7),
        part_port=_PartPort(),
    )

    with pytest.raises(ReconstructionValidationError, match="ambiguous"):
        service.mesh_to_parametric(
            str(source),
            str(tmp_path / "bracket.SLDPRT"),
            benchmark_class="prismatic_bracket",
            approximation_tolerance_mm=0.25,
        )


def test_mesh_to_parametric_reports_quantitative_deviation(tmp_path: Path) -> None:
    source = tmp_path / "bracket.stl"
    source.write_bytes(b"mesh")
    service = ReconstructionService(mesh_port=_MeshPort(), part_port=_PartPort())

    result = service.mesh_to_parametric(
        str(source),
        str(tmp_path / "bracket.SLDPRT"),
        benchmark_class="prismatic_bracket",
        approximation_tolerance_mm=0.25,
    )

    assert result.strategy is ReconstructionStrategy.APPROXIMATION
    deviations = [row.deviation_mm for row in result.dimension_ledger]
    assert any(value > 0.0 for value in deviations)
    assert max(deviations) <= 0.25


def test_compare_uses_real_dimension_delta_and_fails_outside_tolerance() -> None:
    service = ReconstructionService()
    comparison = service.compare(
        {"width": 80.0, "hole_diameter": 10.0},
        {"width": 80.03, "hole_diameter": 10.08},
        tolerance_mm=0.05,
    )

    assert comparison.max_deviation_mm == pytest.approx(0.08)
    assert comparison.within_tolerance is False
    assert comparison.ledger[1].deviation_mm == pytest.approx(0.08)
