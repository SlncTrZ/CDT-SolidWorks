from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.evaluation.domain import EvaluationService
from cdt_solidworks.evaluation.native import SolidWorksEvaluationAdapter
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("CDT_SW_NATIVE_EVIDENCE_ROOT"),
    reason="native SOLIDWORKS evidence requires Windows and CDT_SW_NATIVE_EVIDENCE_ROOT",
)


def _require_success(result, label: str):
    assert result.state is NativeCallState.SUCCESS, (
        label,
        result.state,
        result.failure,
    )
    assert result.value is not None
    return result.value


def test_native_mass_bounds_and_geometry_sanity() -> None:
    root = Path(os.environ["CDT_SW_NATIVE_EVIDENCE_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    part = root / "evaluation-fixture.SLDPRT"
    evidence = root / "evaluation-native.json"
    for path in (part, evidence):
        if path.exists():
            path.unlink()

    policy = DocumentPathPolicy((root,))
    session = SolidWorksSession()
    try:
        session_info = _require_success(
            session.connect(
                policy=AttachPolicy.ATTACH_OR_START,
                version=2024,
                visible=True,
                timeout=120.0,
            ),
            "connect",
        )
        fixture = _require_success(
            CadCoreService(
                session,
                path_policy=policy,
                default_timeout=90.0,
            ).create_rect_extrude(
                part,
                width_mm=100.0,
                height_mm=60.0,
                depth_mm=20.0,
                timeout=90.0,
            ),
            "create_rect_extrude",
        )

        service = EvaluationService(
            SolidWorksEvaluationAdapter(
                session,
                path_policy=policy,
                default_timeout=60.0,
            )
        )
        mass = service.mass_properties(str(part), "Default")
        bounds = service.bounding_box(str(part), "Default")
        sanity = service.require_clean_geometry(str(part), "Default")

        expected_volume_m3 = 0.100 * 0.060 * 0.020
        assert math.isclose(mass.volume_m3, expected_volume_m3, rel_tol=0.0, abs_tol=1e-9)
        assert mass.mass_kg > 0.0
        assert mass.surface_area_m2 > 0.0
        assert all(math.isfinite(value) for value in mass.center_of_mass_m)
        assert all(math.isfinite(value) for value in mass.inertia_kg_m2)
        assert bounds.approximate is True
        spans = sorted(high - low for low, high in zip(bounds.min_m, bounds.max_m, strict=True))
        expected_spans = sorted((0.100, 0.060, 0.020))
        assert all(
            math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-6)
            for actual, expected in zip(spans, expected_spans, strict=True)
        )
        assert sanity.solid_body_count == 1
        assert sanity.surface_body_count == 0
        assert sanity.feature_error_count == 0
        assert part.is_file() and part.stat().st_size > 0

        evidence.write_text(
            json.dumps(
                {
                    "host": os.environ.get("COMPUTERNAME"),
                    "solidworks_revision": session_info.revision,
                    "solidworks_version_year": session_info.version_year,
                    "session_ownership": session_info.ownership.value,
                    "fixture": fixture,
                    "part_bytes": part.stat().st_size,
                    "mass": {
                        "mass_kg": mass.mass_kg,
                        "volume_m3": mass.volume_m3,
                        "surface_area_m2": mass.surface_area_m2,
                        "center_of_mass_m": mass.center_of_mass_m,
                        "inertia_kg_m2": mass.inertia_kg_m2,
                    },
                    "bounds": {
                        "min_m": bounds.min_m,
                        "max_m": bounds.max_m,
                        "approximate": bounds.approximate,
                    },
                    "geometry_sanity": {
                        "solid_body_count": sanity.solid_body_count,
                        "surface_body_count": sanity.surface_body_count,
                        "component_count": sanity.component_count,
                        "feature_error_count": sanity.feature_error_count,
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        assert evidence.is_file() and evidence.stat().st_size > 0
    finally:
        session.disconnect(timeout=10.0)
        session.close_dispatcher(timeout=3.0)
