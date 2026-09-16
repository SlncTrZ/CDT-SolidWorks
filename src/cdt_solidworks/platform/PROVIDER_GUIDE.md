# CDT-SolidWorks Provider

The provider exposes a capability-honest SOLIDWORKS COM surface. Native CAD primitives are advertised only when they have a bounded implementation path, rebuild/read-back checks, and Windows SOLIDWORKS acceptance evidence.

## Platform tools

- `help` — read-only provider identity, version, contract fingerprint and this guide content.
- `system_status` — provider/backend dependency state.
- `system_observability` — bounded provider telemetry (counts, latency, operation ID and failure class) without request payloads or credentials.
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

## Provider composition contract

Parallel domain lanes compose through the packaged `cdt_solidworks.integration.plugins` namespace. Discovery is intentionally bounded to modules named `agent1_*` through `agent6_*`; there is no environment variable, filesystem path, entry-point, or arbitrary import hook that can expand the plugin search surface.

Every plugin must declare `PLUGIN_CONTRACT_VERSION = 1`, a unique `PLUGIN_ID`, a unique `PLUGIN_ORDER`, `register_tools(server, runtime)`, and `capability_descriptors(runtime)`. Startup fails closed on malformed contracts, duplicate plugin IDs/orders, duplicate public tool names, duplicate service/capability keys, import failures, or registration failures. Plugin order is deterministic.

The shared runtime exposes `core.session`, `core.path_policy`, `core.document_service`, and `core.cad_service` service ports plus duplicate-safe lane service registration. Plugin capability availability is dependency-aware: loading a module never makes its capabilities available by itself, and an unavailable declared dependency forces the capability unavailable.

Every advertised tool, including plugin tools, receives a closed input schema with `additionalProperties=false`; plugin tools using `**kwargs` are rejected at startup. Plugin calls share bounded provider observability for call/operation ID, latency, failure class, timeout/uncertainty, and reconciliation counts without retaining raw request or COM payloads. Mutation results preserve the original native call ID and are never retried by the composition layer.

Plugin-discovered lane capabilities remain implementation claims only until Agent 7 validates the exact integrated wheel on Linux and Windows with native SOLIDWORKS evidence. This guide does not pre-award acceptance for parallel lane features.

### Integration-candidate plugin tools — native acceptance pending

These names are part of the composed tool catalog, but listing them here is **not** a native-acceptance claim. Runtime capability state remains fail-closed until the required services, licenses, templates, and Agent-7 evidence are present.

- Agent 1: `topology_query`, `topology_resolve`, `topology_inspect`.
- Agent 2: `assembly_component_insert`, `assembly_mate_get`, `assembly_mate_delete`, `configuration_state_query`, `configuration_component_set_suppressed`, `configuration_component_set_configuration`, `toolbox_probe`, `toolbox_catalog_query`, `toolbox_component_resolve`, `toolbox_component_properties`, `toolbox_component_insert`.
- Agent 3: `part_profile_extrude`, `part_profile_cut`, `part_feature_parameters_get`, `part_feature_parameter_set`, `part_gear_create_spur`, `part_gear_parameters_get`, `part_gear_parameters_set`.
- Agent 4: `drawing_dimension_create`, `drawing_dimensions_list`, `drawing_bom_create`, `drawing_bom_read`, `drawing_update`.
- Agent 5: `body_move_copy`, `body_delete_keep`, `surface_offset`, `sheet_metal_add_edge_flange`, `sheet_metal_add_hem`, `sheet_metal_add_sketched_bend`, `sheet_metal_unfold_bend`, `sheet_metal_fold_bend`, `weldment_trim_extend`, `weldment_set_cut_list_property`, `reconstruction_assess`, `reconstruction_step_to_editable`, `reconstruction_mesh_to_parametric`, `reconstruction_compare`.

## Native CAD tools

The following bounded operations have native SOLIDWORKS 2024 acceptance evidence:

