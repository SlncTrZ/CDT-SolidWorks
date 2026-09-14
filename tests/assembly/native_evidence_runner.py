"""Real-SOLIDWORKS evidence runner for M95 Agent-C assembly capability."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from cdt_solidworks.assembly.domain import (
    AssemblyRefusal,
    AssemblyService,
    MateKind,
    MateRequest,
    MateState,
)
from cdt_solidworks.assembly.native import AssemblyNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native import AttachPolicy, NativeCallState, SolidWorksSession
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.errors import NativeRuntimeError


_STD_TANGENT_RAYS = (
    (
        0.0443527596829085,
        0.0297941775455115,
        0.00219712535516692,
        -0.400036026779312,
        -0.515038074910024,
        -0.758094294050284,
        0.00299151229353486,
        1,
    ),
    (
        0.130082347379926,
        0.0512737883206569,
        -0.0238688162796734,
        -0.400036026779312,
        -0.515038074910024,
        -0.758094294050284,
        0.00299151229353486,
        1,
    ),
)

_STD_CONCENTRIC_RAYS = (
    (
        0.153629139956536,
        0.0267016961580566,
        -0.0207225117635517,
        -0.400036026779312,
        -0.515038074910024,
        -0.758094294050284,
        0.00299151229353486,
        1,
    ),
    (
        0.31258643852118,
        0.0771807121026882,
        -0.175728481540773,
        -0.400036026779312,
        -0.515038074910024,
        -0.758094294050284,
        0.00299151229353486,
        1,
    ),
)

_WIDTH_RAYS = (
    (
        0.00868857956595548,
        0.0414144214960288,
        0.0633435410960033,
        -0.520148774728431,
        -0.59141018013918,
        -0.616181183562315,
        0.000468381592786756,
        1,
    ),
    (
        0.040068521476087,
        0.0399799509449394,
        0.0585753748188154,
        0.320596315934938,
        -0.422312467890091,
        -0.847862124212143,
        0.000468381592786756,
        1,
    ),
    (
        0.0313565896258297,
        0.0296508617577445,
        0.0442099188854286,
        0.340524666870961,
        -0.380278973953885,
        -0.859901653226112,
        0.000431713609895031,
        16,
    ),
    (
        0.0267766714922573,
        0.0259424893815421,
        0.0380784753310763,
        -0.849725692314326,
        -0.107993323108602,
        0.516046209156602,
        0.000431713609895031,
        16,
    ),
)

_SLOT_RAYS = (
    (
        0.12649032355856,
        0.133857958976421,
        0.00879467058769023,
        0.0353905007657348,
        0.579257713320296,
        0.814375843216443,
        0.00214371287556113,
        1,
    ),
    (
        0.0959257342875013,
        0.0999222046038994,
        -0.0251429018803719,
        0.0353905007657348,
        0.579257713320296,
        0.814375843216443,
        0.00214371287556113,
        1,
    ),
)


class EvidenceError(NativeRuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "assembly_evidence_failed",
            "assembly_evidence",
            message,
        )


def _require(result: Any, stage: str) -> Any:
    if result.state is not NativeCallState.SUCCESS:
        failure = result.failure
        raise EvidenceError(
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


def _windows_public_samples() -> Path:
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    return public / "Documents" / "SOLIDWORKS" / "SOLIDWORKS 2024" / "samples" / "tutorial" / "api"


def _open_document(
    session: SolidWorksSession,
    path: Path,
    doc_type: int,
    *,
    read_only: bool = False,
) -> None:
    source = str(path.resolve())

    def operation(app: Any) -> None:
        existing = session.api.get_open_document(app, source)
        if existing is not None:
            return
        model, errors, warnings = session.api.open_document(
            app,
            source,
            doc_type,
            read_only=read_only,
            silent=True,
            configuration="",
        )
        if model is None or int(errors) != 0:
            raise EvidenceError(
                f"open failed for {source}: errors={errors}, warnings={warnings}"
            )

    _require(
        session.execute(
            operation,
            stage="assembly_evidence_open",
            timeout=90.0,
            mutation=False,
        ),
        "open_document",
    )


def _save_document(session: SolidWorksSession, path: Path) -> None:
    source = str(path.resolve())

    def operation(app: Any) -> None:
        model = session.api.get_open_document(app, source)
        if model is None:
            raise EvidenceError(f"document not open: {source}")
        saved, errors, warnings = session.api.save_document(model)
        if not saved or int(errors) != 0:
            raise EvidenceError(
                f"save failed for {source}: errors={errors}, warnings={warnings}"
            )

    _require(
        session.execute(
            operation,
            stage="assembly_evidence_save",
            timeout=90.0,
            mutation=True,
        ),
        "save_document",
    )


def _close_document(session: SolidWorksSession, path: Path) -> None:
    source = str(path.resolve())

    def operation(app: Any) -> None:
        model = session.api.get_open_document(app, source)
        if model is not None:
            session.api.close_document(app, session.api.document_title(model))

    _require(
        session.execute(
            operation,
            stage="assembly_evidence_close",
            timeout=30.0,
            mutation=False,
        ),
        "close_document",
    )


def _name_first_edge(session: SolidWorksSession, part_path: Path, name: str) -> None:
    source = str(part_path.resolve())

    def operation(app: Any) -> None:
        model, errors, warnings = session.api.open_document(
            app,
            source,
            1,
            read_only=False,
            silent=True,
            configuration="",
        )
        if model is None or int(errors) != 0:
            raise EvidenceError(
                f"edge-name open failed: errors={errors}, warnings={warnings}"
            )
        try:
            bodies = session.api.bodies(model, 0, False)
            if len(bodies) != 1:
                raise EvidenceError(f"expected one solid body, got {len(bodies)}")
            raw_edges = session.api._member(bodies[0], "GetEdges")
            edges = () if raw_edges is None else tuple(raw_edges)
            if not edges:
                raise EvidenceError("part has no edge to name")
            edge = edges[0]
            ok = bool(session.api._member(model, "SetEntityName", edge, name))
            actual = str(session.api._member(model, "GetEntityName", edge) or "")
            if not ok and actual != name:
                raise EvidenceError(f"SetEntityName failed: actual={actual!r}")
            saved, save_errors, save_warnings = session.api.save_document(model)
            if not saved or int(save_errors) != 0:
                raise EvidenceError(
                    "edge-name save failed: "
                    f"errors={save_errors}, warnings={save_warnings}"
                )
        finally:
            session.api.close_document(app, session.api.document_title(model))

    _require(
        session.execute(
            operation,
            stage="assembly_evidence_name_edge",
            timeout=90.0,
            mutation=True,
        ),
        "name_first_edge",
    )


def _save_as_current_model(
    session: SolidWorksSession, source_path: Path, target_path: Path
) -> None:
    source = str(source_path.resolve())
    target = str(target_path.resolve())

    def operation(app: Any) -> None:
        model = session.api.get_open_document(app, source)
        if model is None:
            raise EvidenceError(f"source assembly not open: {source}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.unlink(missing_ok=True)
        saved, errors, warnings = session.api.save_as(model, target)
        if not saved or int(errors) != 0 or not target_path.is_file():
            raise EvidenceError(
                f"save-as failed: errors={errors}, warnings={warnings}"
            )

    _require(
        session.execute(
            operation,
            stage="assembly_evidence_save_as",
            timeout=90.0,
            mutation=True,
        ),
        "save_as_current_model",
    )


def _normalize_persistent_reference(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (bytes, bytearray)):
        return tuple(value)
    if isinstance(value, (tuple, list)):
        return tuple(int(item) for item in value)
    try:
        return tuple(int(item) for item in value)
    except TypeError:
        return (int(value),)


def _underlying_part_face_by_persistent_reference(
    session: SolidWorksSession,
    assembly_extension: Any,
    component: Any,
    selected_face: Any,
    *,
    selection_index: int,
) -> tuple[Any, Any]:
    part_model = session.api._member(component, "GetModelDoc2")
    if part_model is None:
        raise EvidenceError(f"component model unresolved at index {selection_index}")

    selected_ref = _normalize_persistent_reference(
        session.api._member(
            assembly_extension, "GetPersistReference3", selected_face
        )
    )
    if not selected_ref:
        raise EvidenceError(
            f"selected face persistent reference missing at index {selection_index}"
        )

    matches: list[Any] = []
    for body in session.api.bodies(part_model, 0, False):
        raw_faces = session.api._member(body, "GetFaces")
        if raw_faces is None:
            faces: tuple[Any, ...] = ()
        elif isinstance(raw_faces, (tuple, list)):
            faces = tuple(raw_faces)
        else:
            try:
                faces = tuple(raw_faces)
            except TypeError:
                faces = (raw_faces,)

        for part_face in faces:
            assembly_face = session.api._member(
                component, "GetCorrespondingEntity", part_face
            )
            if assembly_face is None:
                continue
            candidate_ref = _normalize_persistent_reference(
                session.api._member(
                    assembly_extension, "GetPersistReference3", assembly_face
                )
            )
            if candidate_ref == selected_ref:
                matches.append(part_face)

    if len(matches) != 1:
        component_name = session.api.component_name(component)
        raise EvidenceError(
            "persistent-reference face recovery was not unique at "
            f"index {selection_index}: component={component_name!r}; "
            f"matches={len(matches)}"
        )
    return part_model, matches[0]


def _select_rays_and_name_faces(
    session: SolidWorksSession,
    assembly_path: Path,
    rays: Iterable[tuple[float, float, float, float, float, float, float, int]],
    names: tuple[str, ...],
) -> tuple[str, ...]:
    source = str(assembly_path.resolve())
    ray_values = tuple(rays)
    if len(ray_values) != len(names):
        raise EvidenceError("ray/name count mismatch")

    def operation(app: Any) -> tuple[str, ...]:
        model = session.api.get_open_document(app, source)
        if model is None:
            raise EvidenceError(f"assembly not open: {source}")
        extension = session.api._member(model, "Extension")
        selection_manager = session.api._member(model, "SelectionManager")
        session.api._member(model, "ClearSelection2", True)
        refs: list[str] = []
        try:
            for ray in ray_values:
                x, y, z, dx, dy, dz, radius, mark = ray
                selected = bool(
                    session.api._member(
                        extension,
                        "SelectByRay",
                        x,
                        y,
                        z,
                        dx,
                        dy,
                        dz,
                        radius,
                        2,
                        True,
                        mark,
                        0,
                    )
                )
                if not selected:
                    raise EvidenceError(f"SelectByRay failed for ray={ray!r}")

            for index, entity_name in enumerate(names, start=1):
                face = session.api._member(
                    selection_manager, "GetSelectedObject6", index, -1
                )
                component = session.api._member(
                    selection_manager, "GetSelectedObjectsComponent4", index, -1
                )
                if face is None or component is None:
                    raise EvidenceError(
                        f"selected face/component missing at index {index}"
                    )
                part_model, underlying = _underlying_part_face_by_persistent_reference(
                    session,
                    extension,
                    component,
                    face,
                    selection_index=index,
                )
                ok = bool(
                    session.api._member(
                        part_model, "SetEntityName", underlying, entity_name
                    )
                )
                actual = str(
                    session.api._member(part_model, "GetEntityName", underlying) or ""
                )
                if not ok and actual != entity_name:
                    raise EvidenceError(
                        f"face naming failed at index {index}: {actual!r}"
                    )
                refs.append(
                    f"{session.api.component_name(component)}:face:{entity_name}"
                )
        finally:
            session.api._member(model, "ClearSelection2", True)
        return tuple(refs)

    return tuple(
        _require(
            session.execute(
                operation,
                stage="assembly_evidence_name_faces",
                timeout=90.0,
                mutation=True,
            ),
            "select_rays_and_name_faces",
        )
    )


def _open_standard_mate_fixture(
    session: SolidWorksSession, sample_root: Path
) -> tuple[Path, str]:
    assembly = sample_root / "assem20.sldasm"
    shaft = sample_root / "shaft.sldprt"
    if not assembly.is_file() or not shaft.is_file():
        raise EvidenceError("standard-mate SOLIDWORKS samples are missing")
    _open_document(session, assembly, 2, read_only=False)

    def operation(app: Any) -> str:
        model = session.api.get_open_document(app, str(assembly.resolve()))
        if model is None:
            raise EvidenceError("standard mate assembly did not remain open")
        shaft_model = session.api.get_open_document(app, str(shaft.resolve()))
        opened_shaft = False
        if shaft_model is None:
            shaft_model, errors, warnings = session.api.open_document(
                app,
                str(shaft.resolve()),
                1,
                read_only=True,
                silent=True,
                configuration="",
            )
            if shaft_model is None or int(errors) != 0:
                raise EvidenceError(
                    f"shaft open failed: errors={errors}, warnings={warnings}"
                )
            opened_shaft = True
        try:
            component = session.api._member(
                model,
                "AddComponent5",
                str(shaft.resolve()),
                0,
                "",
                False,
                "",
                0.29642267129384,
                0.0920506109250709,
                -0.187506963149644,
            )
            if component is None:
                raise EvidenceError("shaft insertion failed")
            values = (
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.29642267129384,
                0.0420506109250709,
                -0.187506963149644,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            transform = session.api._member(component, "Transform2")
            if transform is None:
                raise EvidenceError("shaft transform missing")
            transform.ArrayData = AssemblyNativeAdapter(session)._double_array(values)
            solved = bool(
                session.api._member(component, "SetTransformAndSolve2", transform)
            )
            if not solved:
                raise EvidenceError("shaft transform solve failed")
            return session.api.component_name(component)
        finally:
            if opened_shaft and shaft_model is not None:
                session.api.close_document(
                    app, session.api.document_title(shaft_model)
                )

    shaft_id = str(
        _require(
            session.execute(
                operation,
                stage="assembly_evidence_standard_fixture",
                timeout=90.0,
                mutation=True,
            ),
            "open_standard_mate_fixture",
        )
    )
    return assembly, shaft_id


def _verify_saved_mates(
    session: SolidWorksSession,
    artifact: Path,
    expected_ids: tuple[str, ...],
) -> dict[str, Any]:
    _open_document(session, artifact, 2, read_only=False)
    service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    assembly_id = str(artifact.resolve())
    mates = service.list_mates(assembly_id)
    by_id = {mate.identity: mate for mate in mates}
    missing = tuple(identity for identity in expected_ids if identity not in by_id)
    if missing:
        raise EvidenceError(f"saved mates missing after reopen: {missing!r}")
    states = {
        identity: {
            "state": by_id[identity].state.value,
            "kind": by_id[identity].kind.value if by_id[identity].kind else None,
            "error_status": by_id[identity].error_status,
        }
        for identity in expected_ids
    }
    _close_document(session, artifact)
    return states


def run_lifecycle_evidence(
    session: SolidWorksSession, root: Path
) -> dict[str, Any]:
    policy = DocumentPathPolicy((root,))
    cad = CadCoreService(session, path_policy=policy, default_timeout=90.0)
    part_a = root / "lifecycle-a.SLDPRT"
    part_b = root / "lifecycle-b.SLDPRT"
    replacement = root / "lifecycle-replacement.SLDPRT"
    nested = root / "lifecycle-nested.SLDASM"
    assembly = root / "lifecycle-main.SLDASM"

    _require(
        cad.create_rect_extrude(
            part_a, width_mm=30.0, height_mm=20.0, depth_mm=10.0
        ),
        "create lifecycle-a",
    )
    _require(
        cad.create_rect_extrude(
            part_b, width_mm=24.0, height_mm=18.0, depth_mm=8.0
        ),
        "create lifecycle-b",
    )
    _require(
        cad.create_rect_extrude(
            replacement, width_mm=26.0, height_mm=16.0, depth_mm=12.0
        ),
        "create lifecycle-replacement",
    )
    _name_first_edge(session, part_a, "pattern-axis")

    nested_info = _require(
        cad.create_assembly(
            nested,
            component_paths=(str(part_a.resolve()), str(part_b.resolve())),
            placements_mm=((0.0, 0.0, 0.0), (45.0, 0.0, 0.0)),
        ),
        "create nested assembly",
    )
    main_info = _require(
        cad.create_assembly(
            assembly,
            component_paths=(
                str(part_a.resolve()),
                str(part_b.resolve()),
                str(nested.resolve()),
                str(part_a.resolve()),
            ),
            placements_mm=(
                (-100.0, 0.0, 0.0),
                (-40.0, 0.0, 0.0),
                (40.0, 0.0, 0.0),
                (120.0, 0.0, 0.0),
            ),
        ),
        "create main assembly",
    )
    ids = tuple(str(value) for value in main_info["components"])
    if len(ids) != 4:
        raise EvidenceError(f"expected four top-level components, got {ids!r}")

    _open_document(session, assembly, 2, read_only=False)
    service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    assembly_id = str(assembly.resolve())
    recursive_before = service.list_components(assembly_id, recursive=True)
    nested_children = tuple(
        item for item in recursive_before if item.parent_identity is not None
    )
    if len(recursive_before) < 6 or len(nested_children) < 2:
        raise EvidenceError(
            "nested traversal did not expose expected recursive component identities"
        )

    delete_id = ids[0]
    replace_id = ids[1]
    seed_id = ids[3]
    service.delete_component(assembly_id, delete_id)
    replaced = service.replace_component(
        assembly_id, replace_id, str(replacement.resolve()), None
    )
    transform = (
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.12,
        0.03,
        0.01,
        1.0,
        0.0,
        0.0,
        0.0,
    )
    moved = service.set_component_transform(assembly_id, seed_id, transform)
    pattern = service.create_linear_component_pattern(
        assembly_id,
        seed_component_ids=(seed_id,),
        direction_ref=f"{seed_id}:edge:pattern-axis",
        spacing_m=0.04,
        total_instances=3,
    )
    after = service.list_components(assembly_id, recursive=True)
    if delete_id in {item.identity for item in after}:
        raise EvidenceError("deleted component remained after lifecycle mutations")
    _save_document(session, assembly)
    _close_document(session, assembly)

    _open_document(session, assembly, 2, read_only=False)
    reopened_service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    reopened = reopened_service.list_components(assembly_id, recursive=True)
    reopened_by_id = {item.identity: item for item in reopened}
    if delete_id in reopened_by_id:
        raise EvidenceError("deleted component returned after reopen")
    if replaced.identity not in reopened_by_id:
        raise EvidenceError("replacement identity missing after reopen")
    if reopened_by_id[replaced.identity].source_path != str(replacement.resolve()):
        raise EvidenceError("replacement source path did not persist")
    reopened_seed = reopened_by_id.get(seed_id)
    if reopened_seed is None:
        raise EvidenceError("moved seed component missing after reopen")
    if any(
        not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
        for actual, expected in zip(reopened_seed.transform, transform, strict=True)
    ):
        raise EvidenceError("component transform did not persist")
    reopened_pattern = reopened_service._adapter.read_component_pattern(
        assembly_id, pattern.identity
    )
    if reopened_pattern.total_instances != 3 or not reopened_pattern.direction_resolved:
        raise EvidenceError("component pattern did not persist")
    if not any(item.parent_identity is not None for item in reopened):
        raise EvidenceError("nested parent identity missing after reopen")
    _close_document(session, assembly)

    return {
        "assembly": str(assembly.resolve()),
        "assembly_sha256": _sha256(assembly),
        "nested_assembly": str(nested.resolve()),
        "nested_component_count": nested_info["component_count"],
        "recursive_component_count_before": len(recursive_before),
        "nested_children_before": [item.identity for item in nested_children],
        "deleted_component": delete_id,
        "replacement_component": replaced.identity,
        "replacement_source": replaced.source_path,
        "moved_component": moved.identity,
        "transform": list(transform),
        "pattern": {
            "identity": pattern.identity,
            "seed_component_ids": list(pattern.seed_component_ids),
            "spacing_m": pattern.spacing_m,
            "total_instances": pattern.total_instances,
            "direction_resolved": pattern.direction_resolved,
        },
        "recursive_component_count_after_reopen": len(reopened),
    }


def run_standard_mates_evidence(
    session: SolidWorksSession, root: Path, sample_root: Path
) -> dict[str, Any]:
    source, shaft_id = _open_standard_mate_fixture(session, sample_root)
    service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    assembly_id = str(source.resolve())

    tangent_refs = _select_rays_and_name_faces(
        session,
        source,
        _STD_TANGENT_RAYS,
        ("m95-tangent-a", "m95-tangent-b"),
    )
    tangent = service.add_mate(
        assembly_id,
        MateRequest(
            kind=MateKind.TANGENT,
            selection_refs=tangent_refs,
            alignment="anti_aligned",
        ),
    )
    tangent_suppressed = service.set_mate_suppressed(
        assembly_id, tangent.identity, True
    )
    if tangent_suppressed.state is not MateState.SUPPRESSED:
        raise EvidenceError("tangent mate did not suppress")
    tangent_unsuppressed = service.set_mate_suppressed(
        assembly_id, tangent.identity, False
    )
    if tangent_unsuppressed.state is not MateState.SOLVED:
        raise EvidenceError("tangent mate did not restore to solved state")

    concentric_refs = _select_rays_and_name_faces(
        session,
        source,
        _STD_CONCENTRIC_RAYS,
        ("m95-concentric-a", "m95-concentric-b"),
    )
    concentric = service.add_mate(
        assembly_id,
        MateRequest(
            kind=MateKind.CONCENTRIC,
            selection_refs=concentric_refs,
            alignment="anti_aligned",
        ),
    )

    components = service.list_components(assembly_id, recursive=False)
    cylinder = next(
        (item.identity for item in components if item.identity.casefold().startswith("cylinder20")),
        None,
    )
    if cylinder is None:
        raise EvidenceError("cylinder20 component missing in standard mate fixture")
    lock_mate = service.add_mate(
        assembly_id,
        MateRequest(
            kind=MateKind.LOCK,
            selection_refs=(f"component:{cylinder}", f"component:{shaft_id}"),
        ),
    )

    try:
        service.add_mate(
            assembly_id,
            MateRequest(
                kind=MateKind.TANGENT,
                selection_refs=("assembly:plane:front", "assembly:plane:top"),
                value=1.0,
            ),
        )
    except AssemblyRefusal as exc:
        invalid_reason = exc.reason
    else:
        raise EvidenceError("invalid tangent request was not refused")

    artifact = root / "standard-mates.SLDASM"
    _save_as_current_model(session, source, artifact)
    # SaveAs renames the open document to the target. Close by target path.
    _close_document(session, artifact)
    states = _verify_saved_mates(
        session,
        artifact,
        (tangent.identity, concentric.identity, lock_mate.identity),
    )
    return {
        "source": str(source),
        "artifact": str(artifact.resolve()),
        "artifact_sha256": _sha256(artifact),
        "shaft_component": shaft_id,
        "tangent_refs": tangent_refs,
        "concentric_refs": concentric_refs,
        "mates": {
            "tangent": tangent.identity,
            "concentric": concentric.identity,
            "lock": lock_mate.identity,
        },
        "reopen_states": states,
        "invalid_request_reason": invalid_reason,
        "suppression_roundtrip": {
            "suppressed": tangent_suppressed.state.value,
            "unsuppressed": tangent_unsuppressed.state.value,
        },
    }


def run_mate_status_evidence(
    session: SolidWorksSession, sample_root: Path
) -> dict[str, Any]:
    source = sample_root / "repairassemmates.sldasm"
    if not source.is_file():
        raise EvidenceError("mate-status SOLIDWORKS sample is missing")
    _open_document(session, source, 2, read_only=True)
    try:
        service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
        mates = service.list_mates(str(source.resolve()))
        dangling = tuple(mate for mate in mates if mate.state is MateState.DANGLING)
        if not dangling:
            observed = tuple((mate.identity, mate.state.value) for mate in mates)
            raise EvidenceError(
                f"repairassemmates sample exposed no dangling mate: {observed!r}"
            )
        return {
            "source": str(source.resolve()),
            "mate_count": len(mates),
            "dangling": [
                {
                    "identity": mate.identity,
                    "kind": mate.kind.value if mate.kind else None,
                    "state": mate.state.value,
                    "error_status": mate.error_status,
                    "rebuild_errors": list(mate.rebuild_errors),
                }
                for mate in dangling
            ],
        }
    finally:
        _close_document(session, source)


def run_width_mate_evidence(
    session: SolidWorksSession, root: Path, sample_root: Path
) -> dict[str, Any]:
    source = sample_root / "AdvancedMates" / "advancedmatedemo1.sldasm"
    if not source.is_file():
        raise EvidenceError("width-mate SOLIDWORKS sample is missing")
    _open_document(session, source, 2, read_only=False)
    refs = _select_rays_and_name_faces(
        session,
        source,
        _WIDTH_RAYS,
        ("m95-width-a", "m95-width-b", "m95-tab-a", "m95-tab-b"),
    )
    service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    mate = service.add_mate(
        str(source.resolve()),
        MateRequest(
            kind=MateKind.WIDTH,
            selection_refs=refs,
            constraint="centered",
        ),
    )
    artifact = root / "width-mate.SLDASM"
    _save_as_current_model(session, source, artifact)
    _close_document(session, artifact)
    states = _verify_saved_mates(session, artifact, (mate.identity,))
    return {
        "source": str(source),
        "artifact": str(artifact.resolve()),
        "artifact_sha256": _sha256(artifact),
        "refs": refs,
        "mate": mate.identity,
        "reopen_states": states,
    }


def run_slot_mate_evidence(
    session: SolidWorksSession, root: Path, sample_root: Path
) -> dict[str, Any]:
    source = sample_root / "MechanicalMates" / "slot_slot.sldasm"
    if not source.is_file():
        raise EvidenceError("slot-mate SOLIDWORKS sample is missing")
    _open_document(session, source, 2, read_only=False)
    refs = _select_rays_and_name_faces(
        session,
        source,
        _SLOT_RAYS,
        ("m95-slot-a", "m95-slot-b"),
    )
    service = AssemblyService(AssemblyNativeAdapter(session, timeout=60.0))
    mate = service.add_mate(
        str(source.resolve()),
        MateRequest(
            kind=MateKind.SLOT,
            selection_refs=refs,
            alignment="aligned",
            constraint="free",
        ),
    )
    artifact = root / "slot-mate.SLDASM"
    _save_as_current_model(session, source, artifact)
    _close_document(session, artifact)
    states = _verify_saved_mates(session, artifact, (mate.identity,))
    return {
        "source": str(source),
        "artifact": str(artifact.resolve()),
        "artifact_sha256": _sha256(artifact),
        "refs": refs,
        "mate": mate.identity,
        "reopen_states": states,
    }


def main() -> None:
    root = Path(
        os.environ.get(
            "CDT_SW_M95_C_ASSEMBLY_ROOT",
            str(Path(os.environ.get("TEMP", r"C:\Temp")) / "cdt-sw-m95-c-assembly"),
        )
    ).resolve()
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    sample_root = _windows_public_samples()
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
        report.update(
            solidworks_revision=connected.revision,
            solidworks_version_year=connected.version_year,
            session_ownership=connected.ownership.value,
        )
        lifecycle = run_lifecycle_evidence(session, root)
        standard_mates = run_standard_mates_evidence(session, root, sample_root)
        mate_status = run_mate_status_evidence(session, sample_root)
        width = run_width_mate_evidence(session, root, sample_root)
        slot = run_slot_mate_evidence(session, root, sample_root)
        report.update(
            ok=True,
            verdict="PASS",
            lifecycle=lifecycle,
            standard_mates=standard_mates,
            mate_status=mate_status,
            width_mate=width,
            slot_mate=slot,
        )
    except BaseException as exc:
        report.update(
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        raise
    finally:
        try:
            session.disconnect(timeout=30.0)
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
