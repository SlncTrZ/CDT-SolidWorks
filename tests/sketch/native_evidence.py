"""Windows-only native evidence runner for Mechanical-90 Agent A sketch breadth.

Run manually on the supported SOLIDWORKS host; it is intentionally not collected by
pytest. The fixture uses the accepted shared serialized session and the lane-local
bounded sketch adapter, then saves/reopens the native artifact for independent COM
read-back.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, TypeVar

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.native.errors import NativeRuntimeError
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.rebuild import rebuild_document
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.part.runtime import DocumentTarget, RebuildResult
from cdt_solidworks.sketch.models import (
    Arc,
    ArcDirection,
    CenterLine,
    Circle,
    Ellipse,
    LineSegment,
    PlaneKind,
    Point2D,
    SketchDefinition,
    SketchPlane,
    SketchPoint,
    Spline,
)
from cdt_solidworks.sketch.native import NativeSketchBinding, SketchNativeRuntime
from cdt_solidworks.sketch.service import SketchService

T = TypeVar("T")
_PART_DOC = 1
_STANDARD_PLANE_INDEX = {PlaneKind.FRONT: 0, PlaneKind.TOP: 1, PlaneKind.RIGHT: 2}


def _require_success(result: Any, stage: str) -> Any:
    if result.state is not NativeCallState.SUCCESS:
        failure = result.failure
        if failure is None:
            detail = result.state.value
        else:
            detail = f"{failure.code}: {failure.message} details={dict(failure.details)}"
        raise RuntimeError(f"{stage} failed: {detail}")
    return result.value


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return tuple(value)
    return (value,)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(output: Path) -> dict[str, Any]:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"refusing to overwrite existing evidence artifact: {output}")

    session = SolidWorksSession()
    api = session.api
    opened_title: str | None = None
    reopened_title: str | None = None

    connect = session.connect(policy=AttachPolicy.ATTACH_OR_START, version=2024, visible=False, timeout=120.0)

    def execute(
        operation: Callable[[Any], T],
        *,
        stage: str,
        mutation: bool,
    ) -> T:
        def instrumented(app: Any) -> T:
            try:
                return operation(app)
            except Exception as exc:
                raise NativeRuntimeError(
                    "fixture_operation_failed",
                    stage,
                    f"{type(exc).__name__}: {exc}",
                    details={"exception_type": type(exc).__name__},
                ) from exc

        result = session.execute(instrumented, stage=stage, timeout=60.0, mutation=mutation)
        return _require_success(result, stage)

    try:
        info = _require_success(connect, "connect")

        def create_blank(app: Any) -> tuple[str, int]:
            template = str(api._member(app, "GetDocumentTemplate", _PART_DOC, "", 0, 0.0, 0.0) or "")
            if not template or not Path(template).is_file():
                raise RuntimeError("SOLIDWORKS did not provide an existing default part template")
            model = api._member(app, "NewDocument", template, 0, 0.0, 0.0)
            if model is None:
                raise RuntimeError("SOLIDWORKS did not create the native evidence part")
            success, errors, warnings = api.save_as(model, str(output))
            if not success or errors != 0 or not output.is_file():
                raise RuntimeError(f"initial SaveAs failed: errors={errors}, warnings={warnings}")
            revision = api.update_stamp(model)
            return api.document_title(model), int(revision or 0)

        opened_title, revision = execute(create_blank, stage="agent_a_fixture_create", mutation=True)

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
                if model is None or errors != 0:
                    raise RuntimeError(f"fixture open failed: errors={errors}, warnings={warnings}")
            actual_revision = api.update_stamp(model)
            return NativeSketchBinding(
                model=model,
                document_id=target.document_id,
                revision=int(actual_revision if actual_revision is not None else target.expected_revision),
                units="mm",
                configuration=api.active_configuration(model),
            )

        def select_plane(model: Any, plane: SketchPlane) -> None:
            if plane.kind not in _STANDARD_PLANE_INDEX or plane.reference_id is not None:
                raise RuntimeError("native evidence fixture supports standard planes only")
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
                raise RuntimeError("created sketch feature was not found")
            return found

        def verify_rebuild(model: Any) -> RebuildResult:
            report = rebuild_document(model, api)
            return RebuildResult(
                ok=report.success,
                error_code=None if report.success else "rebuild_failed",
                message=None if report.success else "native rebuild or feature-error gate failed",
            )

        runtime = SketchNativeRuntime(
            executor=execute,
            binding_resolver=resolve_binding,
            plane_selector=select_plane,
            sketch_feature_resolver=resolve_sketch_feature,
            member=api._member,
            rebuild_verifier=verify_rebuild,
        )
        service = SketchService(runtime)
        target = DocumentTarget(str(output), expected_revision=revision, expected_units="mm")
        definition = SketchDefinition(
            name="AgentA_Breadth",
            plane=SketchPlane(PlaneKind.FRONT),
            entities=(
                LineSegment(Point2D(-30.0, -20.0), Point2D(30.0, -20.0), construction=True),
                CenterLine(Point2D(0.0, -30.0), Point2D(0.0, 30.0)),
                Circle(Point2D(-15.0, 10.0), 5.0),
                Arc(
                    center=Point2D(15.0, 10.0),
                    start=Point2D(20.0, 10.0),
                    end=Point2D(15.0, 15.0),
                    direction=ArcDirection.COUNTER_CLOCKWISE,
                ),
                Ellipse(
                    center=Point2D(0.0, 0.0),
                    major_axis_point=Point2D(10.0, 0.0),
                    minor_axis_point=Point2D(0.0, 4.0),
                ),
                SketchPoint(Point2D(22.0, -5.0)),
                Spline(
                    points=(
                        Point2D(-20.0, 20.0),
                        Point2D(-8.0, 25.0),
                        Point2D(8.0, 18.0),
                        Point2D(20.0, 24.0),
                    ),
                    degree=3,
                    construction=True,
                ),
            ),
        )
        snapshot = service.create(target, definition)
        if snapshot.entity_count != len(definition.entities):
            raise RuntimeError("lane adapter read-back entity count mismatch")

        def save_and_close(app: Any) -> None:
            nonlocal opened_title
            model = api.get_open_document(app, str(output))
            if model is None:
                raise RuntimeError("fixture document disappeared before save")
            success, errors, warnings = api.save_document(model)
            if not success or errors != 0:
                raise RuntimeError(f"fixture save failed: errors={errors}, warnings={warnings}")
            title = api.document_title(model)
            api.close_document(app, title)
            opened_title = None

        execute(save_and_close, stage="agent_a_fixture_save_close", mutation=True)

        def reopen_and_read(app: Any) -> dict[str, Any]:
            nonlocal reopened_title
            model, errors, warnings = api.open_document(
                app,
                str(output),
                _PART_DOC,
                read_only=False,
                silent=True,
                configuration="",
            )
            if model is None or errors != 0:
                raise RuntimeError(f"fixture reopen failed: errors={errors}, warnings={warnings}")
            reopened_title = api.document_title(model)
            feature = api._member(model, "FeatureByName", "AgentA_Breadth")
            if feature is None:
                raise RuntimeError("saved sketch feature missing after reopen")
            sketch = api._member(feature, "GetSpecificFeature2")
            if sketch is None:
                raise RuntimeError("saved sketch COM object missing after reopen")
            segments = _as_tuple(api._member(sketch, "GetSketchSegments"))
            sketch_points = _as_tuple(api._member(sketch, "GetSketchPoints2"))
            user_points = []
            user_point_types = []
            user_point_ids = []
            for point in sketch_points:
                try:
                    point_type = int(api._member(point, "Type"))
                except Exception:
                    point_type = None
                if point_type != 1:
                    continue
                user_points.append(point)
                user_point_types.append(point_type)
                try:
                    user_point_ids.append(list(_as_tuple(api._member(point, "GetID"))))
                except Exception:
                    user_point_ids.append([])
            construction_count = 0
            for segment in segments:
                try:
                    if bool(api._member(segment, "ConstructionGeometry")):
                        construction_count += 1
                except Exception:
                    continue
            report = rebuild_document(model, api)
            if not report.success:
                raise RuntimeError("saved/reopened artifact failed rebuild/error verification")
            return {
                "segment_count": len(segments),
                "user_point_count": len(user_points),
                "user_point_types": user_point_types,
                "user_point_ids": user_point_ids,
                "logical_entity_count": len(segments) + len(user_points),
                "construction_segment_count": construction_count,
                "feature_name": api.feature_name(feature),
                "revision": int(api.update_stamp(model) or 0),
            }

        reopen = execute(reopen_and_read, stage="agent_a_fixture_reopen_read", mutation=False)
        if reopen["logical_entity_count"] < len(definition.entities):
            raise RuntimeError("save/reopen native entity read-back is incomplete")

        # Recreate the lane runtime to prove persisted query does not depend on process-local
        # create caches. Plane identity is recovered from ISketch.GetReferenceEntity +
        # IRefPlane.Transform against standard-plane feature ordinals.
        fresh_runtime = SketchNativeRuntime(
            executor=execute,
            binding_resolver=resolve_binding,
            plane_selector=select_plane,
            sketch_feature_resolver=resolve_sketch_feature,
            member=api._member,
            rebuild_verifier=verify_rebuild,
        )
        persisted = SketchService(fresh_runtime).get(
            DocumentTarget(str(output), expected_revision=int(reopen["revision"]), expected_units="mm"),
            "AgentA_Breadth",
        )
        if persisted.plane != SketchPlane(PlaneKind.FRONT):
            raise RuntimeError(f"persisted plane read-back mismatch: {persisted.plane!r}")
        if persisted.entity_count != len(definition.entities):
            raise RuntimeError(
                "fresh-runtime persisted entity read-back mismatch: "
                f"expected={len(definition.entities)} actual={persisted.entity_count} "
                f"ids={persisted.entity_ids!r} reopen_point_types={reopen.get('user_point_types')!r}"
            )

        return {
            "host": "Slnc_TrZ",
            "solidworks_revision": info.revision,
            "solidworks_year": info.version_year,
            "artifact": str(output),
            "artifact_sha256": _sha256(output),
            "sketch_id": snapshot.sketch_id,
            "adapter_entity_count": snapshot.entity_count,
            "definition_state": snapshot.definition_state.value,
            "reopen": reopen,
            "fresh_runtime_readback": {
                "plane": persisted.plane.kind.value,
                "entity_count": persisted.entity_count,
                "entity_ids": list(persisted.entity_ids),
                "definition_state": persisted.definition_state.value,
            },
            "verdict": "NATIVE_PASS",
        }
    finally:
        if reopened_title is not None:
            try:
                execute(
                    lambda app: api.close_document(app, reopened_title),
                    stage="agent_a_fixture_close_reopened",
                    mutation=True,
                )
            except Exception:
                pass
        if opened_title is not None:
            try:
                execute(
                    lambda app: api.close_document(app, opened_title),
                    stage="agent_a_fixture_close_opened",
                    mutation=True,
                )
            except Exception:
                pass
        try:
            session.disconnect(timeout=10.0)
        finally:
            session.close_dispatcher(timeout=5.0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
