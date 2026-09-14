# CDT-SolidWorks Tool Guide

> Status: bounded Mechanical 90 provider surface through M95-R3 Agent A public integration; native-verified subsets on SOLIDWORKS 2024 SP0.1 · Updated: 2026-09-14

## Current callable surface

The provider exposes platform identity/status/capabilities, application lifecycle, explicit document lifecycle/query/rebuild/reconciliation, and a bounded native CAD-core surface.

### Native CAD core

| Tool | Current native scope |
| --- | --- |
| `sketch_create_geometry` | Bounded explicit sketch geometry in an opened part: line, centerline, circle, arc, ellipse, point, cubic spline; may include the native-passed relation and dimension subsets below |
| `sketch_get` | Read back one explicit native sketch by stable sketch identity, including definition state, relations and dimensions |
| `sketch_relations_list` | List native-passed sketch relations using persisted relation identity |
| `sketch_relation_delete` | Delete one explicit sketch relation with rebuild/read-back verification |
| `sketch_dimension_set` | Edit one explicit native-passed sketch dimension in `mm` or `deg` with rebuild/read-back verification |
| `sketch_create_rectangle` | New part, Front/Top/Right plane, rectangular 2D sketch |
| `part_create_rect_extrude` | New rectangular solid boss |
| `part_add_rect_extrude` | Add boss; `merge=false` supports multi-body creation |
| `part_cut_extrude` | Native blind or through-all Cut Extrude from a sketch on Front/Top/Right standard reference planes in an opened millimeter part; path + revision identity required |
| `part_cut_reconcile` | Reconcile an uncertain Cut Extrude by native call ID; clears quarantine only after expected Cut definition, clean rebuild, and solid-body verification |
| `part_simple_hole` | Native single-center blind/through-all Simple Hole on a one-solid-body part using `face_ref=bbox:+z`; selected face must be planar with outward +Z normal; center is model-space `[x_mm, y_mm]` |
| `part_simple_hole_reconcile` | Reconcile uncertain Simple Hole by call ID; verifies native type, diameter, center, end condition/depth, rebuild and solid body |
| `part_revolve` | Native solid Revolve from a Front/Top/Right sketch with exactly one construction centerline; `axis_ref=profile_centerline`; angle `(0, 360]` degrees |
| `part_revolve_cut` | Native Revolve Cut with the same bounded profile-centerline/angle contract |
| `part_revolve_reconcile` | Reconcile uncertain boss/cut Revolve by call ID; verifies type, centerline axis, angle, rebuild and solid body before clearing quarantine |
| `part_hole_wizard` | ANSI Metric countersink / Flat Head Screw ANSI B18.6.7M, native-passed sizes `M2`, `M4`, `M6`, one center, through-all, one solid body, `face_ref=bbox:+z` only |
| `part_fillet` | Constant-radius fillet on the native-passed `bbox:edge:+x:+z` selector; tangent propagation remains disabled |
| `part_chamfer` | Distance-angle chamfer on the native-passed `bbox:edge:-x:+z` selector |
| `part_shell` | Shell using the native-passed `bbox:+z` face removal selector, inward only |
| `part_draft` | Neutral-plane draft for `face_refs=[bbox:+x]`, `neutral_plane_ref=bbox:+z`, non-reversed direction |
| `part_rib` | Rib from one explicit sketch profile with native-passed `both_sides=true` contract |
| `part_linear_pattern` | Feature linear pattern with explicit seed identities, `direction_ref=bbox:edge:+y:+z`, `geometry_pattern=false` |
| `part_circular_pattern` | Feature circular pattern around an explicitly promoted reference-axis feature, `geometry_pattern=false` |
| `part_mirror` | Feature mirror about `plane:right`, `geometry_pattern=false` |
| `part_reference_plane` / `part_reference_axis` / `part_reference_point` | Native-passed reference combinations: offset from `plane:front`; axis from `plane:top` + `plane:right`; point from `bbox:+z` |
| `part_feature_get` / `part_feature_rename` / `part_feature_set_suppressed` | Explicit feature query/rename/suppress/unsuppress with identity and rebuild/read-back gates |
| `part_feature_set_parameter` | Whitelist-only feature edit; currently only constant-fillet `radius_mm`, never an arbitrary feature-definition surface |
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

The production native services have been exercised on SOLIDWORKS 2024 through provider-owned COM sessions. Accepted evidence includes sketch relation/definition-state/dimension workflows, Hole Wizard M2/M4/M6, Fillet/Chamfer/Shell, Draft/Rib, Linear/Circular Pattern/Mirror, bounded reference geometry, feature query/rename/suppression/fillet-radius edit, part creation, blind/through-all Cut Extrude with save/reopen and volume read-back, Revolve/Revolve Cut, Simple Hole, a second non-merged body, Combine, Split, sheet-metal base flange, surface extrusion, assembly/configuration workflows, clean rebuild/error checks, native saves/reopens, and provider-owned cleanup.

Capability promotion remains granular. `solidworks.part.parametric` intentionally remains partial even though the evidence-backed Agent A feature groups above are now individually callable and advertised. Sweep, Loft/Boundary and broader arbitrary topology/feature-definition editing remain unavailable. Full assembly-mate, configuration, drawing, evaluation and MBD families also remain partial; no broad capability is promoted merely because a neighboring subset exists.

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
