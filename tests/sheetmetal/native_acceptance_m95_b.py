"""Native acceptance for Agent B fabrication body and sheet-metal promotion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cdt_solidworks.body.native import BodyNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.native.cad_core import CadCoreService
from cdt_solidworks.native.models import NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession
from cdt_solidworks.sheetmetal.native import SheetMetalNativeAdapter

EVIDENCE = Path(__file__).resolve().parent / "_native_evidence" / "m95-b"
ARTIFACTS = EVIDENCE / "artifacts"
OUT = EVIDENCE / "native_acceptance.json"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def require(result, label: str):
    if result.state is not NativeCallState.SUCCESS:
        raise RuntimeError(f"{label}: {result.state.value}: {result.failure}")
    return result.value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_overlap(core: CadCoreService, path: Path) -> None:
    require(
        core.create_rect_extrude(path, width_mm=40, height_mm=40, depth_mm=10, timeout=90),
        "body base",
    )
    added = require(
        core.add_rect_extrude(
            path,
            width_mm=40,
            height_mm=40,
            depth_mm=10,
            center_x_mm=20,
            merge=False,
            timeout=90,
        ),
        "body second extrusion",
    )
    if int(added["body_count"]) != 2:
        raise RuntimeError(f"expected two solid bodies, got {added}")


def body_acceptance(session: SolidWorksSession, policy: DocumentPathPolicy) -> dict:
    core = CadCoreService(session, path_policy=policy, default_timeout=90)
    adapter = BodyNativeAdapter(session, path_policy=policy, default_timeout=90)
    path = ARTIFACTS / "body-move-copy-delete.sldprt"
    path.unlink(missing_ok=True)
    create_overlap(core, path)

    before = require(adapter.inspect(path, timeout=60), "body inspect before")
    names = tuple(item["name"] for item in before["solid_bodies"])
    if len(names) != 2 or any(not name for name in names):
        raise RuntimeError(f"invalid initial body identity read-back: {before}")

    copied = require(
        adapter.move_copy(
            path,
            body_names=(names[0],),
            translation_mm=(60.0, 0.0, 0.0),
            copy=True,
            copies=1,
            timeout=90,
        ),
        "body move/copy",
    )
    if int(copied["body_count_after"]) != 3:
        raise RuntimeError(f"move/copy body count mismatch: {copied}")

    persisted_copy = require(adapter.inspect(path, timeout=60), "body inspect after copy")
    current_names = tuple(item["name"] for item in persisted_copy["solid_bodies"])
    if len(current_names) != 3 or any(not name for name in current_names):
        raise RuntimeError(f"copy did not persist three named bodies: {persisted_copy}")

    deleted = require(
        adapter.delete_keep(
            path,
            body_names=(current_names[-1],),
            keep=False,
            timeout=90,
        ),
        "body delete",
    )
    if int(deleted["body_count_after"]) != 2:
        raise RuntimeError(f"delete body count mismatch: {deleted}")

    persisted_delete = require(adapter.inspect(path, timeout=60), "body inspect after delete")
    if len(persisted_delete["solid_bodies"]) != 2:
        raise RuntimeError(f"delete did not persist two bodies: {persisted_delete}")

    return {
        "move_copy": copied,
        "delete": deleted,
        "persisted": persisted_delete,
        "artifact": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        },
    }


def sheetmetal_acceptance(session: SolidWorksSession, policy: DocumentPathPolicy) -> dict:
    adapter = SheetMetalNativeAdapter(session, path_policy=policy, default_timeout=90)
    path = ARTIFACTS / "sheet-metal-hem.sldprt"
    path.unlink(missing_ok=True)

    created = require(
        adapter.create_base_flange(
            path,
            width_mm=100.0,
            height_mm=60.0,
            thickness_mm=2.0,
            bend_radius_mm=1.5,
            timeout=90,
        ),
        "sheet-metal base flange",
    )
    if int(created["body_count"]) != 1:
        raise RuntimeError(f"base flange body count mismatch: {created}")

    hem = require(
        adapter.add_hem(
            path,
            edge_selector="bbox:+x",
            length_mm=12.0,
            gap_mm=0.5,
            position="outside",
            timeout=90,
        ),
        "sheet-metal hem",
    )
    if abs(float(hem["length_mm"]) - 12.0) > 1e-6:
        raise RuntimeError(f"hem length mismatch: {hem}")
    if abs(float(hem["gap_mm"]) - 0.5) > 1e-6:
        raise RuntimeError(f"hem gap mismatch: {hem}")

    persisted_hem = require(adapter.inspect(path, timeout=60), "sheet-metal inspect after hem")
    if not persisted_hem["is_sheet_metal"] or persisted_hem["flattened"]:
        raise RuntimeError(f"formed hem state did not persist: {persisted_hem}")

    flat = require(adapter.set_flattened(path, flattened=True, timeout=90), "sheet-metal flatten")
    if not flat["flattened"] or not flat["flat_pattern_name"]:
        raise RuntimeError(f"flat pattern identity missing: {flat}")
    persisted_flat = require(adapter.inspect(path, timeout=60), "sheet-metal inspect flat")
    if not persisted_flat["flattened"] or not persisted_flat["flat_pattern_name"]:
        raise RuntimeError(f"flat state did not persist: {persisted_flat}")

    formed = require(adapter.set_flattened(path, flattened=False, timeout=90), "sheet-metal form")
    if formed["flattened"]:
        raise RuntimeError(f"formed state read-back mismatch: {formed}")
    persisted_formed = require(adapter.inspect(path, timeout=60), "sheet-metal inspect formed")
    if persisted_formed["flattened"]:
        raise RuntimeError(f"formed state did not persist: {persisted_formed}")

    return {
        "hem": hem,
        "flat": flat,
        "formed": formed,
        "persisted": persisted_formed,
        "artifact": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        },
    }


session = SolidWorksSession()
report: dict = {"ok": False}
try:
    info = require(
        session.connect(
            policy=AttachPolicy.ATTACH_OR_START,
            version=2024,
            visible=True,
            timeout=180,
        ),
        "connect",
    )
    policy = DocumentPathPolicy((ARTIFACTS,))
    report = {
        "ok": True,
        "verdict": "NATIVE_PASS",
        "solidworks_revision": info.revision,
        "solidworks_year": info.version_year,
        "body": body_acceptance(session, policy),
        "sheetmetal": sheetmetal_acceptance(session, policy),
    }
except BaseException as exc:
    report = {
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
finally:
    try:
        session.disconnect(timeout=15)
    except Exception:
        pass
    try:
        session.close_dispatcher(timeout=5)
    except Exception:
        pass
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

if not report["ok"]:
    raise SystemExit(1)
