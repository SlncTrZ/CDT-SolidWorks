# CDT-SolidWorks Tool Guide

> Status: native CAD-core acceptance verified on SOLIDWORKS 2024 SP0.1 · Updated: 2026-09-13

## Current callable surface

The provider exposes platform identity/status/capabilities, application lifecycle, explicit document lifecycle/query/rebuild/reconciliation, and a bounded native CAD-core surface.

### Native CAD core

| Tool | Current native scope |
| --- | --- |
| `sketch_create_rectangle` | New part, Front/Top/Right plane, rectangular 2D sketch |
| `part_create_rect_extrude` | New rectangular solid boss |
| `part_add_rect_extrude` | Add boss; `merge=false` supports multi-body creation |
| `part_combine_all_bodies` | Boolean Add of all current solid bodies |
| `part_split_by_plane` | Split using Front/Top/Right standard plane |
| `sheet_metal_create_base_flange` | Base flange with thickness/bend-radius read-back |
| `surface_create_extrude` | Line-profile surface extrusion |
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

Paths are constrained to configured allowed roots. Create operations refuse to overwrite an existing native document. Unknown tool fields fail loud instead of being silently discarded.

## Verification status

The production native services have been exercised on SOLIDWORKS 2024 through provider-owned COM sessions. Accepted evidence includes part creation, a second non-merged body, Combine, Split, sheet-metal base flange, surface extrusion, assembly creation, component state/configuration persistence, Coincident/Parallel/Perpendicular/Distance/Angle mate creation, Distance mate editing, configuration lifecycle, configuration-specific dimensions/properties/feature suppression, equation/global-variable CRUD, clean rebuild/error checks, native saves/reopens, and provider-owned cleanup.

Capability promotion is granular. Full parametric-part and full assembly-mate families are not claimed yet because Cut/Revolve and additional mate families remain outside the promoted provider surface. Broad `solidworks.configurations` also remains partial: the accepted lifecycle/dimension/property/feature-suppression/equation slices are callable, while wider configuration/design-table behavior is not claimed. Drawing and export remain unavailable until their native integration gates pass.

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
