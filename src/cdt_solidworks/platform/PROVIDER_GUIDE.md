# CDT-SolidWorks Provider

The provider exposes a capability-honest SOLIDWORKS COM surface. Native CAD primitives are advertised only when they have a bounded implementation path, rebuild/read-back checks, and Windows SOLIDWORKS acceptance evidence.

## Platform tools

- `help` — read-only provider identity, version, contract fingerprint and this guide content.
- `system_status` — provider/backend dependency state.
- `system_capabilities` — separates `implemented` from currently `available`.

## Application tools

- `application_probe` — read-only SOLIDWORKS registration/running/version probe.
- `application_connect` — attach to or start an explicit SOLIDWORKS application session.
- `application_disconnect` — disconnect; only provider-owned applications may be exited.

## Document tools

- `document_open`
- `document_info`
- `document_save`
- `document_save_as`
- `document_close`
- `document_reopen`
- `document_list_features`
- `document_list_bodies`
- `document_list_components`
- `document_rebuild`
- `document_reconcile`

Document access is disabled unless deployment configuration supplies at least one allowed filesystem root. Explicit document identity/revision is required for document mutations; stale identity is rejected.

## Native CAD tools

The following bounded operations have native SOLIDWORKS 2024 acceptance evidence:

- `sketch_create_rectangle` — create a rectangular 2D sketch in a new native part.
- `part_create_rect_extrude` — create a rectangular sketch plus one solid boss extrude.
- `part_add_rect_extrude` — add an extrusion to an existing part; `merge=false` creates another solid body.
- `part_combine_all_bodies` — Boolean-add all solid bodies and verify the result is one solid body.
- `part_split_by_plane` — split a solid by Front/Top/Right standard plane and retain resulting bodies.
- `sheet_metal_create_base_flange` — create a base flange with explicit thickness and bend radius.
- `surface_create_extrude` — create an extruded surface and verify surface-body read-back.
- `assembly_create` — create an assembly from explicit native component paths and XYZ placements.
- `assembly_add_coincident_plane_mate` — create one coincident mate between a component standard plane and an assembly standard plane.

All CAD paths are constrained by the same configured path policy as document operations. The provider does not expose arbitrary macros, scripts, COM method names, or raw native API argument lists.

## Capability honesty

Granular native capability keys are used for the accepted surface:

- `solidworks.sketch.rectangle`
- `solidworks.part.extrude`
- `solidworks.part.multibody`
- `solidworks.part.combine`
- `solidworks.part.split`
- `solidworks.sheet_metal.base_flange`
- `solidworks.surface.extrude`
- `solidworks.assembly.components`
- `solidworks.assembly.coincident_mate`

Broad `solidworks.part.parametric` remains `implemented=false` with `partial_native_support` until the remaining parametric feature family, including production Cut/Revolve coverage, is complete. Broad `solidworks.assembly.mates` remains partial until additional mate families are implemented and accepted.

`solidworks.configurations`, `solidworks.drawing`, `solidworks.export`, and `solidworks.license` remain unavailable at provider level until their corresponding native adapters/probes satisfy acceptance criteria.

`solidworks.simulation.study` is declared but `implemented=false`: native Simulation integration has not been built. Motion and Routing likewise remain outside the callable provider surface until their typed adapters and native gates exist. Add-ins are intended to load on demand rather than require Start Up. `solidworks.flow_simulation` and `solidworks.electrical` remain `implemented=false` until dedicated installation/license/API probes and native evidence exist.

## Native correctness

A COM return value alone is never sufficient proof of success. Mutations verify the relevant combination of feature identity, body/component count, native rebuild result, feature error state, and persisted artifact state. A mutation that times out after native dispatch is `uncertain`, not an ordinary failure, and dependent writes remain blocked until reconciliation.

## Authentication

Network mode requires a Bearer token sourced from deployment-managed runtime secrets. Missing or invalid credentials fail closed before MCP tool execution.

## Launch

Install the package and run `cdt-solidworks`. Network startup reads runtime configuration from these environment variable names only:

- `CDT_SOLIDWORKS_BEARER_TOKEN`
- `CDT_SOLIDWORKS_AUTH_ISSUER_URL`
- `CDT_SOLIDWORKS_RESOURCE_URL`
- `CDT_SOLIDWORKS_ALLOWED_ROOTS`
- `CDT_SOLIDWORKS_BIND_HOST`
- `CDT_SOLIDWORKS_PORT`
- `CDT_SOLIDWORKS_VERSION`

Authentication configuration is mandatory and startup fails closed when it is incomplete. If allowed roots are omitted, document and CAD path operations remain disabled by policy.
