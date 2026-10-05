# CDT-SolidWorks

Independent SOLIDWORKS MCP provider for the CDT engineering program.

> Status: **release-qualified for the declared SOLIDWORKS 2024 SP0.1 provider scope at source 9537cc313020ffb26eb21cfd9ab6810a2671a5dc; gateway-controlled lifecycle acceptance remains separate** · Spec pin: `CDT_Engineer@643019c`
>
> **CDT certified host target:** SOLIDWORKS **2024 SP0.1 on Windows**. Current
> acceptance and release scoring are bound to that target only. Other SOLIDWORKS
> releases are not implied supported or unsupported; they are unverified
> compatibility candidates that may be added later and are not part of the
> current quality score.

## Repository role

This repo owns SOLIDWORKS-native runtime code, tests, Windows COM integration and provider-facing documentation. Common architecture/contracts live in `SlncTrZ/CDT_Engineer`; pinned read-only snapshots are under `specs/`.

## Current native scope

The provider has native Windows evidence for:

- application/session lifecycle and document open/query/save/close/reopen;
- bounded sketch geometry creation/query plus native-passed common relations, definition state, linear/angular/radius/diameter dimensions, relation deletion and dimension editing;
- solid extrude, hardened blind/through-all Cut Extrude, bounded boss/cut Revolve, bounded Simple Hole and native-passed ANSI Metric M2/M4/M6 countersink Hole Wizard;
- native-passed Fillet/Chamfer/Shell, Draft/Rib, Linear/Circular Pattern/Mirror, bounded reference plane/axis/point, and whitelist-only feature query/rename/suppression/fillet-radius edit;
- multi-body creation;
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

M95-R3 Agent A's evidence-backed Sketch/Parametric Part subset is registered through granular public tools and capabilities; broader `solidworks.part.parametric` remains intentionally partial. Mechanical 90 Lane D's bounded Drawing/Export/Evaluation subset remains registered and native-verified. The provider still intentionally does **not** claim full part, assembly, configuration, drawing/detailing/BOM, evaluation, MBD, Simulation, Motion, Routing, Flow Simulation or Electrical coverage. Capability promotion remains granular and evidence-gated. Final exact-source acceptance at `9537cc313020ffb26eb21cfd9ab6810a2671a5dc` is **PASS / RELEASE-QUALIFIED FOR DECLARED PROVIDER SCOPE**. Unaccepted families remain explicitly partial/unavailable; this does not certify full Mechanical suite coverage or the gateway-controlled lifecycle lane.

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

## Gateway-controlled execution lifecycle

Integration target: an authorized lifecycle controller ensures the native runtime, verifies readiness, syncs the already-registered gateway provider, verifies activation, and refreshes client tools/list. The lifecycle tools are not implemented or advertised by this provider merely because this guide exists. A stopped engine must not be the only endpoint capable of starting itself.

Require the accepted SOLIDWORKS 2024 SP0.1 session and STA worker; distinguish provider-owned and user-owned instances. Preserve rebuild/read-back verification and uncertainty quarantine. Gateway/lifecycle acceptance is separate from declared-scope native release qualification.

Stop/drain requires verified ownership, no unresolved mutation and explicit dirty-document handling. Do not kill all application processes or silently discard work. Gateway hot activation does not require a gateway restart and may change the provider generation.

Interface reference: [CDT_Engineer Execution Lifecycle Contract](https://github.com/SlncTrZ/CDT_Engineer/blob/main/docs/EXECUTION_LIFECYCLE_CONTRACT.md). The contract is a draft target and is not published by this documentation-only workspace update; it is available in the sibling CDT_Engineer checkout. Existing pinned `specs/**` remain unchanged.
