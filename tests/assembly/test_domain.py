import unittest

from cdt_solidworks.assembly.domain import (
    AssemblyPostconditionError,
    AssemblyRefusal,
    AssemblyService,
    ComponentLoadState,
    ComponentSnapshot,
    MateRequest,
    MateSnapshot,
    MateState,
    RebuildReport,
)


class FakeAssemblyAdapter:
    def __init__(self):
        self.components = {
            "base": ComponentSnapshot(
                identity="base",
                source_path=r"C:\fixtures\base.SLDPRT",
                configuration="Default",
                transform=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
                load_state=ComponentLoadState.RESOLVED,
            )
        }
        self.mates = {}
        self.rebuild = RebuildReport(ok=True)
        self.force_transform_mismatch = False
        self.mate_state = MateState.SOLVED

    def read_component(self, assembly_id, component_id):
        if component_id not in self.components:
            raise AssemblyRefusal("missing_component", component_id)
        return self.components[component_id]

    def insert_component(self, assembly_id, source_path, configuration):
        component_id = "inserted"
        self.components[component_id] = ComponentSnapshot(
            identity=component_id,
            source_path=source_path,
            configuration=configuration,
            transform=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
            load_state=ComponentLoadState.RESOLVED,
        )
        return component_id

    def replace_component(self, assembly_id, component_id, source_path, configuration):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=source_path,
            configuration=configuration,
            transform=current.transform,
            load_state=current.load_state,
        )

    def set_component_transform(self, assembly_id, component_id, transform):
        current = self.read_component(assembly_id, component_id)
        stored = current.transform if self.force_transform_mismatch else transform
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=current.configuration,
            transform=stored,
            load_state=current.load_state,
        )

    def set_component_load_state(self, assembly_id, component_id, state):
        current = self.read_component(assembly_id, component_id)
        self.components[component_id] = ComponentSnapshot(
            identity=current.identity,
            source_path=current.source_path,
            configuration=current.configuration,
            transform=current.transform,
            load_state=state,
        )

    def add_mate(self, assembly_id, request):
        mate_id = "mate-1"
        self.mates[mate_id] = MateSnapshot(
            identity=mate_id,
            state=self.mate_state,
            component_ids=("base", "inserted"),
            degrees_of_freedom=1,
            rebuild_errors=(),
        )
        return mate_id

    def read_mate(self, assembly_id, mate_id):
        return self.mates[mate_id]

    def rebuild_assembly(self, assembly_id):
        return self.rebuild


class AssemblyServiceTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAssemblyAdapter()
        self.service = AssemblyService(self.adapter)

    def test_insert_requires_readback_and_rebuild(self):
        component = self.service.insert_component(
            "asm-1", r"C:\fixtures\inserted.SLDPRT", "Default"
        )
        self.assertEqual("inserted", component.identity)
        self.assertEqual(r"C:\fixtures\inserted.SLDPRT", component.source_path)

    def test_replace_component_requires_source_readback(self):
        component = self.service.replace_component(
            "asm-1", "base", r"C:\\fixtures\\replacement.SLDPRT", "Machined"
        )
        self.assertEqual(r"C:\\fixtures\\replacement.SLDPRT", component.source_path)
        self.assertEqual("Machined", component.configuration)

    def test_missing_component_is_typed_refusal(self):
        with self.assertRaisesRegex(AssemblyRefusal, "missing_component"):
            self.service.set_component_load_state(
                "asm-1", "missing", ComponentLoadState.SUPPRESSED
            )

    def test_transform_must_match_readback(self):
        self.adapter.force_transform_mismatch = True
        transform = (1.0, 0.0, 0.0, 10.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        with self.assertRaisesRegex(AssemblyPostconditionError, "transform_readback_mismatch"):
            self.service.set_component_transform("asm-1", "base", transform)

    def test_rebuild_failure_prevents_success(self):
        self.adapter.rebuild = RebuildReport(ok=False, errors=("mate rebuild failed",))
        with self.assertRaisesRegex(AssemblyPostconditionError, "rebuild_failed"):
            self.service.set_component_load_state(
                "asm-1", "base", ComponentLoadState.SUPPRESSED
            )

    def test_unsolved_mate_cannot_pass(self):
        self.service.insert_component(
            "asm-1", r"C:\\fixtures\\inserted.SLDPRT", "Default"
        )
        self.adapter.mate_state = MateState.OVER_DEFINED
        with self.assertRaisesRegex(AssemblyPostconditionError, "mate_not_solved"):
            self.service.add_mate(
                "asm-1",
                MateRequest(
                    kind="coincident",
                    selection_refs=("base:Face1", "inserted:Face2"),
                ),
            )

    def test_mate_requires_solved_readback(self):
        inserted = self.service.insert_component(
            "asm-1", r"C:\fixtures\inserted.SLDPRT", "Default"
        )
        mate = self.service.add_mate(
            "asm-1",
            MateRequest(
                kind="concentric",
                selection_refs=("base:Face1", f"{inserted.identity}:Face2"),
            ),
        )
        self.assertEqual(MateState.SOLVED, mate.state)
        self.assertEqual(1, mate.degrees_of_freedom)


if __name__ == "__main__":
    unittest.main()
