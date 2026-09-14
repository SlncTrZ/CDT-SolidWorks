from __future__ import annotations

from pathlib import Path

from cdt_solidworks.assembly.domain import (
    AssemblyPostconditionError,
    ComponentLoadState,
    ComponentSnapshot,
    MateKind,
    MateSnapshot,
    MateState,
)
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.assembly_config import (
    IntegratedAssemblyService,
    IntegratedConfigurationService,
)
from cdt_solidworks.native.models import NativeCallState


class _AssemblyDomain:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.mates = {
            "Distance1": MateSnapshot("Distance1", MateState.SOLVED, (), 1, kind=MateKind.DISTANCE, value=0.02),
            "Angle1": MateSnapshot("Angle1", MateState.SOLVED, (), 1, kind=MateKind.ANGLE, value=0.2),
            "Coincident1": MateSnapshot("Coincident1", MateState.SOLVED, (), 1, kind=MateKind.COINCIDENT),
        }

    def list_components(self, path: str, *, recursive: bool = False):
        self.calls.append(("list_components", path, recursive))
        return (
            ComponentSnapshot("Part-1", r"C:\\fixture.SLDPRT", "Default", tuple(range(16)), ComponentLoadState.RESOLVED),
        )

    def set_component_fixed(self, path: str, component_id: str, fixed: bool):
        self.calls.append(("set_fixed", path, component_id, fixed))
        return ComponentSnapshot(component_id, r"C:\\fixture.SLDPRT", "Default", tuple(range(16)), ComponentLoadState.RESOLVED, fixed=fixed)

    def set_component_load_state(self, path: str, component_id: str, state: ComponentLoadState):
        self.calls.append(("set_load_state", path, component_id, state))
        return ComponentSnapshot(component_id, r"C:\\fixture.SLDPRT", "Default", tuple(range(16)), state)

    def set_component_configuration(self, path: str, component_id: str, configuration: str):
        self.calls.append(("set_configuration", path, component_id, configuration))
        return ComponentSnapshot(component_id, r"C:\\fixture.SLDPRT", configuration, tuple(range(16)), ComponentLoadState.RESOLVED)

    def add_mate(self, path: str, request):
        self.calls.append(("add_mate", path, request))
        return MateSnapshot("Mate1", MateState.SOLVED, (), 1, kind=request.kind, value=request.value)

    def list_mates(self, path: str):
        self.calls.append(("list_mates", path))
        return tuple(self.mates.values())

    def read_mate(self, path: str, mate_id: str):
        self.calls.append(("read_mate", path, mate_id))
        return self.mates[mate_id]

    def set_mate_suppressed(self, path: str, mate_id: str, suppressed: bool):
        self.calls.append(("set_mate_suppressed", path, mate_id, suppressed))
        current = self.mates[mate_id]
        return MateSnapshot(current.identity, MateState.SUPPRESSED if suppressed else MateState.SOLVED, (), 1, kind=current.kind, value=current.value)

    def set_mate_value(self, path: str, mate_id: str, value: float):
        self.calls.append(("set_mate_value", path, mate_id, value))
        current = self.mates[mate_id]
        return MateSnapshot(current.identity, current.state, (), 1, kind=current.kind, value=value)


class _ConfigurationDomain:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list(self, path: str):
        self.calls.append(("list", path)); return ("Default", "Alternate")

    def create(self, path: str, name: str, parent: str | None = None):
        self.calls.append(("create", path, name, parent)); return None

    def rename(self, path: str, old_name: str, new_name: str):
        self.calls.append(("rename", path, old_name, new_name)); return new_name

    def delete(self, path: str, name: str):
        self.calls.append(("delete", path, name)); return None

    def activate(self, path: str, name: str):
        self.calls.append(("activate", path, name)); return name

    def set_dimension(self, path: str, configuration: str, dimension_name: str, value: float):
        self.calls.append(("set_dimension", path, configuration, dimension_name, value)); return value

    def set_property(self, path: str, configuration: str | None, property_name: str, value: str):
        self.calls.append(("set_property", path, configuration, property_name, value)); return value

    def delete_property(self, path: str, configuration: str | None, property_name: str):
        self.calls.append(("delete_property", path, configuration, property_name)); return None

    def set_feature_suppressed(self, path: str, configuration: str, feature_id: str, suppressed: bool):
        self.calls.append(("set_feature_suppressed", path, configuration, feature_id, suppressed)); return "suppressed" if suppressed else "resolved"

    def list_equations(self, path: str):
        self.calls.append(("list_equations", path)); return ()

    def add_equation(self, path: str, expression: str):
        self.calls.append(("add_equation", path, expression)); return {"identity": '"X"', "expression": expression}

    def set_equation(self, path: str, identity: str, expression: str):
        self.calls.append(("set_equation", path, identity, expression)); return {"identity": identity, "expression": expression}

    def delete_equation(self, path: str, identity: str):
        self.calls.append(("delete_equation", path, identity)); return None


