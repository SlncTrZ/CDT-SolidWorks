# CDT-SolidWorks Tool Guide

> Status: bounded Mechanical 90 A–D provider surface callable and native-verified on SOLIDWORKS 2024 SP0.1 · Updated: 2026-09-13

## Current callable surface

The provider exposes platform identity/status/capabilities, application lifecycle, explicit document lifecycle/query/rebuild/reconciliation, and a bounded native CAD-core surface.

### Native CAD core

| Tool | Current native scope |
| --- | --- |
| `sketch_create_geometry` | Bounded explicit sketch geometry in an opened part: line, centerline, circle, arc, ellipse, point, cubic spline |
| `sketch_get` | Read back one explicit native sketch by stable sketch identity |
| `sketch_create_rectangle` | New part, Front/Top/Right plane, rectangular 2D sketch |
| `part_create_rect_extrude` | New rectangular solid boss |
| `part_add_rect_extrude` | Add boss; `merge=false` supports multi-body creation |
| `part_cut_extrude` | Native blind or through-all Cut Extrude from a sketch on Front/Top/Right standard reference planes in an opened millimeter part; path + revision identity required |
| `part_cut_reconcile` | Reconcile an uncertain Cut Extrude by native call ID; clears quarantine only after expected Cut definition, clean rebuild, and solid-body verification |
| `part_combine_all_bodies` | Boolean Add of all current solid bodies |
| `part_split_by_plane` | Split using Front/Top/Right standard plane |
| `sheet_metal_create_base_flange` | Base flange with thickness/bend-radius read-back |
| `surface_create_extrude` | Line-profile surface extrusion |
| `body_inspect` | Read bounded solid/surface-body state and identity |
| `body_combine` | Explicit body Boolean `add` / `subtract` / `common` on named bodies |
| `surface_thicken` | Thicken one explicitly named surface body with solid-body read-back |
| `sheet_metal_inspect` | Read accepted Base Flange / Flat Pattern state |
| `sheet_metal_set_flattened` | Persist Flat Pattern suppression state |
| `weldment_inspect` | Read structural-member and cut-list state |
| `weldment_create_structural_member` | Create a structural member from an allowed `.sldlfp` profile and named path sketch |
| `assembly_create` | Insert native part/assembly components at XYZ placements |
| `assembly_add_coincident_plane_mate` | Coincident standard-plane mate |
| `assembly_components_list` | Explicit recursive/non-recursive native component read-back |
| `assembly_component_set_fixed` | Fix/float one explicit component with rebuild/read-back |
| `assembly_component_set_load_state` | Native-accepted `resolved` / `suppressed` component state |
| `assembly_component_set_configuration` | Referenced configuration read-back for one component |
| `assembly_mate_create` | Native-accepted Coincident / Parallel / Perpendicular / Distance / Angle mate creation |
| `assembly_mates_list` | Stable mate identity/type/state read-back |
| `assembly_coincident_mate_set_suppressed` | Coincident mate suppress/unsuppress with solved-state read-back |
| `assembly_distance_mate_set_value` | Distance mate value edit with persisted read-back |
| `configuration_list` / `configuration_create` / `configuration_rename` / `configuration_delete` / `configuration_activate` | Bounded configuration lifecycle |
| `configuration_set_dimension` | Configuration-specific model dimension mutation in system units |
| `configuration_set_property` / `configuration_delete_property` | Document/configuration custom property mutation |
| `configuration_set_feature_suppressed` | Configuration-specific feature suppression |
| `configuration_equations_list` / `configuration_equation_add` / `configuration_equation_set` / `configuration_equation_delete` | Equation/global-variable CRUD with canonical read-back |
| `drawing_create` | Create a new native `.SLDDRW` using the configured/default SOLIDWORKS drawing template |
| `drawing_sheet_create` | Add and rebuild one explicit drawing sheet |
| `drawing_front_view_create` | Create the native-accepted Front model view from one native part source |
| `export_document` | Part → STEP/IGES/Parasolid/STL/3MF or drawing → PDF/DXF/DWG; target overwrite refused |
| `evaluation_mass_properties` | Read validated part mass, volume, surface area, center of mass, and inertia |
| `evaluation_bounding_box` | Read a validated approximate part bounding box |
| `evaluation_geometry_sanity` | Read body/feature-error sanity and fail on native feature errors |

Paths are constrained to configured allowed roots. Create operations refuse to overwrite an existing native document. Unknown tool fields fail loud instead of being silently discarded.

## Verification status

The production native services have been exercised on SOLIDWORKS 2024 through provider-owned COM sessions. Accepted evidence includes part creation, blind/through-all Cut Extrude with save/reopen and volume read-back, a second non-merged body, Combine, Split, sheet-metal base flange, surface extrusion, assembly creation, component state/configuration persistence, Coincident/Parallel/Perpendicular/Distance/Angle mate creation, Distance mate editing, configuration lifecycle, configuration-specific dimensions/properties/feature suppression, equation/global-variable CRUD, clean rebuild/error checks, native saves/reopens, and provider-owned cleanup.

Capability promotion is granular. Full parametric-part and full assembly-mate families are not claimed yet: Cut Extrude is promoted, while Revolve and the remaining parametric feature families plus additional mate families remain outside the promoted provider surface. Broad `solidworks.configurations` also remains partial: the accepted lifecycle/dimension/property/feature-suppression/equation slices are callable, while wider configuration/design-table behavior is not claimed.

## Lane D accepted boundary

The callable Lane D surface above has passed a public-wrapper native smoke on SOLIDWORKS 2024 SP0.1. Geometry exports are independently reopened and checked through SOLIDWORKS; drawing exports persist non-empty typed artifacts; evaluation is read-only. The accepted boundary deliberately excludes projected/section/detail views, drawing dimensions/annotations/BOM, STEP 242 PMI publication, single-sheet PDF export, measurement/interference tools, and native MBD/DimXpert/PMI. Those remain unavailable until their own deterministic native gates pass.

## Add-in capability status

- SOLIDWORKS Simulation, Motion and Routing adapters are not callable yet; provider integration will load add-ins on demand rather than require Start Up.
- Full Flow Simulation availability is not claimed until a dedicated installation/license/API probe succeeds.
- SOLIDWORKS Electrical availability is not claimed until a dedicated installation/license/API probe succeeds.

The provider reports those families as unsupported/unverified instead of returning fake success.

## Correctness and timeout semantics

Mutations must pass native postconditions and rebuild/feature-error checks before success. An in-flight timeout becomes `uncertain`; dependent mutations remain blocked until reconciliation proves final state.

## Authentication and launch

Network mode is fail-closed behind Bearer authentication. Install the package and run `cdt-solidworks` with deployment-managed values for:

- `CDT_SOLIDWORKS_BEARER_TOKEN`
- `CDT_SOLIDWORKS_AUTH_ISSUER_URL`
- `CDT_SOLIDWORKS_RESOURCE_URL`
- `CDT_SOLIDWORKS_ALLOWED_ROOTS`
- `CDT_SOLIDWORKS_BIND_HOST`
- `CDT_SOLIDWORKS_PORT`
- `CDT_SOLIDWORKS_VERSION`

If allowed roots are not configured, document and CAD path operations remain disabled.
