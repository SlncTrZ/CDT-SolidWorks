from pathlib import Path

from cdt_solidworks.assembly.domain import MateKind, MateSnapshot, MateState
from cdt_solidworks.document.path_policy import DocumentPathPolicy
from cdt_solidworks.integration.assembly_config import IntegratedAssemblyService
from cdt_solidworks.native.models import NativeCallState


class _AssemblyDomain:
    def __init__(self) -> None:
        self.requests = []
        self.insert_calls = []
        self.deleted = []

    def add_mate(self, path, request):
        self.requests.append((path, request))
        return MateSnapshot(
            identity="Concentric1",
            state=MateState.SOLVED,
            component_ids=tuple(
                value
                for value in request.resolved_reference_component_ids
                if value is not None
            ),
            degrees_of_freedom=1,
            reference_component_ids=request.resolved_reference_component_ids,
            kind=request.kind,
            value=request.value,
            error_status=0,
        )

    def read_mate(self, path, mate_id):
        return MateSnapshot(
            identity=mate_id,
            state=MateState.SOLVED,
            component_ids=("Shaft-1", "Bearing-1"),
            degrees_of_freedom=1,
            reference_component_ids=("Shaft-1", "Bearing-1"),
            kind=MateKind.CONCENTRIC,
            error_status=0,
        )

    def delete_mate(self, path, mate_id):
        self.deleted.append((path, mate_id))

    def insert_component(self, path, source, configuration, *, transform=None):
        self.insert_calls.append((path, source, configuration, transform))
        return {
            "identity": "Bearing-1",
            "source_path": source,
            "configuration": configuration,
            "transform": transform,
        }


class _Topology:
    def __init__(self) -> None:
        self.calls = []
        self.entities = {
            "swref1.shaft": (object(), "Shaft-1"),
            "swref1.bearing": (object(), "Bearing-1"),
        }

    def resolve(self, document_id, reference):
        self.calls.append((document_id, reference))
        entity, component_id = self.entities[reference]
        return {"native_entity": entity, "component_id": component_id}


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    assembly = tmp_path / "fixture.SLDASM"
    source = tmp_path / "bearing.SLDPRT"
    assembly.write_bytes(b"assembly")
    source.write_bytes(b"part")
    return assembly, source


def test_topology_bound_mate_requires_opaque_refs_and_resolves_every_ref(tmp_path: Path) -> None:
    assembly, _ = _fixture(tmp_path)
    domain = _AssemblyDomain()
    topology = _Topology()
    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    service.bind_topology_service(topology, required=True)

    result = service.create_mate(
        str(assembly),
        kind="concentric",
        selection_refs=("swref1.shaft", "swref1.bearing"),
        alignment="anti_aligned",
    )
    assert result.state is NativeCallState.SUCCESS
    request = domain.requests[-1][1]
    assert request.topology_resolved is True
    assert len(request.resolved_entities) == 2
    assert request.resolved_reference_component_ids == ("Shaft-1", "Bearing-1")
    assert topology.calls == [
        (str(assembly.resolve()), "swref1.shaft"),
        (str(assembly.resolve()), "swref1.bearing"),
    ]


def test_topology_bound_mate_fails_closed_when_service_missing(tmp_path: Path) -> None:
    assembly, _ = _fixture(tmp_path)
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    service.bind_topology_service(None, required=True)
    result = service.create_mate(
        str(assembly),
        kind="concentric",
        selection_refs=("swref1.shaft", "swref1.bearing"),
    )
    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert result.failure is not None
    assert result.failure.code == "topology_service_unavailable"
    assert domain.requests == []


def test_topology_bound_mate_rejects_legacy_geometry_names(tmp_path: Path) -> None:
    assembly, _ = _fixture(tmp_path)
    topology = _Topology()
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    service.bind_topology_service(topology, required=True)
    result = service.create_mate(
        str(assembly),
        kind="concentric",
        selection_refs=("Part-1:face:bore", "Part-2:face:shaft"),
    )
    assert result.state is NativeCallState.FAILURE
    assert result.dispatched is False
    assert topology.calls == []
    assert domain.requests == []


def test_component_insert_validates_source_and_forwards_transform(tmp_path: Path) -> None:
    assembly, source = _fixture(tmp_path)
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    transform = [float(index) for index in range(16)]
    result = service.insert_component(
        str(assembly), str(source), "Default", transform
    )
    assert result.state is NativeCallState.SUCCESS
    assert domain.insert_calls[-1] == (
        str(assembly.resolve()),
        str(source.resolve()),
        "Default",
        tuple(transform),
    )


def test_mate_get_and_delete_use_explicit_identity(tmp_path: Path) -> None:
    assembly, _ = _fixture(tmp_path)
    domain = _AssemblyDomain()
    service = IntegratedAssemblyService(
        path_policy=DocumentPathPolicy((tmp_path,)), service=domain
    )
    assert service.read_mate(str(assembly), "Concentric1").state is NativeCallState.SUCCESS
    deleted = service.delete_mate(str(assembly), "Concentric1")
    assert deleted.state is NativeCallState.SUCCESS
    assert domain.deleted == [(str(assembly.resolve()), "Concentric1")]