def _files(tmp_path: Path) -> tuple[Path, Path]:
    assembly = tmp_path / "fixture.SLDASM"; assembly.write_bytes(b"asm")
    part = tmp_path / "fixture.SLDPRT"; part.write_bytes(b"part")
    return assembly, part


def test_assembly_wrapper_limits_promoted_native_surface(tmp_path: Path) -> None:
    assembly, _ = _files(tmp_path)
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    listed = service.list_components(str(assembly), recursive=True)
    assert listed.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == ("list_components", str(assembly.resolve()), True)

    refused = service.set_component_load_state(str(assembly), "Part-1", "lightweight")
    assert refused.state is NativeCallState.FAILURE
    assert refused.failure is not None and refused.failure.code == "cad_validation_error"
    assert refused.dispatched is False

    mate = service.create_mate(
        str(assembly), kind="perpendicular",
        selection_refs=("Part-1:plane:front", "assembly:plane:front"),
        value=None, alignment=None,
    )
    assert mate.state is NativeCallState.SUCCESS

    unsupported = service.create_mate(
        str(assembly), kind="concentric",
        selection_refs=("Part-1:plane:front", "assembly:plane:front"),
        value=None, alignment=None,
    )
    assert unsupported.state is NativeCallState.FAILURE
    assert unsupported.failure is not None and unsupported.failure.code == "cad_validation_error"

    ignored_value = service.create_mate(
        str(assembly), kind="coincident",
        selection_refs=("Part-1:plane:front", "assembly:plane:front"),
        value=0.01, alignment="closest",
    )
    assert ignored_value.state is NativeCallState.FAILURE
    assert ignored_value.dispatched is False

    invalid_ref_count = service.create_mate(
        str(assembly), kind="parallel", selection_refs=("only-one",)
    )
    assert invalid_ref_count.state is NativeCallState.FAILURE
    assert invalid_ref_count.dispatched is False

    invalid_alignment = service.create_mate(
        str(assembly), kind="perpendicular",
        selection_refs=("Part-1:plane:front", "assembly:plane:front"),
        alignment="closest",
    )
    assert invalid_alignment.state is NativeCallState.FAILURE
    assert invalid_alignment.dispatched is False


def test_assembly_wrapper_guards_evidence_specific_mate_edits(tmp_path: Path) -> None:
    assembly, _ = _files(tmp_path)
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    wrong_value_kind = service.set_distance_mate_value(str(assembly), "Angle1", 0.5)
    assert wrong_value_kind.state is NativeCallState.FAILURE
    assert wrong_value_kind.failure is not None and wrong_value_kind.failure.code == "cad_validation_error"
    assert not any(call[0] == "set_mate_value" for call in domain.calls)

    updated = service.set_distance_mate_value(str(assembly), "Distance1", 0.03)
    assert updated.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == ("set_mate_value", str(assembly.resolve()), "Distance1", 0.03)

    wrong_suppress_kind = service.set_coincident_mate_suppressed(str(assembly), "Angle1", True)
    assert wrong_suppress_kind.state is NativeCallState.FAILURE
    assert not any(call[0] == "set_mate_suppressed" for call in domain.calls)

    suppressed = service.set_coincident_mate_suppressed(str(assembly), "Coincident1", True)
    assert suppressed.state is NativeCallState.SUCCESS


def test_configuration_wrapper_accepts_only_native_document_types(tmp_path: Path) -> None:
    _, part = _files(tmp_path)
    drawing = tmp_path / "fixture.SLDDRW"; drawing.write_bytes(b"drawing")
    domain = _ConfigurationDomain()
    service = IntegratedConfigurationService(path_policy=DocumentPathPolicy((tmp_path,)), service=domain)

    result = service.list(str(part))
    assert result.state is NativeCallState.SUCCESS
    assert domain.calls[-1] == ("list", str(part.resolve()))

    refused = service.list(str(drawing))
    assert refused.state is NativeCallState.FAILURE
    assert refused.failure is not None and refused.failure.code == "document_type_mismatch"
    assert refused.dispatched is False


def test_uncertain_domain_state_is_preserved_for_callers(tmp_path: Path) -> None:
    assembly, _ = _files(tmp_path)

    class _UncertainDomain(_AssemblyDomain):
        def set_component_fixed(self, path: str, component_id: str, fixed: bool):
            raise AssemblyPostconditionError(
                "native_state_uncertain",
                "call_id=native-call-42; Native operation timed out after dispatch.",
            )

    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=_UncertainDomain()
    )
    result = service.set_component_fixed(str(assembly), "Part-1", True)
    assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH
    assert result.call_id == "native-call-42"
    assert result.dispatched is True