- `sketch_create_geometry` — create bounded explicit sketch geometry in an opened native part.
- `sketch_get` — read one explicit native sketch with stable identity/read-back.
- `sketch_relations_list` — list native sketch relations and bounded definition-state evidence.
- `sketch_relation_delete` — delete one explicit accepted sketch relation and verify read-back.
- `sketch_dimension_set` — edit one accepted sketch dimension and verify persisted value.
- `sketch_create_rectangle` — create a rectangular 2D sketch in a new native part.
- `part_create_rect_extrude` — create a rectangular sketch plus one solid boss extrude.
- `part_add_rect_extrude` — add an extrusion to an existing part; `merge=false` creates another solid body.
- `part_cut_extrude` — create a blind or through-all Cut Extrude from a sketch on a Front/Top/Right standard reference plane using path + revision identity.
- `part_cut_reconcile` — reconcile an uncertain Cut Extrude by native call ID and expected feature definition; quarantine clears only after rebuild/body verification succeeds.
- `part_simple_hole` — create one blind or through-all native Simple Hole on a one-solid-body part using only `face_ref=bbox:+z`, one model-space X/Y center and a planar face with outward +Z normal; arbitrary face identities, multi-body targeting and multi-center holes remain intentionally unexposed.
- `part_simple_hole_reconcile` — reconcile an uncertain Simple Hole by call ID; verifies persisted native type, diameter, center, end condition/depth, rebuild and solid body before clearing quarantine.
- `part_revolve` — create a solid Revolve from a standard-plane sketch containing exactly one construction centerline; `axis_ref` is intentionally bounded to `profile_centerline`.
- `part_revolve_cut` — create a Revolve Cut using the same bounded profile-centerline and angle contract.
- `part_revolve_reconcile` — reconcile an uncertain boss/cut Revolve by call ID; verifies native type, axis, angle, rebuild and solid body before clearing quarantine.
- `part_hole_wizard` — create the bounded native-accepted ANSI Metric countersink Hole Wizard subset with explicit size/center identity and save-reopen read-back.
- `part_fillet`, `part_chamfer`, `part_shell`, `part_draft`, `part_rib` — create the bounded native-accepted common feature subsets with rebuild/read-back validation.
- `part_linear_pattern`, `part_circular_pattern`, `part_mirror` — create native-accepted feature pattern/mirror subsets with persisted feature identity.
- `part_reference_plane`, `part_reference_axis`, `part_reference_point` — create bounded native reference geometry.
- `part_feature_get`, `part_feature_rename`, `part_feature_set_suppressed`, `part_feature_set_parameter` — bounded feature query/rename/suppression and whitelisted parameter editing.
- `part_combine_all_bodies` — Boolean-add all solid bodies and verify the result is one solid body.
- `part_split_by_plane` — split a solid by Front/Top/Right standard plane and retain resulting bodies.
- `sheet_metal_create_base_flange` — create a base flange with explicit thickness and bend radius.
- `surface_create_extrude` — create an extruded surface and verify surface-body read-back.
- `body_inspect` — inspect bounded native solid/surface-body state.
- `body_combine` — run named-body Boolean Add/Subtract/Common with native read-back.
- `surface_thicken` — thicken one named surface body and verify resulting solid state.
- `sheet_metal_inspect` — inspect accepted Base Flange and Flat Pattern state.
- `sheet_metal_set_flattened` — persist Flat Pattern suppression state.
- `weldment_inspect` — inspect structural-member and cut-list state.
- `weldment_create_structural_member` — create a structural member from an allowed profile root and named path sketch.
- `assembly_create` — create an assembly from explicit native component paths and XYZ placements.
- `assembly_add_coincident_plane_mate` — create one coincident mate between a component standard plane and an assembly standard plane.
- `assembly_components_list` — list native assembly components by explicit document identity.
- `assembly_component_set_fixed` — fix/float one explicit component.
- `assembly_component_set_load_state` — set one component to the native-accepted `resolved` or `suppressed` state.
- `assembly_component_set_configuration` — set/read back one component referenced configuration.
- `assembly_component_delete` — delete one explicit top-level component and verify absence after rebuild.
- `assembly_component_replace` — replace one explicit top-level component with an allowed native part/assembly and verify source/configuration read-back.
- `assembly_component_set_transform` — apply one explicit 16-value native component transform and verify persistence.
- `assembly_component_pattern_create` — create a bounded one-direction linear component pattern from stable seed/direction identities.
- `assembly_mate_create` — create Coincident, Concentric, Distance, Angle, Parallel, Perpendicular, Tangent, Lock, Width, or Slot mates using bounded stable selection references; Width/Slot constraints are bounded to centered/free.
- `assembly_mates_list` — list stable mate identity/type/state, including solved/suppressed/dangling state.
- `assembly_mate_set_suppressed` — suppress/unsuppress a native-accepted mate and verify solved-state read-back.
- `assembly_mate_set_value` — edit native-accepted Distance or Angle mate values and verify solved read-back.
- `assembly_coincident_mate_set_suppressed` — compatibility surface for Coincident-only suppression.
- `assembly_distance_mate_set_value` — compatibility surface for Distance-only value edit.
- `configuration_list`, `configuration_create`, `configuration_rename`, `configuration_delete`, `configuration_activate` — bounded configuration lifecycle.
- `configuration_set_dimension` — set a configuration-specific model dimension.
- `configuration_set_property`, `configuration_delete_property` — mutate document/configuration custom properties.
- `configuration_set_feature_suppressed` — set feature suppression for one explicit configuration.
- `configuration_set_material` — assign and read back one explicit SOLIDWORKS material for one configuration.
- `configuration_display_states_list`, `configuration_display_state_create`, `configuration_display_state_rename`, `configuration_display_state_delete` — bounded display-state lifecycle for one explicit configuration.
- `configuration_equations_list`, `configuration_equation_add`, `configuration_equation_set`, `configuration_equation_delete` — bounded equation/global-variable CRUD.
- `drawing_create` — create one new native drawing under the configured path policy.
- `drawing_sheet_create` — add and rebuild one explicit drawing sheet.
- `drawing_front_view_create` — create the native-accepted Front view from one native part source.
- `drawing_standard_view_create` — create bounded Front/Top/Right/Isometric views.
- `drawing_projected_view_create` — create a projected view from one explicit parent view.
- `drawing_section_view_create` — create a bounded section view from an explicit parent view and section line.
- `drawing_note_add` — add a non-dangling note to one explicit view.
- `drawing_center_marks_auto_insert` — auto-insert and read back persisted center-mark identities.
- `export_document` — export only accepted source/format pairs: part → STEP/IGES/Parasolid/STL/3MF, drawing → PDF/DXF/DWG; PDF may target one explicit sheet.
- `import_document` — import STEP/IGES/Parasolid into a new native `.SLDPRT` with geometry read-back.
- `evaluation_mass_properties` — read validated part mass/volume/area, center of mass, and inertia.
- `evaluation_bounding_box` — read a validated approximate part bounding box.
- `evaluation_geometry_sanity` — read body/error sanity and fail when native feature errors exist.
- `evaluation_measure` — measure one or two bounded stable part references and return validated distance/angle/radius/diameter values where applicable.
- `evaluation_interferences` — detect assembly interference pairs and validated overlap volume, including a clean zero-interference case.

