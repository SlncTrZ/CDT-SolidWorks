from pathlib import Path

from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.toolbox import IntegratedToolboxService
from cdt_solidworks.native.models import NativeCallResult, NativeCallState
from cdt_solidworks.toolbox.domain import ToolboxResolvedComponent


class _ToolboxDomain:
    def __init__(self) -> None:
        self.resolve_calls = []
        self.property_calls = []

    def probe(self):
        return {"available": True}

    def catalog_query(self, **kwargs):
        return ()

    def resolve_component(self, **kwargs):
        self.resolve_calls.append(kwargs)
        target = Path(kwargs["project_directory"]) / (kwargs.get("filename") or "bolt.SLDPRT")
        target.write_bytes(b"copy")
        return ToolboxResolvedComponent(
            standard=kwargs["standard"],
            family=kwargs["family"],
            size=kwargs["size"],
            source_path="C:/Toolbox/bolt.SLDPRT",
            project_path=str(target.resolve()),
            configuration=kwargs["size"],
            part_number="BOLT-1",
            properties=(("Part Number", "BOLT-1"),),
        )

    def component_properties(self, path, configuration):
        self.property_calls.append((path, configuration))
        return (("Part Number", "BOLT-1"),)


class _Assembly:
    def __init__(self) -> None:
        self.calls = []

    def insert_component(self, path, source, configuration=None, transform=None):
        self.calls.append((path, source, configuration, transform))
        return NativeCallResult.success(
            {"identity": "Bolt-1"}, call_id="native-insert", dispatched=True
        )


def test_toolbox_resolve_enforces_allowed_project_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    domain = _ToolboxDomain()
    service = IntegratedToolboxService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    result = service.resolve_component(
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        project_directory=str(project),
    )
    assert result.state is NativeCallState.SUCCESS
    assert result.dispatched is True
    assert Path(result.value.project_path).parent == project.resolve()

    outside = tmp_path.parent / "outside-agent2-toolbox"
    outside.mkdir(exist_ok=True)
    refused = service.resolve_component(
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        project_directory=str(outside),
    )
    assert refused.state is NativeCallState.FAILURE
    assert refused.dispatched is False


def test_toolbox_insert_chains_project_copy_to_assembly_insert(tmp_path: Path) -> None:
    assembly_path = tmp_path / "fixture.SLDASM"
    assembly_path.write_bytes(b"asm")
    project = tmp_path / "project"
    project.mkdir()
    assembly = _Assembly()
    service = IntegratedToolboxService(
        path_policy=DocumentPathPolicy((tmp_path,)),
        service=_ToolboxDomain(),
        assembly_service=assembly,
    )
    result = service.insert_component(
        str(assembly_path),
        standard="ANSI Inch",
        family="hex bolt",
        size="1/4-20 x 1",
        project_directory=str(project),
        transform=[float(index) for index in range(16)],
    )
    assert result.state is NativeCallState.SUCCESS
    assert result.call_id == "native-insert"
    assert result.dispatched is True
    assert result.value["assembly_component"]["identity"] == "Bolt-1"
    assert assembly.calls[-1][2] == "1/4-20 x 1"
    assert Path(assembly.calls[-1][1]).parent == project.resolve()


def test_toolbox_properties_validate_project_path(tmp_path: Path) -> None:
    component = tmp_path / "bolt.SLDPRT"
    component.write_bytes(b"part")
    domain = _ToolboxDomain()
    service = IntegratedToolboxService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    result = service.component_properties(str(component), "Default")
    assert result.state is NativeCallState.SUCCESS
    assert domain.property_calls == [(str(component.resolve()), "Default")]
