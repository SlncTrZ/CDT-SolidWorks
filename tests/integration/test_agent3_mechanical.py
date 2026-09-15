from __future__ import annotations

from dataclasses import dataclass

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.mechanical import IntegratedMechanicalService
from cdt_solidworks.mechanical.service import GearBuildMutationError
from cdt_solidworks.native.models import NativeCallResult, NativeCallState, NativeFailure


@dataclass
class Result:
    value: str = "ok"


class Service:
    def __init__(self):
        self.targets = []

    def create(self, target, name, spec):
        self.targets.append(target)
        return Result()

    def parameters_get(self, target, sketch_id, feature_id):
        self.targets.append(target)
        return {"sketch_id": sketch_id, "feature_id": feature_id}

    def parameter_set(self, target, feature_id, parameter, value):
        self.targets.append(target)
        return {"feature_id": feature_id, parameter: value}


def test_mechanical_wrapper_binds_configuration_and_refuses_bad_keyway(tmp_path) -> None:
    part = tmp_path / "gear.SLDPRT"
    part.write_bytes(b"part")
    domain = Service()
    service = IntegratedMechanicalService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )

    result = service.gear_create_spur(
        path=str(part),
        expected_revision=4,
        name="Gear1",
        tooth_count=24,
        module_mm=2.0,
        pressure_angle_deg=20.0,
        face_width_mm=12.0,
        bore_diameter_mm=8.0,
        configuration="Config-A",
    )
    assert result.state is NativeCallState.SUCCESS
    assert domain.targets[-1].expected_configuration == "Config-A"

    bad = service.gear_create_spur(
        path=str(part),
        expected_revision=4,
        name="Gear1",
        tooth_count=True,
        module_mm=2.0,
        pressure_angle_deg=20.0,
        face_width_mm=12.0,
        bore_diameter_mm=8.0,
    )
    assert bad.state is NativeCallState.FAILURE
    assert bad.dispatched is False


def test_mechanical_wrapper_preserves_original_uncertain_call_id(tmp_path) -> None:
    part = tmp_path / "gear.SLDPRT"
    part.write_bytes(b"part")
    original = NativeCallResult(
        state=NativeCallState.UNCERTAIN_AFTER_DISPATCH,
        call_id="native-origin-call",
        failure=NativeFailure(
            code="native_state_uncertain",
            stage="part_extrude_native",
            message="Native mutation outcome is uncertain.",
        ),
        dispatched=True,
    )

    class Interrupt(RuntimeError):
        def __init__(self):
            self.result = original

    class UncertainService(Service):
        def create(self, target, name, spec):
            raise Interrupt()

    service = IntegratedMechanicalService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=UncertainService()
    )
    result = service.gear_create_spur(
        path=str(part), expected_revision=2, name="Gear1", tooth_count=24,
        module_mm=2.0, pressure_angle_deg=20.0, face_width_mm=12.0, bore_diameter_mm=8.0,
    )
    assert result is original
    assert result.call_id == "native-origin-call"
    assert result.state is NativeCallState.UNCERTAIN_AFTER_DISPATCH


def test_partial_gear_build_failure_is_reported_as_dispatched(tmp_path) -> None:
    part = tmp_path / "gear.SLDPRT"
    part.write_bytes(b"part")

    class PartialService(Service):
        def create(self, target, name, spec):
            raise GearBuildMutationError("gear sketch exists but extrusion failed")

    service = IntegratedMechanicalService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=PartialService()
    )
    result = service.gear_create_spur(
        path=str(part), expected_revision=2, name="Gear1", tooth_count=24,
        module_mm=2.0, pressure_angle_deg=20.0, face_width_mm=12.0, bore_diameter_mm=8.0,
    )
    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is True