All CAD paths are constrained by the same configured path policy as document operations. The provider does not expose arbitrary macros, scripts, COM method names, or raw native API argument lists.

## Capability honesty

Granular native capability keys are used for the accepted surface:

- `solidworks.sketch.geometry`
- `solidworks.sketch.rectangle`
- `solidworks.part.extrude`
- `solidworks.part.cut_extrude`
- `solidworks.part.multibody`
- `solidworks.part.combine`
- `solidworks.part.split`
- `solidworks.sheet_metal.base_flange`
- `solidworks.surface.extrude`
- `solidworks.body.inspect`
- `solidworks.body.combine`
- `solidworks.surface.thicken`
- `solidworks.sheet_metal.inspect`
- `solidworks.sheet_metal.flat_pattern`
- `solidworks.weldment.cut_list`
- `solidworks.weldment.structural_member`
- `solidworks.assembly.components`
- `solidworks.assembly.component_state`
- `solidworks.assembly.component_configuration`
- `solidworks.assembly.component_lifecycle`
- `solidworks.assembly.component_pattern`
- `solidworks.assembly.coincident_mate`
- `solidworks.assembly.common_mates`
- `solidworks.assembly.advanced_common_mates`
- `solidworks.assembly.mate_suppression`
- `solidworks.assembly.mate_value`
- `solidworks.assembly.coincident_mate_suppression`
- `solidworks.assembly.distance_mate_value`
- `solidworks.configuration.lifecycle`
- `solidworks.configuration.dimension`
- `solidworks.configuration.properties`
- `solidworks.configuration.feature_suppression`
- `solidworks.configuration.material`
- `solidworks.configuration.display_states`
- `solidworks.configuration.equations`
- `solidworks.drawing.lifecycle`
- `solidworks.drawing.front_view`
- `solidworks.drawing.standard_views`
- `solidworks.drawing.projected_view`
- `solidworks.drawing.section_view`
- `solidworks.drawing.note`
- `solidworks.drawing.center_mark`
- `solidworks.export.step`
- `solidworks.export.iges`
- `solidworks.export.parasolid`
- `solidworks.export.stl`
- `solidworks.export.3mf`
- `solidworks.export.pdf`
- `solidworks.export.dxf`
- `solidworks.export.dwg`
- `solidworks.export.pdf.single_sheet`
- `solidworks.import.step`
- `solidworks.import.iges`
- `solidworks.import.parasolid`
- `solidworks.evaluation.mass_properties`
- `solidworks.evaluation.bounding_box`
- `solidworks.evaluation.geometry_sanity`
- `solidworks.evaluation.measurement`
- `solidworks.evaluation.interference`

