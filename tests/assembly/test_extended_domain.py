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
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


class ExtendedAssemblyAdapter:
    def __init__(self):
        self.components = {
            "Base-1": ComponentSnapshot(
                identity="Base-1",
                source_path=r"C:\fixtures\base.SLDPRT",
                configuration="Default",
                transform=_IDENTITY,
                load_state=ComponentLoadState.RESOLVED,
                fixed=True,
            ),
            "Bracket-1": ComponentSnapshot(
                identity="Bracket-1",
                source_path=r"C:\fixtures\bracket.SLDPRT",
                configuration="Default",
                transform=_IDENTITY,
                load_state=ComponentLoadState.RESOLVED,
                fixed=False,
            ),
        }
        self.mates = {
            "MateCoincident1": MateSnapshot(
                identity="MateCoincident1",
                state=MateState.SOLVED,
                component_ids=("Base-1", "Bracket-1"),
                degrees_of_freedom=1,
                kind=MateKind.COINCIDENT,
                value=None,
            )
        }
        self.rebuild = RebuildReport(ok=True)

    def list_components(self, assembly_id, recursive):
        return tuple(self.components.values())

    def read_component(self, assembly_id, component_id):
        if component_id not in self.components:
            raise AssemblyRefusal("missing_component", component_id)
        return self.components[component_id]

    def insert_component(self, assembly_id, source_path, configuration):
        raise AssertionError("not used")

    def replace_component(self, assembly_id, component_id, source_path, configuration):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=source_path,
            configuration=configuration,
            transform=current.transform,
            load_state=current.load_state,
            fixed=current.fixed,
        )

    def delete_component(self, assembly_id, component_id):
        self.components.pop(component_id)

    def set_component_transform(self, assembly_id, component_id, transform):
        raise AssertionError("not used")

    def set_component_load_state(self, assembly_id, component_id, state):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=current.configuration,
            transform=current.transform,
            load_state=state,
            fixed=current.fixed,
        )

    def set_component_fixed(self, assembly_id, component_id, fixed):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=current.configuration,
            transform=current.transform,
            load_state=current.load_state,
            fixed=fixed,
        )

    def set_component_configuration(self, assembly_id, component_id, configuration):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=configuration,
            transform=current.transform,
            load_state=current.load_state,
            fixed=current.fixed,
        )

    def add_mate(self, assembly_id, request):
        mate_id = f"Mate{request.kind.value.title()}2"
        self.mates[mate_id] = MateSnapshot(
            identity=mate_id,
            state=MateState.SOLVED,
            component_ids=("Base-1", "Bracket-1"),
            degrees_of_freedom=1,
            kind=request.kind,
            value=request.value,
        )
        return mate_id

    def list_mates(self, assembly_id):
        return tuple(self.mates.values())

    def read_mate(self, assembly_id, mate_id):
        if mate_id not in self.mates:
            raise AssemblyRefusal("missing_mate", mate_id)
        return self.mates[mate_id]

    def set_mate_suppressed(self, assembly_id, mate_id, suppressed):
        current = self.read_mate(assembly_id, mate_id)
        self.mates[mate_id] = MateSnapshot(
            identity=current.identity,
            state=MateState.SUPPRESSED if suppressed else MateState.SOLVED,
            component_ids=current.component_ids,
            degrees_of_freedom=current.degrees_of_freedom,
            kind=current.kind,
            value=current.value,
        )

    def set_mate_value(self, assembly_id, mate_id, value):
        current = self.read_mate(assembly_id, mate_id)
        self.mates[mate_id] = MateSnapshot(
            identity=current.identity,
            state=current.state,
            component_ids=current.component_ids,
            degrees_of_freedom=current.degrees_of_freedom,
            kind=current.kind,
            value=value,
        )

    def rebuild_assembly(self, assembly_id):
        return self.rebuild


class ExtendedAssemblyServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = ExtendedAssemblyAdapter()
        self.service = AssemblyService(self.adapter)

    def test_recursive_component_query_preserves_unique_identity(self):
        components = self.service.list_components("asm-1", recursive=True)
        self.assertEqual(("Base-1", "Bracket-1"), tuple(item.identity for item in components))

    def test_delete_component_requires_absence_readback(self):
        self.service.delete_component("asm-1", "Bracket-1")
        self.assertNotIn("Bracket-1", {item.identity for item in self.service.list_components("asm-1")})

    def test_fix_float_requires_readback(self):
        fixed = self.service.set_component_fixed("asm-1", "Bracket-1", True)
        self.assertTrue(fixed.fixed)
        floating = self.service.set_component_fixed("asm-1", "Bracket-1", False)
        self.assertFalse(floating.fixed)

    def test_component_referenced_configuration_requires_readback(self):
        component = self.service.set_component_configuration("asm-1", "Bracket-1", "Machined")
        self.assertEqual("Machined", component.configuration)

    def test_mate_kind_is_bounded(self):
        with self.assertRaisesRegex(AssemblyRefusal, "unsupported_mate_kind"):
            self.service.add_mate(
                "asm-1",
                MateRequest(kind="run_arbitrary_macro", selection_refs=("a", "b")),
            )

    def test_perpendicular_mate_rejects_unsupported_alignment_before_dispatch(self):
        before = tuple(self.adapter.mates)
        with self.assertRaisesRegex(AssemblyRefusal, "mate_alignment_not_supported"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    kind=MateKind.PERPENDICULAR,
                    selection_refs=("Base-1:plane:front", "Bracket-1:plane:front"),
                    alignment="closest",
                ),
            )
        self.assertEqual(before, tuple(self.adapter.mates))

    def test_common_mate_family_is_accepted(self):
        for kind in (
            MateKind.COINCIDENT,
            MateKind.CONCENTRIC,
            MateKind.DISTANCE,
            MateKind.ANGLE,
            MateKind.PARALLEL,
            MateKind.PERPENDICULAR,
            MateKind.TANGENT,
            MateKind.LOCK,
            MateKind.WIDTH,
            MateKind.SLOT,
        ):
            request = MateRequest(
                kind=kind,
                selection_refs=("Base-1:plane:front", "Bracket-1:plane:front"),
                value=0.01 if kind is MateKind.DISTANCE else (0.5 if kind is MateKind.ANGLE else None),
            )
            mate = self.service.add_mate("asm-1", request)
            self.assertEqual(kind, mate.kind)

    def test_mate_list_has_unique_stable_identity(self):
        mates = self.service.list_mates("asm-1")
        self.assertEqual(("MateCoincident1",), tuple(item.identity for item in mates))

    def test_mate_suppress_unsuppress_requires_state_readback(self):
        suppressed = self.service.set_mate_suppressed("asm-1", "MateCoincident1", True)
        self.assertEqual(MateState.SUPPRESSED, suppressed.state)
        active = self.service.set_mate_suppressed("asm-1", "MateCoincident1", False)
        self.assertEqual(MateState.SOLVED, active.state)

    def test_only_distance_and_angle_mates_accept_value_edit(self):
        with self.assertRaisesRegex(AssemblyRefusal, "mate_value_not_supported"):
            self.service.set_mate_value("asm-1", "MateCoincident1", 0.01)

        distance = self.service.add_mate(
            "asm-1",
            MateRequest(
                kind=MateKind.DISTANCE,
                selection_refs=("Base-1:plane:front", "Bracket-1:plane:front"),
                value=0.01,
            ),
        )
        updated = self.service.set_mate_value("asm-1", distance.identity, 0.02)
        self.assertEqual(0.02, updated.value)

    def test_unsuppress_must_not_claim_dangling_mate(self):
        current = self.adapter.mates["MateCoincident1"]
        self.adapter.mates["MateCoincident1"] = MateSnapshot(
            identity=current.identity,
            state=MateState.DANGLING,
            component_ids=current.component_ids,
            degrees_of_freedom=current.degrees_of_freedom,
            kind=current.kind,
            value=current.value,
        )
        original = self.adapter.set_mate_suppressed

        def keep_dangling(assembly_id, mate_id, suppressed):
            if suppressed:
                original(assembly_id, mate_id, True)

        self.adapter.set_mate_suppressed = keep_dangling
        with self.assertRaisesRegex(AssemblyPostconditionError, "mate_not_solved"):
            self.service.set_mate_suppressed("asm-1", "MateCoincident1", False)


if __name__ == "__main__":
    unittest.main()
