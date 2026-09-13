# CDT-SolidWorks

Independent SOLIDWORKS MCP provider for the CDT engineering program.

> Status: **bounded Mechanical 90 A–D provider surface integrated and native-verified on SOLIDWORKS 2024 SP0.1; mature 90–95 release gate not yet met** · Spec pin: `CDT_Engineer@643019c`

## Repository role

This repo owns SOLIDWORKS-native runtime code, tests, Windows COM integration and provider-facing documentation. Common architecture/contracts live in `SlncTrZ/CDT_Engineer`; pinned read-only snapshots are under `specs/`.

## Current native scope

The provider has native Windows evidence for:

- application/session lifecycle and document open/query/save/close/reopen;
- bounded sketch geometry creation/query, including line/centerline/circle/arc/ellipse/point/spline plus rectangular sketch primitives;
- solid extrude, hardened blind/through-all Cut Extrude, bounded boss/cut Revolve on profile construction centerlines with feature-specific uncertainty reconciliation, and multi-body creation;
- body inspection plus bounded Boolean combine operations and core Combine/Split workflows;
- sheet-metal Base Flange plus accepted sheet-metal inspection/flat-pattern state;
- extruded surfaces plus accepted surface thickening;
- weldment/cut-list inspection plus structural-member creation when profile roots are configured;
- assembly component insertion plus fix/float, suppress/resolve, and referenced-configuration state;
- Coincident, Parallel, Perpendicular, Distance, and Angle mate creation, with accepted Coincident suppression and Distance value editing;
- bounded configuration lifecycle, configuration-specific dimensions/properties/feature suppression, and equation/global-variable CRUD;
- drawing creation, sheet creation, and a non-dangling Front model view;
- STEP, IGES, Parasolid, STL, and 3MF geometry export with independent SOLIDWORKS reopen/read-back;
- PDF, DXF, and DWG drawing export with persisted artifact verification;
- part mass/volume/area, center of mass, inertia, bounding box, and geometry-sanity evaluation;
- rebuild/error validation and bounded path policy.

Mechanical 90 Lane D's bounded Drawing/Export/Evaluation subset is now registered in the provider and has passed a public-wrapper native smoke on SOLIDWORKS 2024. The provider still intentionally does **not** claim full part, assembly, configuration, drawing/detailing/BOM, evaluation, MBD, Simulation, Motion, Routing, Flow Simulation or Electrical coverage. Capability promotion remains granular and evidence-gated. The post-integration weighted audit confirms the mature 90–95 Mechanical Core release threshold is **not yet met**; unaccepted families remain explicitly partial/unavailable.

## Correctness rules

- Preserve SOLIDWORKS feature-tree, configuration and assembly semantics.
- A COM return value alone is not success; relevant rebuild/error state and postconditions must be read back.
- In-flight timeout after native dispatch is `uncertain` until reconciliation proves final state.
- Provider-owned and user-owned SOLIDWORKS sessions remain distinct; user sessions are never blindly killed.
- No arbitrary macro/script/dynamic COM invocation surface is exposed.

## Start here

1. Read `docs/SPEC_BASELINE.md`.
2. Read `docs/TOOL_GUIDE.md` for the callable provider surface.
3. Read `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, and `specs/CONTRACTS.md`.

Development roadmaps, research, handoffs and acceptance evidence are intentionally kept outside the public documentation set.
