"""Agent-2 plugin: assembly lifecycle, configuration context, and Toolbox."""

from __future__ import annotations

from typing import Any

from cdt_solidworks.integration.registrar import _result_payload
from cdt_solidworks.integration.toolbox import IntegratedToolboxService
from cdt_solidworks.native.models import NativeCallState


PLUGIN_CONTRACT_VERSION = 1
PLUGIN_ID = "agent2.assembly_toolbox"
PLUGIN_ORDER = 2


def _ensure_services(runtime: Any) -> tuple[Any | None, Any | None, Any | None]:
    assembly = getattr(runtime, "assembly_service", None)
    configuration = getattr(runtime, "configuration_service", None)
    topology = getattr(runtime, "topology_service", None)
    if assembly is not None and hasattr(assembly, "bind_topology_service"):
        assembly.bind_topology_service(topology, required=True)

    toolbox = getattr(runtime, "toolbox_service", None)
    if toolbox is None:
        session = getattr(runtime, "session", None)
        path_policy = getattr(runtime, "path_policy", None)
        if session is not None and path_policy is not None and hasattr(session, "api"):
            toolbox = IntegratedToolboxService(
                session,
                path_policy=path_policy,
                assembly_service=assembly,
            )
            setattr(runtime, "toolbox_service", toolbox)
    elif getattr(toolbox, "assembly_service", None) is None and assembly is not None:
        try:
            toolbox.assembly_service = assembly
        except Exception:
            pass
    return assembly, configuration, toolbox


def register_tools(server: Any, runtime: Any) -> None:
    assembly, configuration, toolbox = _ensure_services(runtime)

    if assembly is not None:
        @server.tool(
            name="assembly_component_insert",
            description="Insert an existing SOLIDWORKS part/subassembly with explicit configuration and optional verified native transform.",
        )
        def assembly_component_insert(
            path: str,
            source_path: str,
            configuration: str | None = None,
            transform: list[float] | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                assembly.insert_component(path, source_path, configuration, transform)
            )

        @server.tool(
            name="assembly_mate_get",
            description="Read one mate by stable identity including solver state, family, values, and reference-component pairing.",
        )
        def assembly_mate_get(path: str, mate_id: str) -> dict[str, Any]:
            return _result_payload(assembly.read_mate(path, mate_id))

        @server.tool(
            name="assembly_mate_delete",
            description="Delete one explicitly identified mate, rebuild, and verify that it is absent.",
        )
        def assembly_mate_delete(path: str, mate_id: str) -> dict[str, Any]:
            return _result_payload(assembly.delete_mate(path, mate_id))

    if configuration is not None:
        @server.tool(
            name="configuration_state_query",
            description="Read explicit configuration-scoped dimensions, properties, and component states with context-drift detection.",
        )
        def configuration_state_query(
            path: str,
            name: str,
            dimensions: list[str] | None = None,
            properties: list[str] | None = None,
            component_ids: list[str] | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                configuration.query_state(
                    path,
                    name,
                    dimensions=dimensions or (),
                    properties=properties or (),
                    component_ids=component_ids or (),
                )
            )

        @server.tool(
            name="configuration_component_set_suppressed",
            description="Set one assembly component suppression state in one explicit configuration and verify configuration-specific read-back.",
        )
        def configuration_component_set_suppressed(
            path: str,
            configuration_name: str,
            component_id: str,
            suppressed: bool,
        ) -> dict[str, Any]:
            return _result_payload(
                configuration.set_component_suppressed(
                    path, configuration_name, component_id, suppressed
                )
            )

        @server.tool(
            name="configuration_component_set_configuration",
            description="Set one component referenced configuration inside one explicit assembly configuration and verify read-back.",
        )
        def configuration_component_set_configuration(
            path: str,
            configuration_name: str,
            component_id: str,
            referenced_configuration: str,
        ) -> dict[str, Any]:
            return _result_payload(
                configuration.set_component_configuration(
                    path,
                    configuration_name,
                    component_id,
                    referenced_configuration,
                )
            )

    if toolbox is not None:
        @server.tool(
            name="toolbox_probe",
            description="Probe Toolbox add-in, license/library state, root, database, and version without mutating vendor content.",
        )
        def toolbox_probe() -> dict[str, Any]:
            return _result_payload(toolbox.probe())

        @server.tool(
            name="toolbox_catalog_query",
            description="Bounded Toolbox query by standard, family, and size/configuration.",
        )
        def toolbox_catalog_query(
            standard: str | None = None,
            family: str | None = None,
            size: str | None = None,
            limit: int = 25,
        ) -> dict[str, Any]:
            return _result_payload(
                toolbox.catalog_query(
                    standard=standard, family=family, size=size, limit=limit
                )
            )

        @server.tool(
            name="toolbox_component_resolve",
            description="Resolve one exact Toolbox component to a project-safe copy; vendor library files are never mutation targets.",
        )
        def toolbox_component_resolve(
            standard: str,
            family: str,
            size: str,
            project_directory: str,
            filename: str | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                toolbox.resolve_component(
                    standard=standard,
                    family=family,
                    size=size,
                    project_directory=project_directory,
                    filename=filename,
                )
            )

        @server.tool(
            name="toolbox_component_properties",
            description="Read bounded configuration-specific Toolbox/BOM properties from a project component.",
        )
        def toolbox_component_properties(
            path: str, configuration: str
        ) -> dict[str, Any]:
            return _result_payload(toolbox.component_properties(path, configuration))

        @server.tool(
            name="toolbox_component_insert",
            description="Create a project-safe Toolbox component copy and insert it into an assembly with explicit configuration/placement.",
        )
        def toolbox_component_insert(
            assembly_path: str,
            standard: str,
            family: str,
            size: str,
            project_directory: str,
            filename: str | None = None,
            transform: list[float] | None = None,
        ) -> dict[str, Any]:
            return _result_payload(
                toolbox.insert_component(
                    assembly_path,
                    standard=standard,
                    family=family,
                    size=size,
                    project_directory=project_directory,
                    filename=filename,
                    transform=transform,
                )
            )


