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

- `sketch_create_geometry` — create bounded explicit sketch geometry in an opened native part.
- `sketch_get` — read one explicit native sketch with stable identity/read-back.
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
- `assembly_mate_create` — create Coincident, Parallel, Perpendicular, Distance, or Angle mates using bounded selection references.
- `assembly_mates_list` — list stable mate identity/type/state.
- `assembly_coincident_mate_set_suppressed` — suppress/unsuppress an accepted Coincident mate.
- `assembly_distance_mate_set_value` — edit an accepted Distance mate and verify persisted value.
- `configuration_list`, `configuration_create`, `configuration_rename`, `configuration_delete`, `configuration_activate` — bounded configuration lifecycle.
- `configuration_set_dimension` — set a configuration-specific model dimension.
- `configuration_set_property`, `configuration_delete_property` — mutate document/configuration custom properties.
- `configuration_set_feature_suppressed` — set feature suppression for one explicit configuration.
- `configuration_equations_list`, `configuration_equation_add`, `configuration_equation_set`, `configuration_equation_delete` — bounded equation/global-variable CRUD.
- `drawing_create` — create one new native drawing under the configured path policy.
- `drawing_sheet_create` — add and rebuild one explicit drawing sheet.
- `drawing_front_view_create` — create the native-accepted Front view from one native part source.
- `export_document` — export only accepted source/format pairs: part → STEP/IGES/Parasolid/STL/3MF, drawing → PDF/DXF/DWG.
- `evaluation_mass_properties` — read validated part mass/volume/area, center of mass, and inertia.
- `evaluation_bounding_box` — read a validated approximate part bounding box.
- `evaluation_geometry_sanity` — read body/error sanity and fail when native feature errors exist.

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
- `solidworks.assembly.coincident_mate`
- `solidworks.assembly.common_mates`
- `solidworks.assembly.coincident_mate_suppression`
- `solidworks.assembly.distance_mate_value`
- `solidworks.configuration.lifecycle`
- `solidworks.configuration.dimension`
- `solidworks.configuration.properties`
- `solidworks.configuration.feature_suppression`
- `solidworks.configuration.equations`
- `solidworks.drawing.lifecycle`
- `solidworks.drawing.front_view`
- `solidworks.export.step`
- `solidworks.export.iges`
- `solidworks.export.parasolid`
- `solidworks.export.stl`
- `solidworks.export.3mf`
- `solidworks.export.pdf`
- `solidworks.export.dxf`
- `solidworks.export.dwg`
- `solidworks.evaluation.mass_properties`
- `solidworks.evaluation.bounding_box`
- `solidworks.evaluation.geometry_sanity`

Broad `solidworks.part.parametric` remains `implemented=false` with `partial_native_support`: native Cut Extrude is now promoted, but Revolve and the remaining parametric feature family still require their own production native gates. Broad `solidworks.assembly.mates` remains partial because Concentric/Tangent/Lock/Width/Slot and other mate behavior are not promoted without direct native gates. Broad `solidworks.configurations` remains partial because design-table and wider configuration-state automation are not yet promoted.

Lane D's bounded drawing lifecycle, STEP/IGES/Parasolid/STL/3MF geometry export, PDF/DXF/DWG drawing export, and part evaluation are now callable and have passed public-wrapper native smoke. Broad `solidworks.drawing`, `solidworks.export`, and `solidworks.evaluation` remain `partial_native_support` rather than implying family-wide support; `solidworks.mbd` and `solidworks.license` remain unavailable.

The Lane D acceptance boundary still excludes projected/section/detail views, dimensions/annotations/BOM, STEP 242 PMI publication, single-sheet PDF, measurement/interference, and native MBD/DimXpert/PMI.

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
- `CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS`
- `CDT_SOLIDWORKS_BIND_HOST`
- `CDT_SOLIDWORKS_PORT`
- `CDT_SOLIDWORKS_VERSION`

Authentication configuration is mandatory and startup fails closed when it is incomplete. If allowed roots are omitted, document and CAD path operations remain disabled by policy. Weldment profile roots are configured independently from document roots; `weldment_create_structural_member` remains unavailable until `CDT_SOLIDWORKS_WELDMENT_PROFILE_ROOTS` is configured, while read-only weldment inspection can remain available.
