"""Agent A Part P0 Native Gate — Final-HEAD SOLIDWORKS evidence for promoted part breadth.
Wing: Mechanical 95 | Topic: agent-a-part-p0-native-acceptance | Updated: 2026-09-14 12:20
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TEST_PART = Path(__file__).resolve().parent
for path in (SRC, TEST_PART):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from native_merge_gate import NativeHarness, _git_optional, _require_success, _sha256

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.part.models import (
    ChamferSpec,
    CircularPatternSpec,
    DraftSpec,
    FeatureKind,
    FilletSpec,
    HoleSpec,
    LinearPatternSpec,
    MirrorSpec,
    PartPostconditions,
    ProfileRef,
    ReferenceAxisSpec,
    ReferencePlaneSpec,
    ReferencePointSpec,
    RibSpec,
    ShellSpec,
)
from cdt_solidworks.part.runtime import DocumentTarget
from cdt_solidworks.part.service import PartService

_OFFICIAL_RIB_SAMPLE = Path(
    r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2024\samples\tutorial\api\block20.sldprt"
)


def _target(harness: NativeHarness, path: Path, stage: str) -> DocumentTarget:
    return DocumentTarget(
        str(path.resolve()),
        harness.current_revision(path, stage),
        "mm",
    )


def _assert_close(actual: float, expected: float, label: str, *, tolerance: float = 1e-6) -> None:
    if not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance):
        raise RuntimeError(f"{label} mismatch: expected={expected} actual={actual}")


def _block(harness: NativeHarness, artifact_dir: Path, name: str, *, plane: str = "front") -> Path:
    artifact = artifact_dir / f"{name}.SLDPRT"
    artifact.unlink(missing_ok=True)
    cad = CadCoreService(
        harness.session,
        path_policy=DocumentPathPolicy((artifact_dir,)),
        default_timeout=120.0,
    )
    _require_success(
        cad.create_rect_extrude(
            artifact,
            width_mm=100.0,
            height_mm=60.0,
            depth_mm=30.0,
            plane=plane,
            timeout=120.0,
        ),
        f"{name}_base",
    )
    harness.reopen(artifact, f"{name}_open")
    return artifact


def _persisted_feature(
    harness: NativeHarness,
    artifact: Path,
    feature_id: str,
    stage: str,
) -> Any:
    harness.save_close(artifact, f"{stage}_save")
    revision = harness.reopen(artifact, f"{stage}_reopen")
    snapshot = PartService(harness.part_runtime()).get_feature(
        DocumentTarget(str(artifact.resolve()), revision, "mm"),
        feature_id,
    )
    if snapshot is None:
        raise RuntimeError(f"{stage} feature missing after save/reopen: {feature_id}")
    return snapshot


def _feature_case(
    harness: NativeHarness,
    artifact_dir: Path,
    name: str,
    method: str,
    spec: Any,
    expected_kind: FeatureKind,
    expected_parameters: dict[str, float | bool | int],
) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, name)
    service = PartService(harness.part_runtime())
    created = getattr(service, method)(
        _target(harness, artifact, f"{name}_revision"),
        spec,
        postconditions=PartPostconditions(body_count=1),
    )
    persisted = _persisted_feature(harness, artifact, created.feature.feature_id, name)
    if persisted.kind is not expected_kind:
        raise RuntimeError(
            f"{name} kind mismatch after reopen: expected={expected_kind.value} actual={persisted.kind.value}"
        )
    for key, expected in expected_parameters.items():
        actual = persisted.parameters.get(key)
        if isinstance(expected, bool) or isinstance(expected, int):
            if actual != expected:
                raise RuntimeError(f"{name}.{key} mismatch: expected={expected!r} actual={actual!r}")
        else:
            if actual is None:
                raise RuntimeError(f"{name}.{key} missing after reopen")
            _assert_close(float(actual), float(expected), f"{name}.{key}")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "kind": persisted.kind.value,
        "parameters": dict(persisted.parameters),
        "verdict": "NATIVE_PASS",
    }


def _reference_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, "reference")
    service = PartService(harness.part_runtime())
    plane = service.reference_plane(
        _target(harness, artifact, "reference_plane_revision"),
        ReferencePlaneSpec("OffsetPlane", "plane:front", 10.0, False),
        postconditions=PartPostconditions(body_count=1),
    )
    axis = service.reference_axis(
        _target(harness, artifact, "reference_axis_revision"),
        ReferenceAxisSpec("AxisZ", "plane:top", "plane:right"),
        postconditions=PartPostconditions(body_count=1),
    )
    point = service.reference_point(
        _target(harness, artifact, "reference_point_revision"),
        ReferencePointSpec("TopCenter", "bbox:+z"),
        postconditions=PartPostconditions(body_count=1),
    )
    harness.save_close(artifact, "reference_save")
    harness.reopen(artifact, "reference_reopen")
    reopened = PartService(harness.part_runtime())
    plane_snapshot = reopened.get_feature(
        _target(harness, artifact, "reference_plane_readback_revision"),
        plane.feature.feature_id,
    )
    axis_snapshot = reopened.get_feature(
        _target(harness, artifact, "reference_axis_readback_revision"),
        axis.feature.feature_id,
    )
    point_snapshot = reopened.get_feature(
        _target(harness, artifact, "reference_point_readback_revision"),
        point.feature.feature_id,
    )
    if plane_snapshot is None or axis_snapshot is None or point_snapshot is None:
        raise RuntimeError("reference geometry did not survive save/reopen")
    if plane_snapshot.kind is not FeatureKind.REFERENCE_PLANE:
        raise RuntimeError("reference plane kind mismatch after reopen")
    if axis_snapshot.kind is not FeatureKind.REFERENCE_AXIS:
        raise RuntimeError("reference axis kind mismatch after reopen")
    if point_snapshot.kind is not FeatureKind.REFERENCE_POINT:
        raise RuntimeError("reference point kind mismatch after reopen")
    _assert_close(float(plane_snapshot.parameters["offset_mm"]), 10.0, "reference_plane.offset_mm")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "plane": {"id": plane_snapshot.feature_id, "parameters": dict(plane_snapshot.parameters)},
        "axis": {"id": axis_snapshot.feature_id},
        "point": {"id": point_snapshot.feature_id},
        "verdict": "NATIVE_PASS",
    }


def _management_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, "management")
    service = PartService(harness.part_runtime())
    fillet = service.fillet(
        _target(harness, artifact, "management_fillet_revision"),
        FilletSpec("ManagedFillet", ("bbox:edge:+x:+z",), 3.0, False),
        postconditions=PartPostconditions(body_count=1),
    )
    edited = service.set_feature_parameter(
        _target(harness, artifact, "management_edit_revision"),
        fillet.feature.feature_id,
        "radius_mm",
        4.0,
    )
    renamed = service.rename_feature(
        _target(harness, artifact, "management_rename_revision"),
        fillet.feature.feature_id,
        "FilletManaged",
    )
    suppressed = service.set_feature_suppressed(
        _target(harness, artifact, "management_suppress_revision"),
        renamed.feature_id,
        True,
    )
    unsuppressed = service.set_feature_suppressed(
        _target(harness, artifact, "management_unsuppress_revision"),
        renamed.feature_id,
        False,
    )
    persisted = _persisted_feature(harness, artifact, renamed.feature_id, "management")
    if persisted.kind is not FeatureKind.FILLET or persisted.suppressed:
        raise RuntimeError("managed fillet did not persist as an unsuppressed fillet")
    _assert_close(float(persisted.parameters["radius_mm"]), 4.0, "management.radius_mm")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "edited_parameters": dict(edited.parameters),
        "suppressed_result": suppressed.suppressed,
        "unsuppressed_result": unsuppressed.suppressed,
        "persisted_parameters": dict(persisted.parameters),
        "verdict": "NATIVE_PASS",
    }


def _linear_pattern_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, "linear_pattern")
    service = PartService(harness.part_runtime())
    seed = service.hole(
        _target(harness, artifact, "linear_seed_revision"),
        HoleSpec("LinearSeed", 4.0, "bbox:+z", ((-25.0, 0.0),), True),
        postconditions=PartPostconditions(body_count=1),
    )
    pattern = service.linear_pattern(
        _target(harness, artifact, "linear_pattern_revision"),
        LinearPatternSpec(
            "LinearP0",
            (seed.feature.feature_id,),
            "bbox:edge:+y:+z",
            3,
            20.0,
            False,
        ),
        postconditions=PartPostconditions(body_count=1),
    )
    persisted = _persisted_feature(harness, artifact, pattern.feature.feature_id, "linear_pattern")
    if persisted.kind is not FeatureKind.LINEAR_PATTERN:
        raise RuntimeError("linear pattern kind mismatch after reopen")
    if persisted.parameters.get("count") != 3:
        raise RuntimeError(f"linear pattern count mismatch: {persisted.parameters.get('count')!r}")
    _assert_close(float(persisted.parameters["spacing_mm"]), 20.0, "linear_pattern.spacing_mm")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "parameters": dict(persisted.parameters),
        "verdict": "NATIVE_PASS",
    }


def _circular_pattern_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, "circular_pattern")
    service = PartService(harness.part_runtime())
    service.reference_axis(
        _target(harness, artifact, "circular_axis_revision"),
        ReferenceAxisSpec("AxisZ", "plane:top", "plane:right"),
        postconditions=PartPostconditions(body_count=1),
    )
    seed = service.hole(
        _target(harness, artifact, "circular_seed_revision"),
        HoleSpec("CircularSeed", 4.0, "bbox:+z", ((20.0, 0.0),), True),
        postconditions=PartPostconditions(body_count=1),
    )
    pattern = service.circular_pattern(
        _target(harness, artifact, "circular_pattern_revision"),
        CircularPatternSpec(
            "CircularP0",
            (seed.feature.feature_id,),
            "feature:AxisZ",
            4,
            360.0,
            False,
        ),
        postconditions=PartPostconditions(body_count=1),
    )
    persisted = _persisted_feature(harness, artifact, pattern.feature.feature_id, "circular_pattern")
    if persisted.kind is not FeatureKind.CIRCULAR_PATTERN:
        raise RuntimeError("circular pattern kind mismatch after reopen")
    if persisted.parameters.get("count") != 4:
        raise RuntimeError(f"circular pattern count mismatch: {persisted.parameters.get('count')!r}")
    _assert_close(float(persisted.parameters["angle_deg"]), 360.0, "circular_pattern.angle_deg")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "parameters": dict(persisted.parameters),
        "verdict": "NATIVE_PASS",
    }


def _mirror_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact = _block(harness, artifact_dir, "mirror")
    service = PartService(harness.part_runtime())
    seed = service.hole(
        _target(harness, artifact, "mirror_seed_revision"),
        HoleSpec("MirrorSeed", 4.0, "bbox:+z", ((-20.0, 0.0),), True),
        postconditions=PartPostconditions(body_count=1),
    )
    mirror = service.mirror(
        _target(harness, artifact, "mirror_revision"),
        MirrorSpec("MirrorP0", (seed.feature.feature_id,), "plane:right", False),
        postconditions=PartPostconditions(body_count=1),
    )
    persisted = _persisted_feature(harness, artifact, mirror.feature.feature_id, "mirror")
    if persisted.kind is not FeatureKind.MIRROR:
        raise RuntimeError("mirror kind mismatch after reopen")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "parameters": dict(persisted.parameters),
        "verdict": "NATIVE_PASS",
    }


def _rib_fixture(harness: NativeHarness, artifact_dir: Path) -> tuple[Path, str]:
    if not _OFFICIAL_RIB_SAMPLE.is_file():
        raise RuntimeError(f"SOLIDWORKS 2024 rib sample missing: {_OFFICIAL_RIB_SAMPLE}")
    artifact = artifact_dir / "rib.SLDPRT"
    artifact.unlink(missing_ok=True)
    shutil.copy2(_OFFICIAL_RIB_SAMPLE, artifact)
    harness.reopen(artifact, "rib_fixture_open")

    def setup(app: Any) -> str:
        model = harness.api.get_open_document(app, str(artifact.resolve()))
        if model is None:
            raise RuntimeError("rib fixture document is not open")
        extension = harness.api._member(model, "Extension")
        null = harness.api.null_dispatch()
        if not bool(
            harness.api._member(
                extension,
                "SelectByID2",
                "",
                "FACE",
                -8.78816842651986e-03,
                3.96239999998897e-02,
                -2.92468281514857e-02,
                False,
                1,
                null,
                0,
            )
        ):
            raise RuntimeError("rib fixture shell face selection failed")
        harness.api._member(model, "InsertFeatureShell", 0.00254, False)
        harness.api._member(
            extension,
            "SelectByID2",
            "",
            "FACE",
            2.64031138414111e-03,
            0.028407059059532,
            -6.13970439424634e-02,
            True,
            0,
            null,
            0,
        )
        harness.api._member(
            extension,
            "SelectByID2",
            "",
            "FACE",
            -0.059937899786064,
            2.77866864457792e-02,
            -8.77977980189826e-03,
            True,
            1,
            null,
            0,
        )
        plane = harness.api._member(
            harness.api._member(model, "FeatureManager"),
            "InsertRefPlane",
            128,
            0,
            128,
            0,
            0,
            0,
        )
        if plane is None:
            raise RuntimeError("rib fixture reference plane creation failed")
        harness.api._member(model, "ClearSelection2", True)
        if not bool(
            harness.api._member(
                extension,
                "SelectByID2",
                "Plane1",
                "PLANE",
                6.64896553058725e-03,
                0.109417877974863,
                5.24178648701081e-02,
                False,
                0,
                null,
                0,
            )
        ):
            raise RuntimeError("rib fixture Plane1 selection failed")
        sketch_manager = harness.api._member(model, "SketchManager")
        harness.api._member(sketch_manager, "InsertSketch", True)
        harness.api._member(
            sketch_manager,
            "CreateLine",
            -0.085797,
            0.021082,
            0.0,
            -0.03423,
            0.035134,
            0.0,
        )
        harness.api._member(
            sketch_manager,
            "CreateLine",
            -0.03423,
            0.035134,
            0.0,
            0.007726,
            0.025357,
            0.0,
        )
        harness.api._member(
            sketch_manager,
            "CreateLine",
            0.007726,
            0.025357,
            0.0,
            0.111514,
            0.039624,
            0.0,
        )
        harness.api._member(model, "ClearSelection2", True)
        harness.api._member(sketch_manager, "InsertSketch", True)
        harness.api._member(model, "ClearSelection2", True)

        sketch_name: str | None = None
        feature = harness.api.first_feature(model)
        while feature is not None:
            if harness.api.feature_type(feature) == "ProfileFeature":
                sketch_name = harness.api.feature_name(feature)
            feature = harness.api.next_feature(feature)
        if not sketch_name:
            raise RuntimeError("rib fixture profile sketch was not found")
        rebuild = rebuild_document(model, harness.api)
        if not rebuild.success:
            raise RuntimeError("rib fixture setup failed rebuild verification")
        return sketch_name

    sketch_id = harness.execute(setup, stage="rib_fixture_setup", mutation=True)
    return artifact, sketch_id


def _rib_gate(harness: NativeHarness, artifact_dir: Path) -> dict[str, Any]:
    artifact, sketch_id = _rib_fixture(harness, artifact_dir)
    service = PartService(harness.part_runtime())
    rib = service.rib(
        _target(harness, artifact, "rib_revision"),
        RibSpec("RibP0", ProfileRef(sketch_id), 2.54, True),
        postconditions=PartPostconditions(body_count=1),
    )
    persisted = _persisted_feature(harness, artifact, rib.feature.feature_id, "rib")
    if persisted.kind is not FeatureKind.RIB:
        raise RuntimeError("rib kind mismatch after reopen")
    _assert_close(float(persisted.parameters["thickness_mm"]), 2.54, "rib.thickness_mm")
    if persisted.parameters.get("both_sides") is not True:
        raise RuntimeError("rib both_sides did not persist")
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "feature_id": persisted.feature_id,
        "parameters": dict(persisted.parameters),
        "fixture_source": str(_OFFICIAL_RIB_SAMPLE),
        "verdict": "NATIVE_PASS",
    }


def run_part_p0_gate(harness: NativeHarness, evidence_dir: Path) -> dict[str, Any]:
    artifact_dir = evidence_dir / "part-p0-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return {
        "fillet": _feature_case(
            harness,
            artifact_dir,
            "fillet",
            "fillet",
            FilletSpec("FilletP0", ("bbox:edge:+x:+z",), 3.0, False),
            FeatureKind.FILLET,
            {"radius_mm": 3.0, "tangent_propagation": False},
        ),
        "chamfer": _feature_case(
            harness,
            artifact_dir,
            "chamfer",
            "chamfer",
            ChamferSpec("ChamferP0", ("bbox:edge:-x:+z",), 2.0, 45.0),
            FeatureKind.CHAMFER,
            {"distance_mm": 2.0, "angle_deg": 45.0},
        ),
        "shell": _feature_case(
            harness,
            artifact_dir,
            "shell",
            "shell",
            ShellSpec("ShellP0", ("bbox:+z",), 2.0, False),
            FeatureKind.SHELL,
            {"thickness_mm": 2.0, "outward": False},
        ),
        "draft": _feature_case(
            harness,
            artifact_dir,
            "draft",
            "draft",
            DraftSpec("DraftP0", ("bbox:+x",), "bbox:+z", 3.0, False),
            FeatureKind.DRAFT,
            {"angle_deg": 3.0, "reverse_direction": False},
        ),
        "rib": _rib_gate(harness, artifact_dir),
        "linear_pattern": _linear_pattern_gate(harness, artifact_dir),
        "circular_pattern": _circular_pattern_gate(harness, artifact_dir),
        "mirror": _mirror_gate(harness, artifact_dir),
        "reference_geometry": _reference_gate(harness, artifact_dir),
        "feature_management": _management_gate(harness, artifact_dir),
        "verdict": "NATIVE_PASS",
    }


def run(evidence_dir: Path, expected_head: str, mode: str) -> dict[str, Any]:
    windows_head = _git_optional("rev-parse", "HEAD")
    windows_status = _git_optional("status", "--porcelain", "--untracked-files=no")
    if windows_head is not None and windows_head != expected_head:
        raise RuntimeError(f"HEAD mismatch: expected={expected_head} actual={windows_head}")
    if mode == "formal" and windows_status:
        raise RuntimeError(f"formal native acceptance requires a clean tracked worktree: {windows_status!r}")

    with NativeHarness(evidence_dir) as harness:
        if str(harness.info.revision) != "32.0.1":
            raise RuntimeError(
                f"SOLIDWORKS revision mismatch: expected=32.0.1 actual={harness.info.revision}"
            )
        part_p0 = run_part_p0_gate(harness, evidence_dir)
        return {
            "ok": True,
            "mode": mode,
            "provider_head": windows_head or expected_head,
            "head_verification": (
                "windows_git" if windows_head is not None else "external_gateway_pre_post_required"
            ),
            "windows_tracked_worktree_clean": (
                None if windows_status is None else not bool(windows_status)
            ),
            "host": "Slnc_TrZ",
            "solidworks_revision": harness.info.revision,
            "solidworks_year": harness.info.version_year,
            "part_p0": part_p0,
            "score_claims": {
                "C_part_p0_non_hole_wizard": {
                    "increment": 7.5,
                    "native_groups": [
                        "fillet + chamfer + shell",
                        "draft + rib",
                        "linear + circular pattern + mirror",
                        "reference plane + axis + point",
                        "feature query + rename + suppress/unsuppress + selected parameter edit",
                    ],
                }
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--mode", choices=("dev", "formal"), default="formal")
    args = parser.parse_args()
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report_path = evidence_dir / f"agent-a-part-p0-native-{args.mode}.report.json"
    payload: dict[str, Any]
    try:
        payload = run(evidence_dir, args.expected_head, args.mode)
    except BaseException as exc:
        payload = {
            "ok": False,
            "mode": args.mode,
            "expected_head": args.expected_head,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(json.dumps({"report": str(report_path), **payload}, indent=2, sort_keys=True, default=str))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
