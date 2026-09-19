"""Fail-closed reconstruction orchestration over injected topology/part/drawing ports."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from .models import (
    DimensionLedgerEntry,
    PrimitiveFit,
    ReconstructionAssessment,
    ReconstructionComparison,
    ReconstructionResult,
    ReconstructionStrategy,
    SourceClass,
    SourceIdentity,
)

_NEUTRAL_EXTENSIONS = {".step", ".stp", ".iges", ".igs", ".x_t", ".x_b"}
_MESH_EXTENSIONS = {".stl"}
_NATIVE_PART_EXTENSIONS = {".sldprt"}
_CONTROLLED_BENCHMARKS = {"prismatic_bracket", "turned_shaft"}
_MISSING_HISTORY = (
    "original_feature_history",
    "feature_intent",
    "parametric_constraints",
    "manufacturing_tolerances",
)


class ReconstructionError(RuntimeError):
    """Base reconstruction error."""


class ReconstructionValidationError(ReconstructionError):
    """Input/evidence cannot support the requested reconstruction claim."""


class ReconstructionUnavailableError(ReconstructionError):
    """A required injected provider port is unavailable."""


class ReconstructionPostconditionError(ReconstructionError):
    """A reconstruction mutation dispatched but failed verification/read-back."""


class ReconstructionService:
    """Bounded STEP/STL reconstruction without arbitrary feature-history recovery claims."""

    def __init__(
        self,
        *,
        topology_port: Any | None = None,
        part_port: Any | None = None,
        drawing_port: Any | None = None,
        mesh_port: Any | None = None,
    ) -> None:
        self.topology_port = topology_port
        self.part_port = part_port
        self.drawing_port = drawing_port
        self.mesh_port = mesh_port

    def assess(
        self,
        source_path: str,
        *,
        mesh_scale_to_mm: float | None = None,
    ) -> ReconstructionAssessment:
        source = self._source_identity(source_path)
        if source.source_class is SourceClass.NEUTRAL_CAD:
            port = self._require_port(self.topology_port, "topology_port")
            evidence = self._mapping(port.inspect_source(source.path), "topology assessment")
        elif source.source_class is SourceClass.MESH:
            port = self._require_port(self.mesh_port, "mesh_port")
            scale = (
                None
                if mesh_scale_to_mm is None
                else self._positive(mesh_scale_to_mm, "mesh_scale_to_mm")
            )
            evidence = self._mapping(
                port.inspect_mesh(source.path, scale_to_mm=scale),
                "mesh assessment",
            )
        elif source.source_class is SourceClass.NATIVE_IMPORTED_BODY:
            port = self._require_port(self.topology_port, "topology_port")
            evidence = self._mapping(port.inspect_source(source.path), "native imported-body assessment")
        else:
            raise ReconstructionValidationError("unsupported or invalid reconstruction source")
        return self._assessment_from_evidence(source, evidence)

    def step_to_editable(
        self,
        source_path: str,
        output_path: str,
        *,
        benchmark_class: str,
        tolerance_mm: float,
        intended_edit: Mapping[str, object] | None = None,
    ) -> ReconstructionResult:
        self._require_port(self.topology_port, "topology_port")
        part = self._require_port(self.part_port, "part_port")
        tolerance = self._positive(tolerance_mm, "tolerance_mm")
        benchmark = self._benchmark(benchmark_class)
        assessment = self.assess(source_path)
        if assessment.source.source_class is not SourceClass.NEUTRAL_CAD:
            raise ReconstructionValidationError("STEP-to-editable requires a neutral CAD source")
        self._require_recognized_class(assessment, benchmark)
        self._require_dimensions(assessment.dimensions_mm, benchmark)

        plan = {
            "benchmark_class": benchmark,
            "source_sha256": assessment.source.sha256,
            "source_format": assessment.source.source_format,
            "strategy": ReconstructionStrategy.PARAMETRIC_REBUILD.value,
            "ordinary_features_only": True,
            "critical_dimensions_mm": dict(assessment.dimensions_mm),
            "primitive_fits": [self._primitive_payload(item) for item in assessment.primitives],
            "missing_design_semantics": list(assessment.missing_design_semantics),
        }
        try:
            rebuild = self._mapping(
                part.rebuild_editable(self._output_path(output_path), plan),
                "editable rebuild result",
            )
        except ReconstructionError:
            raise
        except Exception as exc:
            raise ReconstructionPostconditionError("editable rebuild dispatch failed") from exc
        comparison = self.compare(
            assessment.dimensions_mm,
            self._dimensions(rebuild.get("dimensions_mm"), "editable rebuild dimensions"),
            tolerance_mm=tolerance,
        )
        if not comparison.within_tolerance:
            raise ReconstructionPostconditionError(
                f"reconstructed part exceeds tolerance: max deviation {comparison.max_deviation_mm:.6g} mm"
            )
        feature_ids = self._feature_ids(rebuild.get("feature_ids"))
        reopen_verified = rebuild.get("reopen_verified") is True
        if not reopen_verified:
            raise ReconstructionPostconditionError("editable rebuild was not verified after reopen")

        edit_verified: bool | None = None
        if intended_edit is not None:
            edit = self._intended_edit(intended_edit)
            edit_result = self._mapping(
                part.reopen_and_edit(str(rebuild.get("path") or output_path), edit),
                "intended edit result",
            )
            readback = edit_result.get("readback_mm")
            if (
                edit_result.get("edited") is not True
                or edit_result.get("reopen_verified") is not True
                or isinstance(readback, bool)
                or not isinstance(readback, (int, float))
                or not math.isclose(float(readback), float(edit["value_mm"]), rel_tol=0.0, abs_tol=1e-7)
            ):
                raise ReconstructionPostconditionError("intended parametric edit did not survive reopen/read-back")
            edit_verified = True

        drawing_handoff: Mapping[str, object] = {}
        if self.drawing_port is not None:
            drawing_handoff = self._mapping(
                self.drawing_port.prepare_handoff(
                    str(rebuild.get("path") or output_path),
                    {
                        "benchmark_class": benchmark,
                        "source_sha256": assessment.source.sha256,
                        "dimension_ledger": [self._ledger_payload(row) for row in comparison.ledger],
                    },
                ),
                "drawing handoff",
            )
        return ReconstructionResult(
            source=assessment.source,
            output_path=str(rebuild.get("path") or output_path),
            benchmark_class=benchmark,
            strategy=ReconstructionStrategy.PARAMETRIC_REBUILD,
            editable_feature_ids=feature_ids,
            dimension_ledger=comparison.ledger,
            within_tolerance=True,
            reopen_verified=True,
            intended_edit_verified=edit_verified,
            drawing_handoff=drawing_handoff,
            warnings=assessment.warnings,
        )

    def mesh_to_parametric(
        self,
        source_path: str,
        output_path: str,
        *,
        benchmark_class: str,
        approximation_tolerance_mm: float,
        mesh_scale_to_mm: float | None = None,
        intended_edit: Mapping[str, object] | None = None,
    ) -> ReconstructionResult:
        self._require_port(self.mesh_port, "mesh_port")
        part = self._require_port(self.part_port, "part_port")
        tolerance = self._positive(approximation_tolerance_mm, "approximation_tolerance_mm")
        if mesh_scale_to_mm is None:
            raise ReconstructionValidationError(
                "mesh_scale_to_mm is required because STL does not carry authoritative units"
            )
        scale = self._positive(mesh_scale_to_mm, "mesh_scale_to_mm")
        benchmark = self._benchmark(benchmark_class)
        assessment = self.assess(source_path, mesh_scale_to_mm=scale)
        if assessment.source.source_class is not SourceClass.MESH:
            raise ReconstructionValidationError("mesh-to-parametric requires an STL mesh source")
        if assessment.unit != "mm" or assessment.unit_confidence < 0.99:
            raise ReconstructionValidationError(
                "mesh unit normalization is not explicit enough for reconstruction"
            )
        self._require_recognized_class(assessment, benchmark)
        self._require_dimensions(assessment.dimensions_mm, benchmark)
        if assessment.mesh.get("watertight") is not True or assessment.mesh.get("manifold") is not True:
            raise ReconstructionValidationError("mesh is not watertight/manifold enough for bounded reconstruction")
        if not assessment.primitives:
            raise ReconstructionValidationError("mesh fit is ambiguous: no analytic primitive evidence")
        minimum_confidence = min(item.confidence for item in assessment.primitives)
        residuals = [item.fit_residual_mm for item in assessment.primitives if item.fit_residual_mm is not None]
        maximum_residual = max(residuals, default=math.inf)
        if minimum_confidence < 0.8 or maximum_residual > tolerance:
            raise ReconstructionValidationError(
                "mesh fit is ambiguous: confidence/residual does not satisfy approximation tolerance"
            )

        plan = {
            "benchmark_class": benchmark,
            "source_sha256": assessment.source.sha256,
            "source_format": assessment.source.source_format,
            "strategy": ReconstructionStrategy.APPROXIMATION.value,
            "ordinary_features_only": True,
            "critical_dimensions_mm": dict(assessment.dimensions_mm),
            "approximation_tolerance_mm": tolerance,
            "mesh_scale_to_mm": scale,
            "primitive_fits": [self._primitive_payload(item) for item in assessment.primitives],
        }
        try:
            rebuild = self._mapping(
                part.rebuild_editable(self._output_path(output_path), plan),
                "mesh reconstruction result",
            )
        except ReconstructionError:
            raise
        except Exception as exc:
            raise ReconstructionPostconditionError("mesh reconstruction dispatch failed") from exc
        comparison = self.compare(
            assessment.dimensions_mm,
            self._dimensions(rebuild.get("dimensions_mm"), "mesh reconstruction dimensions"),
            tolerance_mm=tolerance,
        )
        if not comparison.within_tolerance:
            raise ReconstructionPostconditionError(
                f"mesh reconstruction exceeds approximation tolerance: {comparison.max_deviation_mm:.6g} mm"
            )
        feature_ids = self._feature_ids(rebuild.get("feature_ids"))
        if rebuild.get("reopen_verified") is not True:
            raise ReconstructionPostconditionError("mesh reconstruction was not verified after reopen")

        edit_verified: bool | None = None
        if intended_edit is not None:
            edit = self._intended_edit(intended_edit)
            edit_result = self._mapping(
                part.reopen_and_edit(str(rebuild.get("path") or output_path), edit),
                "mesh intended edit result",
            )
            readback = edit_result.get("readback_mm")
            if (
                edit_result.get("edited") is not True
                or edit_result.get("reopen_verified") is not True
                or isinstance(readback, bool)
                or not isinstance(readback, (int, float))
                or not math.isclose(float(readback), float(edit["value_mm"]), rel_tol=0.0, abs_tol=1e-7)
            ):
                raise ReconstructionPostconditionError("mesh reconstruction edit did not survive reopen/read-back")
            edit_verified = True

        return ReconstructionResult(
            source=assessment.source,
            output_path=str(rebuild.get("path") or output_path),
            benchmark_class=benchmark,
            strategy=ReconstructionStrategy.APPROXIMATION,
            editable_feature_ids=feature_ids,
            dimension_ledger=comparison.ledger,
            within_tolerance=True,
            reopen_verified=True,
            intended_edit_verified=edit_verified,
            warnings=assessment.warnings,
        )

    def compare(
        self,
        expected_dimensions_mm: Mapping[str, float],
        actual_dimensions_mm: Mapping[str, float],
        *,
        tolerance_mm: float,
    ) -> ReconstructionComparison:
        tolerance = self._positive(tolerance_mm, "tolerance_mm")
        expected = self._dimensions(expected_dimensions_mm, "expected_dimensions_mm")
        actual = self._dimensions(actual_dimensions_mm, "actual_dimensions_mm")
        if set(expected) != set(actual):
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            raise ReconstructionValidationError(
                f"dimension identities differ; missing={missing}, extra={extra}"
            )
        ledger: list[DimensionLedgerEntry] = []
        for name, target in expected.items():
            measured = actual[name]
            deviation = abs(measured - target)
            ledger.append(
                DimensionLedgerEntry(
                    name=name,
                    expected_mm=target,
                    actual_mm=measured,
                    tolerance_mm=tolerance,
                    deviation_mm=deviation,
                    within_tolerance=deviation <= tolerance,
                )
            )
        if not ledger:
            raise ReconstructionValidationError("comparison requires at least one critical dimension")
        maximum = max(row.deviation_mm for row in ledger)
        return ReconstructionComparison(tuple(ledger), maximum, all(row.within_tolerance for row in ledger))

    def _assessment_from_evidence(
        self, source: SourceIdentity, evidence: Mapping[str, object]
    ) -> ReconstructionAssessment:
        if evidence.get("mixed_representation") is True or evidence.get("valid") is False:
            source = SourceIdentity(
                source.path,
                source.sha256,
                source.source_format,
                SourceClass.MIXED_DEGRADED,
                source.size_bytes,
            )
            strategy = ReconstructionStrategy.REFUSE
        else:
            recognized = self._optional_text(evidence.get("recognized_class"))
            if source.source_class is SourceClass.MESH:
                strategy = ReconstructionStrategy.APPROXIMATION if recognized in _CONTROLLED_BENCHMARKS else ReconstructionStrategy.REFUSE
            elif recognized in _CONTROLLED_BENCHMARKS:
                strategy = ReconstructionStrategy.PARAMETRIC_REBUILD
            elif source.source_class is SourceClass.NATIVE_IMPORTED_BODY:
                strategy = ReconstructionStrategy.FEATURE_RECOGNITION
            else:
                strategy = ReconstructionStrategy.FEATURE_RECOGNITION

        primitives = self._primitives(evidence.get("primitives"))
        dimensions = self._dimensions(evidence.get("dimensions_mm", {}), "assessment dimensions", allow_empty=True)
        unit = self._optional_text(evidence.get("unit"))
        unit_confidence = self._confidence(evidence.get("unit_confidence", 0.0), "unit_confidence")
        frame_confidence = self._confidence(evidence.get("frame_confidence", 0.0), "frame_confidence")
        body_count = evidence.get("body_count")
        if body_count is not None and (isinstance(body_count, bool) or not isinstance(body_count, int) or body_count < 0):
            raise ReconstructionValidationError("body_count must be a non-negative integer when known")
        topology = {
            key: evidence[key]
            for key in ("face_count", "edge_count", "vertex_count", "shell_count")
            if key in evidence
        }
        mesh = {
            key: evidence[key]
            for key in (
                "triangle_count",
                "watertight",
                "manifold",
                "component_count",
                "degenerate_triangle_count",
                "boundary_edge_count",
                "nonmanifold_edge_count",
                "source_scale_to_mm",
            )
            if key in evidence
        }
        warnings: list[str] = []
        if unit != "mm" or unit_confidence < 0.9:
            warnings.append("source units require explicit verification before dimensional release")
        if frame_confidence < 0.9:
            warnings.append("source coordinate frame confidence is below release threshold")
        return ReconstructionAssessment(
            source=source,
            strategy=strategy,
            unit=unit,
            unit_confidence=unit_confidence,
            frame_confidence=frame_confidence,
            body_count=body_count,
            topology=topology,
            mesh=mesh,
            primitives=primitives,
            recognized_class=self._optional_text(evidence.get("recognized_class")),
            dimensions_mm=dimensions,
            missing_design_semantics=_MISSING_HISTORY,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _source_identity(source_path: str) -> SourceIdentity:
        if not isinstance(source_path, str) or not source_path.strip():
            raise ReconstructionValidationError("source_path must be a non-empty string")
        path = Path(source_path).expanduser().resolve(strict=False)
        if not path.is_file():
            raise ReconstructionValidationError("reconstruction source file does not exist")
        suffix = path.suffix.lower()
        if suffix in _NEUTRAL_EXTENSIONS:
            source_class = SourceClass.NEUTRAL_CAD
        elif suffix in _MESH_EXTENSIONS:
            source_class = SourceClass.MESH
        elif suffix in _NATIVE_PART_EXTENSIONS:
            source_class = SourceClass.NATIVE_IMPORTED_BODY
        else:
            raise ReconstructionValidationError(f"unsupported reconstruction source format: {suffix or '<none>'}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return SourceIdentity(str(path), digest.hexdigest(), suffix.removeprefix("."), source_class, path.stat().st_size)

    @staticmethod
    def _output_path(output_path: str) -> str:
        if not isinstance(output_path, str) or not output_path.strip():
            raise ReconstructionValidationError("output_path must be a non-empty string")
        path = Path(output_path).expanduser().resolve(strict=False)
        if path.suffix.lower() != ".sldprt":
            raise ReconstructionValidationError("reconstruction output must be a .SLDPRT path")
        return str(path)

    @staticmethod
    def _require_port(port: Any | None, label: str) -> Any:
        if port is None:
            raise ReconstructionUnavailableError(f"required {label} is unavailable")
        return port

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ReconstructionValidationError(f"{label} must be a mapping")
        return value

    @staticmethod
    def _benchmark(value: str) -> str:
        if not isinstance(value, str) or value.strip() not in _CONTROLLED_BENCHMARKS:
            raise ReconstructionValidationError(
                "benchmark_class must be 'prismatic_bracket' or 'turned_shaft'"
            )
        return value.strip()

    @staticmethod
    def _require_recognized_class(assessment: ReconstructionAssessment, benchmark: str) -> None:
        if assessment.recognized_class != benchmark:
            raise ReconstructionValidationError(
                f"source classification is ambiguous for {benchmark}; observed {assessment.recognized_class!r}"
            )

    @staticmethod
    def _require_dimensions(dimensions: Mapping[str, float], benchmark: str) -> None:
        required = (
            {"width", "height", "depth"}
            if benchmark == "prismatic_bracket"
            else {"length", "outer_diameter"}
        )
        missing = sorted(required - set(dimensions))
        if missing:
            raise ReconstructionValidationError(f"critical source dimensions are missing: {missing}")

    @staticmethod
    def _feature_ids(value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)) or not value:
            raise ReconstructionValidationError("editable rebuild must return feature identities")
        result = tuple(str(item).strip() for item in value)
        if any(not item for item in result) or len(set(result)) != len(result):
            raise ReconstructionValidationError("editable feature identities must be unique and non-empty")
        return result

    @classmethod
    def _dimensions(
        cls, value: object, label: str, *, allow_empty: bool = False
    ) -> dict[str, float]:
        if not isinstance(value, Mapping):
            raise ReconstructionValidationError(f"{label} must be a mapping")
        result: dict[str, float] = {}
        for raw_name, raw_value in value.items():
            name = str(raw_name).strip()
            if not name:
                raise ReconstructionValidationError(f"{label} contains an empty dimension identity")
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ReconstructionValidationError(f"{label}.{name} must be numeric")
            numeric = float(raw_value)
            if not math.isfinite(numeric) or numeric <= 0.0:
                raise ReconstructionValidationError(f"{label}.{name} must be positive and finite")
            result[name] = numeric
        if not result and not allow_empty:
            raise ReconstructionValidationError(f"{label} must not be empty")
        return result

    @classmethod
    def _primitives(cls, value: object) -> tuple[PrimitiveFit, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise ReconstructionValidationError("primitives must be an array")
        rows: list[PrimitiveFit] = []
        for raw in value:
            if not isinstance(raw, Mapping):
                raise ReconstructionValidationError("primitive evidence must be a mapping")
            kind = cls._optional_text(raw.get("kind"))
            if kind is None:
                raise ReconstructionValidationError("primitive kind must not be empty")
            confidence = cls._confidence(raw.get("confidence", 0.0), "primitive confidence")
            residual_raw = raw.get("fit_residual_mm")
            residual = None
            if residual_raw is not None:
                if isinstance(residual_raw, bool) or not isinstance(residual_raw, (int, float)):
                    raise ReconstructionValidationError("primitive fit residual must be numeric")
                residual = float(residual_raw)
                if not math.isfinite(residual) or residual < 0.0:
                    raise ReconstructionValidationError("primitive fit residual must be finite and non-negative")
            parameters: dict[str, float | str | bool] = {}
            for key, item in raw.items():
                if key not in {"kind", "confidence", "fit_residual_mm"} and isinstance(item, (float, int, str, bool)):
                    parameters[str(key)] = item
            rows.append(PrimitiveFit(kind, confidence, residual, parameters))
        return tuple(rows)

    @staticmethod
    def _confidence(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReconstructionValidationError(f"{label} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ReconstructionValidationError(f"{label} must be in [0, 1]")
        return numeric

    @staticmethod
    def _positive(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReconstructionValidationError(f"{label} must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0.0:
            raise ReconstructionValidationError(f"{label} must be positive and finite")
        return numeric

    @classmethod
    def _intended_edit(cls, value: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise ReconstructionValidationError("intended_edit must be a mapping")
        feature_id = cls._optional_text(value.get("feature_id"))
        parameter = cls._optional_text(value.get("parameter"))
        if feature_id is None or parameter is None:
            raise ReconstructionValidationError("intended_edit requires feature_id and parameter")
        numeric = cls._positive(value.get("value_mm"), "intended_edit.value_mm")
        return {"feature_id": feature_id, "parameter": parameter, "value_mm": numeric}

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _primitive_payload(item: PrimitiveFit) -> dict[str, object]:
        return {
            "kind": item.kind,
            "confidence": item.confidence,
            "fit_residual_mm": item.fit_residual_mm,
            "parameters": dict(item.parameters),
        }

    @staticmethod
    def _ledger_payload(item: DimensionLedgerEntry) -> dict[str, object]:
        return {
            "name": item.name,
            "expected_mm": item.expected_mm,
            "actual_mm": item.actual_mm,
            "tolerance_mm": item.tolerance_mm,
            "deviation_mm": item.deviation_mm,
            "within_tolerance": item.within_tolerance,
        }
