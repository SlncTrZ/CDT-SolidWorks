"""Real-SOLIDWORKS evidence runner for Agent C configuration capability.

Run on the Windows workstation that owns SOLIDWORKS 2024. This file is not a
pytest module on purpose; it creates bounded temporary native documents and emits
one JSON record suitable for the completion record.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from cdt_solidworks.configuration.domain import (
    ConfigurationRefusal,
    ConfigurationService,
)
from cdt_solidworks.configuration.native import ConfigurationNativeAdapter
from cdt_solidworks.document.path_policy import DocumentPathPolicy
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


def _open_document(session: SolidWorksSession, path: str, doc_type: int) -> None:
    def operation(app: Any) -> str:
        existing = session.api.get_open_document(app, path)
        if existing is not None:
            return session.api.document_title(existing)
        model, errors, warnings = session.api.open_document(
            app,
            path,
            doc_type,
            read_only=False,
            silent=True,
            configuration="",
        )
        if model is None or int(errors) != 0:
            raise RuntimeError(f"open failed errors={errors} warnings={warnings}")
        return session.api.document_title(model)

    _require(
        session.execute(
            operation,
            stage="evidence_open_document",
            timeout=30.0,
            mutation=True,
        ),
        "open_document",
    )


def _save_document(session: SolidWorksSession, path: str) -> None:
    def operation(app: Any) -> tuple[bool, int, int]:
        model = session.api.get_open_document(app, path)
        if model is None:
            raise RuntimeError("document is not open")
        return session.api.save_document(model)

    success, errors, warnings = _require(
        session.execute(
            operation,
            stage="evidence_save_document",
            timeout=30.0,
            mutation=True,
        ),
        "save_document",
    )
    if not success or int(errors) != 0:
        raise RuntimeError(f"save failed errors={errors} warnings={warnings}")


def _close_document(session: SolidWorksSession, path: str) -> None:
    def operation(app: Any) -> bool:
        model = session.api.get_open_document(app, path)
        if model is None:
            return False
        return bool(
            session.api.close_document(app, session.api.document_title(model))
        )

    _require(
        session.execute(
            operation,
            stage="evidence_close_document",
            timeout=20.0,
            mutation=True,
        ),
        "close_document",
    )


def _discover_dimension(session: SolidWorksSession, path: str) -> str:
    def operation(app: Any) -> str:
        model = session.api.get_open_document(app, path)
        if model is None:
            raise RuntimeError("document is not open")
        feature = session.api.first_feature(model)
        visited = 0
        while feature is not None:
            visited += 1
            if visited > 10_000:
                raise RuntimeError("dimension discovery limit exceeded")
            display = session.api._member(feature, "GetFirstDisplayDimension")
            while display is not None:
                try:
                    dimension = session.api._member(display, "GetDimension2", 0)
                    full_name = str(session.api._member(dimension, "FullName") or "")
                    if full_name and session.api._member(model, "Parameter", full_name) is not None:
                        return full_name
                except Exception:
                    pass
                display = session.api._member(feature, "GetNextDisplayDimension", display)
            feature = session.api.next_feature(feature)
        raise RuntimeError("no writable model dimension discovered")

    return str(
        _require(
            session.execute(
                operation,
                stage="evidence_discover_dimension",
                timeout=20.0,
                mutation=False,
            ),
            "discover_dimension",
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _material_readback(value: Any) -> tuple[str, str] | None:
    if value is None:
        return None
    return (value.database, value.name)


def run_configuration_evidence(
    session: SolidWorksSession, root: Path
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    part_path = root / "configured-part.SLDPRT"
    policy = DocumentPathPolicy((root,))
    cad = CadCoreService(session, path_policy=policy, default_timeout=60.0)
    created = _require(
        cad.create_rect_extrude(
            part_path,
            width_mm=80.0,
            height_mm=50.0,
            depth_mm=12.0,
        ),
        "create_configured_part",
    )
    native_path = str(Path(created["path"]).resolve())
    _open_document(session, native_path, 1)

    adapter = ConfigurationNativeAdapter(session, timeout=30.0)
    service = ConfigurationService(adapter)
    initial_names = service.list(native_path)
    default_name = service.activate(native_path, initial_names[0])

    # Derived configuration lifecycle and configuration-specific dimension state.
    service.create(native_path, "Alternate", parent=default_name)
    if adapter.read_configuration_parent(native_path, "Alternate") != default_name:
        raise RuntimeError("derived configuration parent did not read back")
    service.create(native_path, "DeleteMe", parent=default_name)
    service.rename(native_path, "DeleteMe", "DeleteMeRenamed")
    service.delete(native_path, "DeleteMeRenamed")

    dimension_name = _discover_dimension(session, native_path)
    default_dimension = adapter.read_dimension(native_path, default_name, dimension_name)
    if default_dimension is None:
        raise RuntimeError("default dimension read-back is missing")
    alternate_dimension = float(default_dimension) * 1.5
    service.set_dimension(
        native_path, "Alternate", dimension_name, alternate_dimension
    )

    # Document-level and configuration-level property isolation.
    service.set_property(native_path, None, "M90_RUN", "Agent-C")
    service.set_property(
        native_path, "Alternate", "Description", "Alternate native state"
    )

    # Display-state lifecycle. Keep one renamed state so save/reopen proves persistence.
    display_state = service.create_display_state(
        native_path, "Alternate", "M95 Inspection"
    )
    display_state = service.rename_display_state(
        native_path, "Alternate", display_state, "M95 Review"
    )
    if display_state not in service.list_display_states(native_path, "Alternate"):
        raise RuntimeError("display state did not read back after rename")

    # Material assignment is evidence-gated by an explicit installed database/name.
    material_database = os.environ.get("CDT_SW_MATERIAL_DATABASE")
    material_name = os.environ.get("CDT_SW_MATERIAL_NAME", "Plain Carbon Steel")
    if not material_database:
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidate = (
            program_files
            / "SOLIDWORKS Corp"
            / "SOLIDWORKS"
            / "lang"
            / "english"
            / "sldmaterials"
            / "solidworks materials.sldmat"
        )
        if candidate.is_file():
            material_database = str(candidate)
    if not material_database or not Path(material_database).is_file():
        raise RuntimeError("installed SOLIDWORKS material database was not found")
    assigned_material = service.set_material(
        native_path, "Alternate", material_database, material_name
    )

    # Equation/global-variable CRUD with evaluated read-back.
    equation = service.add_equation(native_path, '"M90_SCALE" = 2')
    equation = service.set_equation(native_path, equation.identity, '"M90_SCALE" = 3')
    if equation.value is None:
        raise RuntimeError("equation evaluation returned no value")

    feature_name = str(created["feature_name"])
    feature_state = service.set_feature_suppressed(
        native_path, "Alternate", feature_name, True
    )
    if feature_state != "suppressed":
        raise RuntimeError("alternate feature suppression did not read back")

    # Negative evidence: active configuration deletion must be refused pre-side-effect.
    service.activate(native_path, "Alternate")
    active_delete_reason = None
    try:
        service.delete(native_path, "Alternate")
    except ConfigurationRefusal as exc:
        active_delete_reason = exc.reason
    if active_delete_reason != "cannot_delete_active_configuration":
        raise RuntimeError(
            f"unexpected active-delete refusal: {active_delete_reason}"
        )
    service.activate(native_path, default_name)

    before_save = {
        "configurations": service.list(native_path),
        "alternate_parent": adapter.read_configuration_parent(native_path, "Alternate"),
        "alternate_material": _material_readback(
            adapter.read_material(native_path, "Alternate")
        ),
        "alternate_display_states": service.list_display_states(native_path, "Alternate"),
        "default_dimension": adapter.read_dimension(
            native_path, default_name, dimension_name
        ),
        "alternate_dimension": adapter.read_dimension(
            native_path, "Alternate", dimension_name
        ),
        "document_property": adapter.read_property(native_path, None, "M90_RUN"),
        "alternate_property": adapter.read_property(
            native_path, "Alternate", "Description"
        ),
        "alternate_feature_state": adapter.read_feature_state(
            native_path, "Alternate", feature_name
        ),
        "equations": tuple(
            (item.identity, item.expression, item.value, item.is_global_variable)
            for item in service.list_equations(native_path)
        ),
    }
    _save_document(session, native_path)
    _close_document(session, native_path)
    _open_document(session, native_path, 1)

    # Save/reopen parity: all durable state must be re-read from the native file.
    reopened_adapter = ConfigurationNativeAdapter(session, timeout=30.0)
    reopened_service = ConfigurationService(reopened_adapter)
    after_reopen = {
        "configurations": reopened_service.list(native_path),
        "alternate_parent": reopened_adapter.read_configuration_parent(
            native_path, "Alternate"
        ),
        "alternate_material": _material_readback(
            reopened_adapter.read_material(native_path, "Alternate")
        ),
        "alternate_display_states": reopened_service.list_display_states(
            native_path, "Alternate"
        ),
        "default_dimension": reopened_adapter.read_dimension(
            native_path, default_name, dimension_name
        ),
        "alternate_dimension": reopened_adapter.read_dimension(
            native_path, "Alternate", dimension_name
        ),
        "document_property": reopened_adapter.read_property(
            native_path, None, "M90_RUN"
        ),
        "alternate_property": reopened_adapter.read_property(
            native_path, "Alternate", "Description"
        ),
        "alternate_feature_state": reopened_adapter.read_feature_state(
            native_path, "Alternate", feature_name
        ),
        "equations": tuple(
            (item.identity, item.expression, item.value, item.is_global_variable)
            for item in reopened_service.list_equations(native_path)
        ),
    }
    if after_reopen != before_save:
        raise RuntimeError(
            f"configuration save/reopen parity mismatch: before={before_save!r} after={after_reopen!r}"
        )
    _close_document(session, native_path)

    return {
        "part_path": native_path,
        "part_sha256": _sha256(Path(native_path)),
        "feature_name": feature_name,
        "dimension_name": dimension_name,
        "active_delete_reason": active_delete_reason,
        "material_database": material_database,
        "material_name": material_name,
        "material_readback": {
            "database": assigned_material.database,
            "name": assigned_material.name,
        },
        "readback": after_reopen,
    }


def main() -> None:
    root = Path(
        os.environ.get(
            "CDT_SW_M90_C_EVIDENCE_ROOT",
            str(Path(os.environ.get("TEMP", r"C:\Temp")) / "cdt-sw-m90-c"),
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
        evidence = run_configuration_evidence(session, root)
        report.update(
            ok=True,
            verdict="PASS",
            solidworks_revision=connected.revision,
            solidworks_version_year=connected.version_year,
            session_ownership=connected.ownership.value,
            configuration=evidence,
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
