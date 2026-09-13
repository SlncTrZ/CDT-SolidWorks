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

Paths are constrained to configured allowed roots. Create operations refuse to overwrite an existing native document. Unknown tool fields fail loud instead of being silently discarded.

## Verification status

The production `CadCoreService` has been exercised on SOLIDWORKS 2024 through a provider-owned COM session. The accepted sequence includes part creation, a second non-merged body, Combine, Split, sheet-metal base flange, surface extrusion, assembly creation, coincident mate creation, clean rebuild/error checks, native saves, and provider-owned cleanup.

Capability promotion is granular. Full parametric-part and full assembly-mate families are not claimed yet because Cut/Revolve and additional mate families are still outside the production-native surface. Configuration, drawing and export remain unavailable.

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