Broad `solidworks.part.parametric` remains `implemented=false` with `partial_native_support`: multiple bounded parametric subsets are promoted, but Sweep/Loft/Boundary and unrestricted feature-definition editing remain outside the accepted public surface. Broad `solidworks.assembly.mates` remains partial because the ten promoted common mate families do not imply Gear/Rack-Pinion/Screw or unrestricted mate semantics. Broad `solidworks.configurations` remains partial because deterministic design-table integration is intentionally unpromoted.

Lane D's bounded drawing lifecycle now includes standard-view parity, Projected, Section, Note, and Center Mark; STEP/IGES/Parasolid provider imports and exact-sheet PDF are also callable alongside the existing export matrix. Broad `solidworks.drawing`, `solidworks.export`, `solidworks.import`, and `solidworks.evaluation` remain `partial_native_support` rather than implying family-wide support; `solidworks.mbd` and `solidworks.license` remain unavailable.

The bounded Drawing/MBD acceptance boundary still excludes Detail View, direct drawing dimensions/model items, balloons, GTol/datum/surface-finish/weld-symbol breadth, BOM/cut-list tables, flat-pattern DXF integration, STEP 242 PMI publication, and native semantic MBD/DimXpert/PMI. Stable-reference measurement and assembly interference are separately promoted by the Evaluation surface and do not imply broader Drawing/MBD support.

`solidworks.simulation.study` is declared but `implemented=false`: native Simulation integration has not been built. Motion and Routing likewise remain outside the callable provider surface until their typed adapters and native gates exist. Add-ins are intended to load on demand rather than require Start Up. `solidworks.flow_simulation` and `solidworks.electrical` remain `implemented=false` until dedicated installation/license/API probes and native evidence exist.

## Native correctness

A COM return value alone is never sufficient proof of success. Mutations verify the relevant combination of feature identity, body/component count, native rebuild result, feature error state, and persisted artifact state. A mutation that times out after native dispatch is `uncertain`, not an ordinary failure, and dependent writes remain blocked until reconciliation.

## Authentication

Network mode requires a Bearer token sourced from deployment-managed runtime secrets. Missing or invalid credentials fail closed before MCP tool execution.

## Launch

Install the package and run `cdt-solidworks`. Agent 6 does not perform production deployment. The W-DEPLOY acceptance hook for Agent 7 is: build one wheel from the exact integrated SHA, hash it, install it non-editably outside the checkout, start the authenticated `/mcp` endpoint from that installed artifact, verify unauthenticated requests fail closed, compare `tools/list` with closed schemas and the capability map, execute one provider-owned native call on Windows with `AttachPolicy.START_NEW`, verify bounded telemetry, then perform a clean provider-owned shutdown. Linux startup/help/catalog smoke must not require SOLIDWORKS.

Network startup reads runtime configuration from these environment variable names only:

- `CDT_SOLIDWORKS_BEARER_TOKEN`
- `CDT_SOLIDWORKS_AUTH_ISSUER_URL`
- `CDT_SOLIDWORKS_RESOURCE_URL`
- `CDT_SOLIDWORKS_ALLOWED_ROOTS`
- `CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS`
- `CDT_SOLIDWORKS_BIND_HOST`
- `CDT_SOLIDWORKS_PORT`
- `CDT_SOLIDWORKS_VERSION`

Authentication configuration is mandatory and startup fails closed when it is incomplete. If allowed roots are omitted, document and CAD path operations remain disabled by policy. Weldment profile roots are configured independently from document roots; `weldment_create_structural_member` remains unavailable until `CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS` is configured, while read-only weldment inspection can remain available.