def capability_descriptors(runtime: Any) -> tuple[dict[str, Any], ...]:
    assembly, configuration, toolbox = _ensure_services(runtime)
    solidworks_available, solidworks_reason = _solidworks_dependency(runtime)
    topology = getattr(runtime, "topology_service", None)
    topology_available = topology is not None and callable(getattr(topology, "resolve", None))

    toolbox_available = False
    toolbox_reason: str | None = "toolbox_service_unavailable"
    if toolbox is not None and solidworks_available:
        probe = toolbox.probe()
        if probe.state is NativeCallState.SUCCESS and probe.value is not None:
            toolbox_available = bool(getattr(probe.value, "available", False))
            toolbox_reason = None if toolbox_available else getattr(
                probe.value, "reason", "toolbox_unavailable"
            )
        else:
            toolbox_reason = (
                probe.failure.code if probe.failure is not None else "toolbox_probe_failed"
            )
    elif toolbox is not None:
        toolbox_reason = solidworks_reason

    assembly_available = assembly is not None and solidworks_available
    configuration_available = configuration is not None and solidworks_available
    topology_mates_available = assembly_available and topology_available
    topology_reason = (
        None
        if topology_mates_available
        else solidworks_reason
        if not solidworks_available
        else "topology_service_unavailable"
    )

    def descriptor(
        name: str,
        implemented: bool,
        available: bool,
        reason: str | None,
        dependencies: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            "name": name,
            "implemented": implemented,
            "available": available,
            "reason": reason,
            "backend": "solidworks_com",
            "dependencies": dependencies,
        }

    return (
        descriptor(
            "solidworks.assembly.component_insert",
            assembly is not None,
            assembly_available,
            None if assembly_available else solidworks_reason or "assembly_service_unavailable",
            ("solidworks",),
        ),
        descriptor(
            "solidworks.assembly.topology_mates",
            assembly is not None,
            topology_mates_available,
            topology_reason,
            ("solidworks", "topology.resolve"),
        ),
        descriptor(
            "solidworks.assembly.mate_delete",
            assembly is not None,
            assembly_available,
            None if assembly_available else solidworks_reason or "assembly_service_unavailable",
            ("solidworks",),
        ),
        descriptor(
            "solidworks.configuration.component_context",
            configuration is not None,
            configuration_available,
            None if configuration_available else solidworks_reason or "configuration_service_unavailable",
            ("solidworks",),
        ),
        descriptor(
            "solidworks.toolbox.catalog",
            toolbox is not None,
            toolbox_available,
            toolbox_reason,
            ("solidworks", "solidworks.toolbox"),
        ),
        descriptor(
            "solidworks.toolbox.component",
            toolbox is not None and assembly is not None,
            toolbox_available and assembly_available,
            None if toolbox_available and assembly_available else toolbox_reason or solidworks_reason,
            ("solidworks", "solidworks.toolbox"),
        ),
    )


def _solidworks_dependency(runtime: Any) -> tuple[bool, str | None]:
    direct_probe = getattr(runtime, "solidworks_dependency_state", None)
    if callable(direct_probe):
        try:
            dependency = direct_probe()
            return bool(getattr(dependency, "available", False)), getattr(
                dependency, "reason", None
            )
        except Exception as exc:
            return False, f"solidworks_probe_failed:{type(exc).__name__}"
    context_method = getattr(runtime, "runtime_context", None)
    if not callable(context_method):
        return False, "runtime_context_unavailable"
    try:
        context = context_method()
        dependencies = getattr(context, "dependencies", ())
        for dependency in dependencies:
            if getattr(dependency, "name", None) == "solidworks":
                return bool(getattr(dependency, "available", False)), getattr(
                    dependency, "reason", None
                )
    except Exception as exc:
        return False, f"solidworks_probe_failed:{type(exc).__name__}"
    return False, "solidworks_dependency_missing"
