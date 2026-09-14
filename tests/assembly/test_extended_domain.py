import unittest

from cdt_solidworks.assembly.domain import (
    AssemblyPostconditionError,
    AssemblyRefusal,
    AssemblyService,
    ComponentLoadState,
    ComponentPatternSnapshot,
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
        self.components["NestedPart-1@SubAsm-1"] = ComponentSnapshot(
            identity="NestedPart-1@SubAsm-1",
            source_path="C:/fixtures/nested.SLDPRT",
            configuration="Default",
            transform=_IDENTITY,
            load_state=ComponentLoadState.RESOLVED,
            fixed=False,
            parent_identity="SubAsm-1",
        )
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
        self.patterns = {}
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
            parent_identity=current.parent_identity,
        )
        return component_id

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
            parent_identity=current.parent_identity,
        )

    def create_linear_component_pattern(
        self, assembly_id, seed_component_ids, direction_ref, spacing_m, total_instances
    ):
        identity = "LocalLPattern1"
        self.patterns[identity] = ComponentPatternSnapshot(
            identity=identity,
            seed_component_ids=tuple(seed_component_ids),
            spacing_m=spacing_m,
            total_instances=total_instances,
            direction_resolved=True,
        )
        for index in range(1, total_instances):
            for seed in seed_component_ids:
                current = self.read_component(assembly_id, seed)
                pattern_id = f"{seed}@{identity}[{index}]"
                self.components[pattern_id] = ComponentSnapshot(
                    identity=pattern_id,
                    source_path=current.source_path,
                    configuration=current.configuration,
                    transform=current.transform,
                    load_state=current.load_state,
                    fixed=False,
                    parent_identity=current.parent_identity,
                )
        return identity

    def read_component_pattern(self, assembly_id, pattern_id):
        return self.patterns[pattern_id]

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

    def test_recursive_component_query_preserves_unique_identity_and_parent(self):
        components = self.service.list_components("asm-1", recursive=True)
        nested = next(item for item in components if item.identity.startswith("NestedPart"))
        self.assertEqual("SubAsm-1", nested.parent_identity)
        self.assertEqual(3, len({item.identity for item in components}))

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

    def test_linear_component_pattern_requires_feature_and_component_count_readback(self):
        pattern = self.service.create_linear_component_pattern(
            "asm-1",
            seed_component_ids=("Bracket-1",),
            direction_ref="Bracket-1:edge:pattern-axis",
            spacing_m=0.025,
            total_instances=3,
        )
        self.assertEqual("LocalLPattern1", pattern.identity)
        self.assertEqual(3, pattern.total_instances)
        self.assertEqual(5, len(self.service.list_components("asm-1", recursive=True)))

    def test_linear_component_pattern_rejects_invalid_count_and_spacing_pre_dispatch(self):
        before = tuple(self.adapter.patterns)
        with self.assertRaisesRegex(AssemblyRefusal, "invalid_pattern_instance_count"):
            self.service.create_linear_component_pattern(
                "asm-1",
                seed_component_ids=("Bracket-1",),
                direction_ref="Bracket-1:edge:pattern-axis",
                spacing_m=0.025,
                total_instances=1,
            )
        with self.assertRaisesRegex(AssemblyRefusal, "invalid_pattern_spacing"):
            self.service.create_linear_component_pattern(
                "asm-1",
                seed_component_ids=("Bracket-1",),
                direction_ref="Bracket-1:edge:pattern-axis",
                spacing_m=0.0,
                total_instances=3,
            )
        self.assertEqual(before, tuple(self.adapter.patterns))

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

    def test_common_mate_family_is_accepted_with_kind_specific_shape(self):
        requests = (
            MateRequest(MateKind.COINCIDENT, ("Base-1:plane:front", "Bracket-1:plane:front")),
            MateRequest(MateKind.CONCENTRIC, ("Base-1:face:bore", "Bracket-1:face:shaft")),
            MateRequest(MateKind.DISTANCE, ("Base-1:plane:front", "Bracket-1:plane:front"), value=0.01),
            MateRequest(MateKind.ANGLE, ("Base-1:plane:front", "Bracket-1:plane:front"), value=0.5),
            MateRequest(MateKind.PARALLEL, ("Base-1:plane:front", "Bracket-1:plane:front")),
            MateRequest(MateKind.PERPENDICULAR, ("Base-1:plane:front", "Bracket-1:plane:front")),
            MateRequest(MateKind.TANGENT, ("Base-1:face:cylinder", "Bracket-1:plane:front")),
            MateRequest(MateKind.LOCK, ("component:Base-1", "component:Bracket-1")),
            MateRequest(
                MateKind.WIDTH,
                (
                    "Base-1:face:width-left",
                    "Base-1:face:width-right",
                    "Bracket-1:face:tab-left",
                    "Bracket-1:face:tab-right",
                ),
                constraint="centered",
            ),
            MateRequest(
                MateKind.SLOT,
                ("Base-1:face:slot", "Bracket-1:face:pin"),
                constraint="centered",
            ),
        )
        for request in requests:
            mate = self.service.add_mate("asm-1", request)
            self.assertEqual(request.kind, mate.kind)

    def test_width_and_slot_shape_fail_closed_before_dispatch(self):
        before = tuple(self.adapter.mates)
        with self.assertRaisesRegex(AssemblyRefusal, "invalid_width_mate_selection"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    MateKind.WIDTH,
                    ("Base-1:face:left", "Base-1:face:right"),
                    constraint="centered",
                ),
            )
        with self.assertRaisesRegex(AssemblyRefusal, "invalid_slot_constraint"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    MateKind.SLOT,
                    ("Base-1:face:slot", "Bracket-1:face:pin"),
                    constraint="distance",
                ),
            )
        self.assertEqual(before, tuple(self.adapter.mates))

    def test_non_value_mate_rejects_value_instead_of_ignoring_it(self):
        with self.assertRaisesRegex(AssemblyRefusal, "mate_value_not_supported"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    MateKind.CONCENTRIC,
                    ("Base-1:face:bore", "Bracket-1:face:shaft"),
                    value=0.1,
                ),
            )

    def test_read_mate_preserves_explicit_identity(self):
        mate = self.service.read_mate("asm-1", "MateCoincident1")
        self.assertEqual("MateCoincident1", mate.identity)
        self.assertEqual(MateKind.COINCIDENT, mate.kind)

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
