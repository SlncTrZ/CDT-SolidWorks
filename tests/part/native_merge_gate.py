"""Agent A native merge gate — Final-HEAD SOLIDWORKS evidence for Sketch P0 and Hole Wizard.
Wing: Mechanical 95 | Topic: agent-a-native-acceptance | Updated: 2026-09-14 10:20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, TypeVar

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.part.models import HoleWizardSize, HoleWizardSpec, PartPostconditions
from cdt_solidworks.part.native import NativePartBinding, PartNativeRuntime
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult
from cdt_solidworks.part.service import PartService
from cdt_solidworks.sketch.models import (
    AngularDimension,
    CenterLine,
    Circle,
    CoincidentConstraint,
    ConcentricConstraint,
    DefinitionState,
    DiameterDimension,
    DistanceDimension,
    EqualConstraint,
    FixConstraint,
    HorizontalConstraint,
    LineSegment,
    MidpointConstraint,
    ParallelConstraint,
    PerpendicularConstraint,
    PlaneKind,
    Point2D,
    RadiusDimension,
    SketchDefinition,
    SketchPlane,
    SketchPoint,
    SymmetricConstraint,
    TangentConstraint,
    VerticalConstraint,
)
from cdt_solidworks.sketch.native import NativeSketchBinding, SketchNativeRuntime
from cdt_solidworks.sketch.service import SketchService

T = TypeVar("T")
_PART_DOC = 1
_STANDARD_PLANE_INDEX = {PlaneKind.FRONT: 0, PlaneKind.TOP: 1, PlaneKind.RIGHT: 2}
_EXPECTED_HW_GEOMETRY = {
    HoleWizardSize.M2: (4.4, 90.0, 2.4),
    HoleWizardSize.M4: (9.4, 90.0, 4.5),
    HoleWizardSize.M6: (12.6, 90.0, 6.6),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_optional(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        # The Windows view of this Linux-created git worktree can read the files
        # through H:\\Develop while its .git indirection remains a Linux path.
        # Formal HEAD/clean verification is therefore performed by the .227
        # launcher immediately before and after the serialized native run.
        return None


def _require_success(result: Any, stage: str) -> Any:
    if result.state is not NativeCallState.SUCCESS:
        failure = result.failure
        detail = result.state.value if failure is None else f"{failure.code}: {failure.message} details={dict(failure.details)}"
        raise RuntimeError(f"{stage} failed: {detail}")
    return result.value


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return tuple(value)
    return (value,)


class NativeHarness:
    def __init__(self, evidence_dir: Path) -> None:
        self.evidence_dir = evidence_dir.resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.session = SolidWorksSession()
        self.api = self.session.api
        self.open_titles: set[str] = set()
        self.info: Any = None

    def __enter__(self) -> "NativeHarness":
        self.info = _require_success(
            self.session.connect(
                policy=AttachPolicy.ATTACH_OR_START,
                version=2024,
                visible=False,
                timeout=120.0,
            ),
            "connect",
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for title in tuple(self.open_titles):
            try:
                self.execute(
                    lambda app, value=title: self.api.close_document(app, value),
                    stage="agent_a_cleanup_close",
                    mutation=True,
                )
            except Exception:
                pass
            self.open_titles.discard(title)
        try:
            self.session.disconnect(timeout=15.0)
        finally:
            self.session.close_dispatcher(timeout=5.0)

    def execute(self, operation: Callable[[Any], T], *, stage: str, mutation: bool) -> T:
        def instrumented(app: Any) -> T:
            try:
                return operation(app)
            except Exception as exc:
                raise NativeRuntimeError(
                    "agent_a_native_gate_failed",
                    stage,
                    f"{type(exc).__name__}: {exc}",
                    details={"exception_type": type(exc).__name__},
                ) from exc

        result = self.session.execute(
            instrumented,
            stage=stage,
            timeout=180.0,
            mutation=mutation,
        )
        return _require_success(result, stage)

    def create_blank_part(self, path: Path, stage: str) -> int:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

        def operation(app: Any) -> tuple[str, int]:
            template = str(self.api._member(app, "GetDocumentTemplate", _PART_DOC, "", 0, 0.0, 0.0) or "")
            if not template or not Path(template).is_file():
                raise RuntimeError("SOLIDWORKS did not provide an existing default part template")
            model = self.api._member(app, "NewDocument", template, 0, 0.0, 0.0)
            if model is None:
                raise RuntimeError("SOLIDWORKS did not create the native evidence part")
            success, errors, warnings = self.api.save_as(model, str(path))
            if not success or int(errors) != 0 or not path.is_file():
                raise RuntimeError(f"initial SaveAs failed: errors={errors}, warnings={warnings}")
            title = self.api.document_title(model)
            revision = int(self.api.update_stamp(model) or 0)
            return title, revision

        title, revision = self.execute(operation, stage=stage, mutation=True)
        self.open_titles.add(title)
        return revision

    def current_revision(self, path: Path, stage: str) -> int:
        source = str(path.resolve())

        def operation(app: Any) -> int:
            model = self.api.get_open_document(app, source)
            if model is None:
                raise RuntimeError(f"document not open: {source}")
            return int(self.api.update_stamp(model) or 0)

        return self.execute(operation, stage=stage, mutation=False)

    def save_close(self, path: Path, stage: str) -> None:
        source = str(path.resolve())

        def operation(app: Any) -> str:
            model = self.api.get_open_document(app, source)
            if model is None:
                raise RuntimeError(f"document not open before save: {source}")
            success, errors, warnings = self.api.save_document(model)
            if not success or int(errors) != 0:
                raise RuntimeError(f"save failed: errors={errors}, warnings={warnings}")
            title = self.api.document_title(model)
            self.api.close_document(app, title)
            return title

        title = self.execute(operation, stage=stage, mutation=True)
        self.open_titles.discard(title)

    def reopen(self, path: Path, stage: str) -> int:
        source = str(path.resolve())

        def operation(app: Any) -> tuple[str, int]:
            model, errors, warnings = self.api.open_document(
                app,
                source,
                _PART_DOC,
                read_only=False,
                silent=True,
                configuration="",
            )
            if model is None or int(errors) != 0:
                raise RuntimeError(f"reopen failed: errors={errors}, warnings={warnings}")
            title = self.api.document_title(model)
            return title, int(self.api.update_stamp(model) or 0)

        title, revision = self.execute(operation, stage=stage, mutation=True)
        self.open_titles.add(title)
        return revision

    def sketch_runtime(self) -> SketchNativeRuntime:
        api = self.api

        def resolve_binding(app: Any, target: DocumentTarget) -> NativeSketchBinding:
            model = api.get_open_document(app, target.document_id)
            if model is None:
                model, errors, warnings = api.open_document(
                    app,
                    target.document_id,
                    _PART_DOC,
                    read_only=False,
                    silent=True,
                    configuration="",
                )
                if model is None or int(errors) != 0:
                    raise RuntimeError(f"sketch binding open failed: errors={errors}, warnings={warnings}")
                self.open_titles.add(api.document_title(model))
            return NativeSketchBinding(
                model=model,
                document_id=target.document_id,
                revision=int(api.update_stamp(model) or 0),
                units="mm",
                configuration=api.active_configuration(model),
            )

        def select_plane(model: Any, plane: SketchPlane) -> None:
            if plane.kind not in _STANDARD_PLANE_INDEX or plane.reference_id is not None:
                raise RuntimeError("native merge gate supports standard planes only")
            target_index = _STANDARD_PLANE_INDEX[plane.kind]
            feature = api.first_feature(model)
            ref_index = 0
            selected = None
            while feature is not None:
                if api.feature_type(feature) == "RefPlane":
                    if ref_index == target_index:
                        selected = feature
                        break
                    ref_index += 1
                feature = api.next_feature(feature)
            if selected is None:
                raise RuntimeError("standard reference plane was not found")
            api._member(model, "ClearSelection2", True)
            if not bool(api._member(selected, "Select2", False, 0)):
                raise RuntimeError("standard reference plane could not be selected")

        def resolve_sketch_feature(model: Any, sketch: Any, requested_name: str) -> Any:
            feature = api.first_feature(model)
            found = None
            while feature is not None:
                if api.feature_type(feature) == "ProfileFeature":
                    found = feature
                feature = api.next_feature(feature)
            if found is None:
                raise RuntimeError(f"created sketch feature {requested_name!r} was not found")
            return found

        def verify_rebuild(model: Any) -> RebuildResult:
            report = rebuild_document(model, api)
            return RebuildResult(
                ok=report.success,
                error_code=None if report.success else "rebuild_failed",
                message=None if report.success else "native rebuild or feature-error gate failed",
            )

        return SketchNativeRuntime(
            executor=self.execute,
            binding_resolver=resolve_binding,
            plane_selector=select_plane,
            sketch_feature_resolver=resolve_sketch_feature,
            member=api._member,
            rebuild_verifier=verify_rebuild,
        )

    def part_runtime(self) -> PartNativeRuntime:
        api = self.api

        def resolve_binding(app: Any, target: DocumentTarget) -> NativePartBinding:
            model = api.get_open_document(app, target.document_id)
            if model is None:
                model, errors, warnings = api.open_document(
                    app,
                    target.document_id,
                    _PART_DOC,
                    read_only=False,
                    silent=True,
                    configuration="",
                )
                if model is None or int(errors) != 0:
                    raise RuntimeError(f"part binding open failed: errors={errors}, warnings={warnings}")
                self.open_titles.add(api.document_title(model))
            return NativePartBinding(
                model=model,
                document_id=target.document_id,
                revision=int(api.update_stamp(model) or 0),
                units="mm",
                configuration=api.active_configuration(model),
            )

        def verify_rebuild(model: Any) -> RebuildResult:
            report = rebuild_document(model, api)
            return RebuildResult(
                ok=report.success,
                error_code=None if report.success else "rebuild_failed",
                message=None if report.success else "native rebuild or feature-error gate failed",
            )

        return PartNativeRuntime(
            executor=self.execute,
            binding_resolver=resolve_binding,
            member=api._member,
            feature_name=api.feature_name,
            feature_type=api.feature_type,
            bodies=api.bodies,
            body_name=api.body_name,
            rebuild_verifier=verify_rebuild,
            null_dispatch=getattr(api, "null_dispatch", lambda: None),
        )


def _sketch_target(harness: NativeHarness, path: Path, label: str) -> DocumentTarget:
    return DocumentTarget(
        str(path.resolve()),
        harness.current_revision(path, f"{label}_revision"),
        "mm",
    )


def _relation_definitions() -> tuple[tuple[str, SketchDefinition, str], ...]:
    front = SketchPlane(PlaneKind.FRONT)
    return (
        (
            "horizontal",
            SketchDefinition(
                "RelHorizontal",
                front,
                (LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 2.0)),),
                (HorizontalConstraint(0),),
            ),
            "horizontal",
        ),
        (
            "vertical",
            SketchDefinition(
                "RelVertical",
                front,
                (LineSegment(Point2D(0.0, 0.0), Point2D(2.0, 20.0)),),
                (VerticalConstraint(0),),
            ),
            "vertical",
        ),
        (
            "parallel",
            SketchDefinition(
                "RelParallel",
                front,
                (
                    LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                    LineSegment(Point2D(0.0, 8.0), Point2D(18.0, 7.0)),
                ),
                (ParallelConstraint(0, 1),),
            ),
            "parallel",
        ),
        (
            "perpendicular",
            SketchDefinition(
                "RelPerpendicular",
                front,
                (
                    LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                    LineSegment(Point2D(2.0, -5.0), Point2D(3.0, 15.0)),
                ),
                (PerpendicularConstraint(0, 1),),
            ),
            "perpendicular",
        ),
        (
            "coincident",
            SketchDefinition(
                "RelCoincident",
                front,
                (
                    SketchPoint(Point2D(5.0, 3.0)),
                    LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                ),
                (CoincidentConstraint(0, 1),),
            ),
            "coincident",
        ),
        (
            "concentric",
            SketchDefinition(
                "RelConcentric",
                front,
                (Circle(Point2D(0.0, 0.0), 5.0), Circle(Point2D(2.0, 2.0), 8.0)),
                (ConcentricConstraint(0, 1),),
            ),
            "concentric",
        ),
        (
            "tangent",
            SketchDefinition(
                "RelTangent",
                front,
                (
                    Circle(Point2D(0.0, 0.0), 10.0),
                    LineSegment(Point2D(-15.0, 10.0), Point2D(15.0, 10.0)),
                ),
                (TangentConstraint(0, 1),),
            ),
            "tangent",
        ),
        (
            "equal",
            SketchDefinition(
                "RelEqual",
                front,
                (
                    LineSegment(Point2D(0.0, 0.0), Point2D(10.0, 0.0)),
                    LineSegment(Point2D(0.0, 8.0), Point2D(14.0, 8.0)),
                ),
                (EqualConstraint(0, 1),),
            ),
            "equal",
        ),
        (
            "midpoint",
            SketchDefinition(
                "RelMidpoint",
                front,
                (
                    SketchPoint(Point2D(4.0, 2.0)),
                    LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                ),
                (MidpointConstraint(0, 1),),
            ),
            "midpoint",
        ),
        (
            "symmetric",
            SketchDefinition(
                "RelSymmetric",
                front,
                (
                    LineSegment(Point2D(-8.0, -10.0), Point2D(-8.0, 10.0)),
                    LineSegment(Point2D(7.0, -10.0), Point2D(7.0, 10.0)),
                    CenterLine(Point2D(0.0, -20.0), Point2D(0.0, 20.0)),
                ),
                (SymmetricConstraint(0, 1, 2),),
            ),
            "symmetric",
        ),
        (
            "fix",
            SketchDefinition(
                "RelFix",
                front,
                (SketchPoint(Point2D(12.0, 7.0)),),
                (FixConstraint(0),),
            ),
            "fix",
        ),
    )


def run_sketch_gate(harness: NativeHarness) -> dict[str, Any]:
    artifact = harness.evidence_dir / "agent-a-sketch-p0.SLDPRT"
    harness.create_blank_part(artifact, "agent_a_sketch_create_part")
    service = SketchService(harness.sketch_runtime())
    created_relations: dict[str, dict[str, Any]] = {}

    under = service.create(
        _sketch_target(harness, artifact, "sketch_under"),
        SketchDefinition(
            "StateUnder",
            SketchPlane(PlaneKind.FRONT),
            (LineSegment(Point2D(0.0, 0.0), Point2D(15.0, 5.0)),),
        ),
    )
    if under.definition_state is not DefinitionState.UNDER_DEFINED:
        raise RuntimeError(f"under-defined state mismatch: {under.definition_state.value}")

    for label, definition, expected_type in _relation_definitions():
        snapshot = service.create(
            _sketch_target(harness, artifact, f"relation_{label}"),
            definition,
        )
        matches = tuple(item for item in snapshot.relations if item.relation_type == expected_type)
        if len(matches) != 1:
            raise RuntimeError(
                f"{label} relation read-back mismatch: {[item.relation_type for item in snapshot.relations]!r}"
            )
        created_relations[label] = {
            "sketch": snapshot.sketch_id,
            "relation_id": matches[0].relation_id,
            "entity_ids": list(matches[0].entity_ids),
            "definition_state": snapshot.definition_state.value,
        }

    fix_state = service.get(
        _sketch_target(harness, artifact, "fix_state"),
        "RelFix",
    )
    if fix_state.definition_state is not DefinitionState.FULLY_DEFINED:
        raise RuntimeError(f"fixed sketch is not fully defined: {fix_state.definition_state.value}")

    delete_fix = service.create(
        _sketch_target(harness, artifact, "delete_fix_create"),
        SketchDefinition(
            "DeleteFix",
            SketchPlane(PlaneKind.FRONT),
            (SketchPoint(Point2D(-12.0, 4.0)),),
            (FixConstraint(0),),
        ),
    )
    fix_relation = next(item for item in delete_fix.relations if item.relation_type == "fix")
    remaining = service.delete_relation(
        _sketch_target(harness, artifact, "delete_fix"),
        "DeleteFix",
        fix_relation.relation_id,
    )
    if any(item.relation_type == "fix" for item in remaining):
        raise RuntimeError("fix relation remained after native deletion")
    unfixed = service.get(_sketch_target(harness, artifact, "delete_fix_state"), "DeleteFix")
    if unfixed.definition_state is not DefinitionState.UNDER_DEFINED:
        raise RuntimeError(f"deleted fix did not return sketch to under-defined: {unfixed.definition_state.value}")

    dimension_definitions = (
        (
            "linear",
            "DimLinear",
            SketchDefinition(
                "DimLinear",
                SketchPlane(PlaneKind.FRONT),
                (LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),),
                dimensions=(DistanceDimension("linear", 0, 20.0),),
            ),
            20.0,
            "mm",
            True,
        ),
        (
            "angular",
            "DimAngular",
            SketchDefinition(
                "DimAngular",
                SketchPlane(PlaneKind.FRONT),
                (
                    LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 0.0)),
                    LineSegment(Point2D(0.0, 0.0), Point2D(0.0, 20.0)),
                ),
                dimensions=(AngularDimension("angular", 0, 1, 90.0),),
            ),
            90.0,
            "deg",
            True,
        ),
        (
            "radius",
            "DimRadius",
            SketchDefinition(
                "DimRadius",
                SketchPlane(PlaneKind.FRONT),
                (Circle(Point2D(35.0, 0.0), 4.0),),
                dimensions=(RadiusDimension("radius", 0, 4.0),),
            ),
            4.0,
            "mm",
            True,
        ),
        (
            "diameter",
            "DimDiameter",
            SketchDefinition(
                "DimDiameter",
                SketchPlane(PlaneKind.FRONT),
                (Circle(Point2D(55.0, 0.0), 6.0),),
                dimensions=(DiameterDimension("diameter", 0, 12.0, driving=False),),
            ),
            12.0,
            "mm",
            False,
        ),
    )
    dimension_map: dict[str, Any] = {}
    for name, sketch_name, definition, expected_value, unit, driving in dimension_definitions:
        try:
            snapshot = service.create(
                _sketch_target(harness, artifact, f"dimension_{name}_create"),
                definition,
            )
        except Exception as exc:
            raise RuntimeError(f"{name} dimension fixture failed: {exc}") from exc
        item = next((candidate for candidate in snapshot.dimensions if candidate.name == name), None)
        if item is None:
            raise RuntimeError(f"missing native dimension {name!r}")
        if item.unit != unit or item.driving is not driving or not math.isclose(item.value, expected_value, abs_tol=1e-6):
            raise RuntimeError(f"dimension {name!r} read-back mismatch: {item!r}")
        dimension_map[name] = item

    linear_edit = service.set_dimension_value(
        _sketch_target(harness, artifact, "linear_edit"),
        "DimLinear",
        "linear",
        25.0,
        unit="mm",
    )
    angular_edit = service.set_dimension_value(
        _sketch_target(harness, artifact, "angular_edit"),
        "DimAngular",
        "angular",
        45.0,
        unit="deg",
    )
    if not math.isclose(linear_edit.value, 25.0, abs_tol=1e-6):
        raise RuntimeError("linear dimension edit did not read back 25 mm")
    if not math.isclose(angular_edit.value, 45.0, abs_tol=1e-6):
        raise RuntimeError("angular dimension edit did not read back 45 deg")

    harness.save_close(artifact, "agent_a_sketch_save_close")
    reopen_revision = harness.reopen(artifact, "agent_a_sketch_reopen")
    fresh_service = SketchService(harness.sketch_runtime())
    fresh_target = DocumentTarget(str(artifact.resolve()), reopen_revision, "mm")

    persisted_relations: dict[str, Any] = {}
    for label, data in created_relations.items():
        relations = fresh_service.list_relations(fresh_target, data["sketch"])
        match = next((item for item in relations if item.relation_type == label), None)
        if match is None:
            raise RuntimeError(f"persisted relation {label!r} missing after reopen")
        if match.relation_id != data["relation_id"]:
            raise RuntimeError(
                f"persisted relation identity changed for {label}: before={data['relation_id']} after={match.relation_id}"
            )
        persisted_relations[label] = {
            "relation_id": match.relation_id,
            "entity_ids": list(match.entity_ids),
        }

    persisted_under = fresh_service.get(fresh_target, "StateUnder")
    persisted_fix = fresh_service.get(fresh_target, "RelFix")
    if persisted_under.definition_state is not DefinitionState.UNDER_DEFINED:
        raise RuntimeError("under-defined state did not persist after reopen")
    if persisted_fix.definition_state is not DefinitionState.FULLY_DEFINED:
        raise RuntimeError("fully-defined state did not persist after reopen")

    persisted_dim_map: dict[str, Any] = {}
    for name, sketch_name, _, _, _, _ in dimension_definitions:
        persisted_dims = fresh_service.get(fresh_target, sketch_name)
        item = next((candidate for candidate in persisted_dims.dimensions if candidate.name == name), None)
        expected_value = {
            "linear": 25.0,
            "angular": 45.0,
            "radius": 4.0,
            "diameter": 12.0,
        }[name]
        if item is None or not math.isclose(item.value, expected_value, abs_tol=1e-6):
            raise RuntimeError(f"persisted dimension {name!r} mismatch: {item!r}")
        persisted_dim_map[name] = item

    # Native negative fixture: fixed diagonal + horizontal is incompatible. The
    # adapter must reject instead of returning a successful over-defined sketch.
    negative_artifact = harness.evidence_dir / "agent-a-sketch-negative.SLDPRT"
    harness.create_blank_part(negative_artifact, "agent_a_sketch_negative_part")
    negative_service = SketchService(harness.sketch_runtime())
    negative_outcome: dict[str, Any]
    try:
        negative_snapshot = negative_service.create(
            _sketch_target(harness, negative_artifact, "negative_relation"),
            SketchDefinition(
                "OverDefinedNegative",
                SketchPlane(PlaneKind.FRONT),
                (LineSegment(Point2D(0.0, 0.0), Point2D(20.0, 7.0)),),
                (FixConstraint(0), HorizontalConstraint(0)),
            ),
        )
    except Exception as exc:
        negative_outcome = {
            "accepted": False,
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    else:
        if negative_snapshot.definition_state is not DefinitionState.OVER_DEFINED:
            raise RuntimeError(
                "incompatible fixed+horizontal relation unexpectedly returned success without over-defined state"
            )
        negative_outcome = {
            "accepted": False,
            "definition_state": negative_snapshot.definition_state.value,
            "message": "service rejected over-defined state",
        }

    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "relation_types": sorted(persisted_relations),
        "relations": persisted_relations,
        "definition_states": {
            "under": persisted_under.definition_state.value,
            "full": persisted_fix.definition_state.value,
            "unfix_via_relation_delete": unfixed.definition_state.value,
        },
        "dimensions": {
            name: {
                "value": item.value,
                "unit": item.unit,
                "driving": item.driving,
            }
            for name, item in persisted_dim_map.items()
        },
        "negative": negative_outcome,
        "verdict": "NATIVE_PASS",
    }


def run_hole_wizard_gate(harness: NativeHarness) -> dict[str, Any]:
    artifact = harness.evidence_dir / "agent-a-hole-wizard.SLDPRT"
    artifact.unlink(missing_ok=True)
    cad = CadCoreService(
        harness.session,
        path_policy=DocumentPathPolicy((harness.evidence_dir,)),
        default_timeout=120.0,
    )
    _require_success(
        cad.create_rect_extrude(
            artifact,
            width_mm=120.0,
            height_mm=80.0,
            depth_mm=30.0,
            plane="front",
            timeout=120.0,
        ),
        "agent_a_hole_wizard_base",
    )

    # CadCore creates and persists the block but does not promise to leave the
    # native document open. Open it explicitly before the lane-local mutations.
    harness.reopen(artifact, "agent_a_hw_open")

    service = PartService(harness.part_runtime())
    centers = {
        HoleWizardSize.M2: (-30.0, 0.0),
        HoleWizardSize.M4: (0.0, 0.0),
        HoleWizardSize.M6: (30.0, 0.0),
    }
    created: dict[str, Any] = {}
    for size in (HoleWizardSize.M2, HoleWizardSize.M4, HoleWizardSize.M6):
        target = DocumentTarget(
            str(artifact.resolve()),
            harness.current_revision(artifact, f"hw_{size.value}_revision"),
            "mm",
        )
        mutation = service.hole_wizard(
            target,
            HoleWizardSpec(
                name=f"HW_{size.value}",
                size=size,
                face_ref="bbox:+z",
                center_mm=centers[size],
            ),
            postconditions=PartPostconditions(body_count=1),
        )
        feature = mutation.feature
        expected_csk, expected_angle, expected_thru = _EXPECTED_HW_GEOMETRY[size]
        checks = {
            "wizard_standard": "ANSI Metric",
            "wizard_fastener": "Flat Head Screw - ANSI B18.6.7M",
            "wizard_size": size.value,
            "through_all": True,
            "center_count": 1,
            "face_ref": "bbox:+z",
        }
        for key, expected in checks.items():
            if feature.parameters.get(key) != expected:
                raise RuntimeError(f"{size.value} {key} mismatch: {feature.parameters.get(key)!r}")
        for key, expected in (
            ("counter_sink_diameter_mm", expected_csk),
            ("counter_sink_angle_deg", expected_angle),
            ("thru_hole_diameter_mm", expected_thru),
            ("center_x_mm", centers[size][0]),
            ("center_y_mm", centers[size][1]),
        ):
            actual = feature.parameters.get(key)
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isclose(
                float(actual), float(expected), rel_tol=0.0, abs_tol=1e-6
            ):
                raise RuntimeError(f"{size.value} {key} mismatch: expected={expected} actual={actual}")
        created[size.value] = dict(feature.parameters)

    harness.save_close(artifact, "agent_a_hw_save_close")
    harness.reopen(artifact, "agent_a_hw_reopen")
    fresh_service = PartService(harness.part_runtime())
    persisted: dict[str, Any] = {}
    for size in (HoleWizardSize.M2, HoleWizardSize.M4, HoleWizardSize.M6):
        target = DocumentTarget(
            str(artifact.resolve()),
            harness.current_revision(artifact, f"hw_{size.value}_persisted_revision"),
            "mm",
        )
        feature = fresh_service.get_feature(target, f"HW_{size.value}")
        expected_csk, expected_angle, expected_thru = _EXPECTED_HW_GEOMETRY[size]
        for key, expected in (
            ("wizard_standard", "ANSI Metric"),
            ("wizard_fastener", "Flat Head Screw - ANSI B18.6.7M"),
            ("wizard_size", size.value),
            ("through_all", True),
            ("center_count", 1),
            ("face_ref", "bbox:+z"),
        ):
            if feature.parameters.get(key) != expected:
                raise RuntimeError(f"persisted {size.value} {key} mismatch")
        for key, expected in (
            ("counter_sink_diameter_mm", expected_csk),
            ("counter_sink_angle_deg", expected_angle),
            ("thru_hole_diameter_mm", expected_thru),
            ("center_x_mm", centers[size][0]),
            ("center_y_mm", centers[size][1]),
        ):
            actual = feature.parameters.get(key)
            if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isclose(
                float(actual), float(expected), rel_tol=0.0, abs_tol=1e-6
            ):
                raise RuntimeError(
                    f"persisted {size.value} {key} mismatch: expected={expected} actual={actual}"
                )
        persisted[size.value] = dict(feature.parameters)

    def verify_model(app: Any) -> dict[str, Any]:
        model = harness.api.get_open_document(app, str(artifact.resolve()))
        if model is None:
            raise RuntimeError("Hole Wizard part disappeared after reopen")
        rebuild = rebuild_document(model, harness.api)
        bodies = tuple(harness.api.bodies(model, 0, False))
        if not rebuild.success or len(bodies) != 1:
            raise RuntimeError(
                f"Hole Wizard persisted model verification failed: rebuild={rebuild.success} bodies={len(bodies)}"
            )
        return {
            "rebuild": rebuild.success,
            "solid_body_count": len(bodies),
            "revision": int(harness.api.update_stamp(model) or 0),
        }

    model_verification = harness.execute(
        verify_model,
        stage="agent_a_hw_model_verify",
        mutation=False,
    )
    return {
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "created": created,
        "persisted": persisted,
        "model": model_verification,
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
        sketch = run_sketch_gate(harness)
        hole_wizard = run_hole_wizard_gate(harness)
        return {
            "ok": True,
            "mode": mode,
            "provider_head": windows_head or expected_head,
            "head_verification": (
                "windows_git"
                if windows_head is not None
                else "external_gateway_pre_post_required"
            ),
            "windows_tracked_worktree_clean": (
                None if windows_status is None else not bool(windows_status)
            ),
            "host": "Slnc_TrZ",
            "solidworks_revision": harness.info.revision,
            "solidworks_year": harness.info.version_year,
            "sketch_p0": sketch,
            "hole_wizard": hole_wizard,
            "score_claims": {
                "B_sketch_reference": {
                    "baseline": 4.5,
                    "claimed": 9.5,
                    "native_group": "common relations + definition state + linear/angular/radius/diameter dimensions",
                },
                "C_hole_group": {
                    "baseline_group_points": 1.0,
                    "claimed_group_points": 2.0,
                    "increment": 1.0,
                    "native_group": "ANSI Metric countersink Hole Wizard M2/M4/M6 bounded subset",
                },
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
    report_path = evidence_dir / f"agent-a-native-{args.mode}.report.json"
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
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"report": str(report_path), **payload}, indent=2, sort_keys=True, default=str))
    return 0 if payload.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
