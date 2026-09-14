"""Real-SOLIDWORKS evidence runner for M95 Agent-C evaluation capability."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.evaluation.domain import EvaluationService
from cdt_solidworks.evaluation.native import SolidWorksEvaluationAdapter
from cdt_solidworks.native import AttachPolicy, NativeCallState, SolidWorksSession
from cdt_solidworks.native.cad_core import CadCoreService


def _require(result: Any, stage: str) -> Any:
    if result.state is not NativeCallState.SUCCESS:
        failure = result.failure
        raise RuntimeError(
            f"{stage} failed: state={result.state.value}; "
            f"code={getattr(failure, 'code', None)}; "
            f"message={getattr(failure, 'message', None)}"
        )
    return result.value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _add_circle_sketch(session: SolidWorksSession, part_path: str) -> str:
    def operation(app: Any) -> str:
        model = session.api.get_open_document(app, part_path)
        owned = model is None
        if model is None:
            model, errors, warnings = session.api.open_document(
                app,
                part_path,
                1,
                read_only=False,
                silent=True,
                configuration="",
            )
            if model is None or int(errors) != 0:
                raise RuntimeError(
                    f"circle fixture open failed errors={errors} warnings={warnings}"
                )
        try:
            feature = session.api.first_feature(model)
            ref_plane = None
            while feature is not None:
                if session.api.feature_type(feature) == "RefPlane":
                    ref_plane = feature
                    break
                feature = session.api.next_feature(feature)
            if ref_plane is None:
                raise RuntimeError("front plane missing")
            session.api._member(model, "ClearSelection2", True)
            if not bool(session.api._member(ref_plane, "Select2", False, 0)):
                raise RuntimeError("front plane selection failed")
            sketch_manager = session.api._member(model, "SketchManager")
            session.api._member(sketch_manager, "InsertSketch", True)
            circle = session.api._member(
                sketch_manager,
                "CreateCircleByRadius",
                0.0,
                0.0,
                0.0,
                0.005,
            )
            if circle is None:
                raise RuntimeError("circle creation failed")
            session.api._member(sketch_manager, "InsertSketch", True)
            found = None
            feature = session.api.first_feature(model)
            while feature is not None:
                if session.api.feature_type(feature) == "ProfileFeature":
                    found = feature
                feature = session.api.next_feature(feature)
            if found is None:
                raise RuntimeError("circle sketch feature missing")
            success, errors, warnings = session.api.save_document(model)
            if not success or int(errors) != 0:
                raise RuntimeError(
                    f"circle fixture save failed errors={errors} warnings={warnings}"
                )
            return session.api.feature_name(found)
        finally:
            if owned:
                session.api.close_document(app, session.api.document_title(model))

    return str(
        _require(
            session.execute(
                operation,
                stage="evaluation_evidence_circle_sketch",
                timeout=60.0,
                mutation=True,
            ),
            "add_circle_sketch",
        )
    )


def run_evaluation_evidence(
    session: SolidWorksSession, root: Path
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    policy = DocumentPathPolicy((root,))
    cad = CadCoreService(session, path_policy=policy, default_timeout=90.0)

    part_a = root / "evaluation-a.SLDPRT"
    part_b = root / "evaluation-b.SLDPRT"
    overlap_assembly = root / "interference-overlap.SLDASM"
    clear_assembly = root / "interference-clear.SLDASM"

    created_a = _require(
        cad.create_rect_extrude(
            part_a,
            width_mm=80.0,
            height_mm=50.0,
            depth_mm=20.0,
        ),
        "create_part_a",
    )
    _require(
        cad.create_rect_extrude(
            part_b,
            width_mm=40.0,
            height_mm=40.0,
            depth_mm=20.0,
        ),
        "create_part_b",
    )
    circle_sketch = _add_circle_sketch(session, str(part_a.resolve()))

    _require(
        cad.create_assembly(
            overlap_assembly,
            component_paths=(str(part_a.resolve()), str(part_b.resolve())),
            placements_mm=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        ),
        "create_overlap_assembly",
    )
    _require(
        cad.create_assembly(
            clear_assembly,
            component_paths=(str(part_a.resolve()), str(part_b.resolve())),
            placements_mm=((0.0, 0.0, 0.0), (250.0, 0.0, 0.0)),
        ),
        "create_clear_assembly",
    )

    service = EvaluationService(
        SolidWorksEvaluationAdapter(
            session,
            path_policy=policy,
            default_timeout=60.0,
        )
    )

    sketch_name = str(created_a["sketch_name"])
    distance = service.measure(
        str(part_a.resolve()),
        f"sketch:{sketch_name}:point:0",
        f"sketch:{sketch_name}:point:1",
    )
    if distance.distance_m is None or distance.distance_m <= 0:
        raise RuntimeError(f"distance measurement invalid: {distance!r}")

    angle = service.measure(
        str(part_a.resolve()),
        "plane:front",
        "plane:right",
    )
    if angle.angle_rad is None or not math.isclose(
        angle.angle_rad, math.pi / 2.0, rel_tol=0.0, abs_tol=1e-8
    ):
        raise RuntimeError(f"angle measurement invalid: {angle!r}")

    circle = service.measure(
        str(part_a.resolve()),
        f"sketch:{circle_sketch}:segment:0",
    )
    if circle.radius_m is None or circle.diameter_m is None:
        raise RuntimeError(f"circle measurement missing: {circle!r}")
    if not math.isclose(circle.radius_m, 0.005, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(f"radius mismatch: {circle!r}")
    if not math.isclose(circle.diameter_m, 0.010, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(f"diameter mismatch: {circle!r}")

    overlap = service.interferences(str(overlap_assembly.resolve()), "Default")
    if not overlap:
        raise RuntimeError("expected at least one interference")
    if any(item.volume_m3 <= 0 for item in overlap):
        raise RuntimeError(f"non-positive interference volume: {overlap!r}")

    clear = service.interferences(str(clear_assembly.resolve()), "Default")
    if clear:
        raise RuntimeError(f"expected no interference: {clear!r}")

    return {
        "part": str(part_a.resolve()),
        "part_sha256": _sha256(part_a),
        "sketch_name": sketch_name,
        "circle_sketch": circle_sketch,
        "distance_m": distance.distance_m,
        "angle_rad": angle.angle_rad,
        "radius_m": circle.radius_m,
        "diameter_m": circle.diameter_m,
        "overlap_assembly": str(overlap_assembly.resolve()),
        "overlap_sha256": _sha256(overlap_assembly),
        "interferences": [
            {
                "identity": item.identity,
                "component_a": item.component_a,
                "component_b": item.component_b,
                "volume_m3": item.volume_m3,
            }
            for item in overlap
        ],
        "clear_assembly": str(clear_assembly.resolve()),
        "clear_sha256": _sha256(clear_assembly),
        "clear_interference_count": len(clear),
    }


def main() -> None:
    root = Path(
        os.environ.get(
            "CDT_SW_M95_C_EVALUATION_ROOT",
            str(Path(os.environ.get("TEMP", r"C:\Temp")) / "cdt-sw-m95-c-evaluation"),
        )
    ).resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    report_path = Path(__file__).with_name("native_evidence_report.json")
    session = SolidWorksSession()
    connected = None
    report: dict[str, Any] = {
        "ok": False,
        "verdict": "FAIL",
        "root": str(root),
    }
    try:
        connected = _require(
            session.connect(
                policy=AttachPolicy.START_NEW,
                version=2024,
                visible=False,
                timeout=180.0,
            ),
            "connect",
        )
        evidence = run_evaluation_evidence(session, root)
        report.update(
            ok=True,
            verdict="PASS",
            solidworks_revision=connected.revision,
            solidworks_version_year=connected.version_year,
            session_ownership=connected.ownership.value,
            evaluation=evidence,
        )
    except BaseException as exc:
        report.update(
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        if connected is not None:
            report.update(
                solidworks_revision=connected.revision,
                solidworks_version_year=connected.version_year,
                session_ownership=connected.ownership.value,
            )
        raise
    finally:
        try:
            session.disconnect(timeout=15.0)
        except Exception as exc:
            report["disconnect_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            session.close_dispatcher(timeout=5.0)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
