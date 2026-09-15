import unittest

from cdt_solidworks.assembly.domain import (
    AssemblyPostconditionError,
    AssemblyRefusal,
    AssemblyService,
    ComponentLoadState,
    ComponentSnapshot,
    MateKind,
    MateRequest,
    MateSnapshot,
    MateState,
    RebuildReport,
)


_IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


class _Adapter:
    def __init__(self) -> None:
        self.components = {}
        self.mates = {}
        self.last_request = None

    def insert_component(self, assembly_id, source_path, configuration):
        identity = "Bearing-1"
        self.components[identity] = ComponentSnapshot(
            identity=identity,
            source_path=source_path,
            configuration=configuration,
            transform=_IDENTITY,
            load_state=ComponentLoadState.RESOLVED,
        )
        return identity

    def read_component(self, assembly_id, component_id):
        try:
            return self.components[component_id]
        except KeyError as exc:
            raise AssemblyRefusal("missing_component", component_id) from exc

    def list_components(self, assembly_id, recursive):
        return tuple(self.components.values())

    def set_component_transform(self, assembly_id, component_id, transform):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=current.configuration,
            transform=tuple(transform),
            load_state=current.load_state,
            fixed=current.fixed,
            parent_identity=current.parent_identity,
        )

    def add_mate(self, assembly_id, request):
        self.last_request = request
        identity = "Concentric1"
        self.mates[identity] = MateSnapshot(
            identity=identity,
            state=MateState.SOLVED,
            component_ids=tuple(
                item for item in request.resolved_reference_component_ids if item is not None
            ),
            degrees_of_freedom=1,
            reference_component_ids=request.resolved_reference_component_ids,
            kind=request.kind,
            value=request.value,
            error_status=0,
        )
        return identity

    def read_mate(self, assembly_id, mate_id):
        try:
            return self.mates[mate_id]
        except KeyError as exc:
            raise AssemblyRefusal("missing_mate", mate_id) from exc

    def list_mates(self, assembly_id):
        return tuple(self.mates.values())

    def delete_mate(self, assembly_id, mate_id):
        self.mates.pop(mate_id, None)

    def rebuild_assembly(self, assembly_id):
        return RebuildReport(ok=True)


class Agent2AssemblyLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = _Adapter()
        self.service = AssemblyService(self.adapter)

    def test_insert_can_apply_explicit_transform_and_read_it_back(self):
        transform = list(_IDENTITY)
        transform[12:15] = (0.1, 0.2, 0.3)
        component = self.service.insert_component(
            "asm-1",
            "C:/project/bearing.SLDPRT",
            "Default",
            transform=transform,
        )
        self.assertEqual(tuple(transform), component.transform)
        self.assertEqual("Default", component.configuration)

    def test_opaque_topology_refs_use_resolved_pairing_without_parsing(self):
        request = MateRequest(
            kind=MateKind.CONCENTRIC,
            selection_refs=("swref1.shaft", "swref1.bearing"),
            resolved_entities=(object(), object()),
            resolved_reference_component_ids=("Shaft-1", "Bearing-1"),
        )
        mate = self.service.add_mate("asm-1", request)
        self.assertEqual(("Shaft-1", "Bearing-1"), mate.reference_component_ids)
        self.assertIs(request.resolved_entities[0], self.adapter.last_request.resolved_entities[0])

    def test_opaque_topology_pairing_readback_mismatch_fails_closed(self):
        original = self.adapter.add_mate

        def swapped(assembly_id, request):
            identity = original(assembly_id, request)
            current = self.adapter.mates[identity]
            self.adapter.mates[identity] = MateSnapshot(
                identity=current.identity,
                state=current.state,
                component_ids=current.component_ids,
                degrees_of_freedom=current.degrees_of_freedom,
                reference_component_ids=tuple(reversed(current.reference_component_ids)),
                kind=current.kind,
                value=current.value,
                error_status=current.error_status,
            )
            return identity

        self.adapter.add_mate = swapped
        with self.assertRaisesRegex(AssemblyPostconditionError, "mate_reference_pairing_mismatch"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    kind=MateKind.CONCENTRIC,
                    selection_refs=("swref1.shaft", "swref1.bearing"),
                    resolved_entities=(object(), object()),
                    resolved_reference_component_ids=("Shaft-1", "Bearing-1"),
                ),
            )

    def test_delete_mate_requires_absence_readback(self):
        request = MateRequest(
            kind=MateKind.CONCENTRIC,
            selection_refs=("swref1.shaft", "swref1.bearing"),
            resolved_entities=(object(), object()),
            resolved_reference_component_ids=("Shaft-1", "Bearing-1"),
        )
        mate = self.service.add_mate("asm-1", request)
        self.service.delete_mate("asm-1", mate.identity)
        self.assertEqual((), self.service.list_mates("asm-1"))


if __name__ == "__main__":
    unittest.main()
