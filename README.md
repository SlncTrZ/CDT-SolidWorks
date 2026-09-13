# CDT-SolidWorks

Independent SOLIDWORKS MCP provider for the CDT engineering program.

> Status: **native CAD core operational on SOLIDWORKS 2024 SP0.1** · Spec pin: `CDT_Engineer@643019c`

## Repository role

This repo owns SOLIDWORKS-native runtime code, tests, Windows COM integration and provider-facing documentation. Common architecture/contracts live in `SlncTrZ/CDT_Engineer`; pinned read-only snapshots are under `specs/`.

## Current native scope

The provider has native Windows evidence for:

- application/session lifecycle and document open/query/save/close/reopen;
- rectangular 2D sketch creation;
- solid extrude and multi-body creation;
- body Combine and Split;
- sheet-metal Base Flange;
- extruded surfaces;
- assembly component insertion plus fix/float, suppress/resolve, and referenced-configuration state;
- Coincident, Parallel, Perpendicular, Distance, and Angle mate creation, with accepted Coincident suppression and Distance value editing;
- bounded configuration lifecycle, configuration-specific dimensions/properties/feature suppression, and equation/global-variable CRUD;
- rebuild/error validation and bounded path policy.

The provider intentionally does **not** claim full part, assembly, configuration, drawing, Simulation, Motion, Routing, Flow Simulation or Electrical coverage until each capability family has production wiring and native acceptance evidence. Assembly and configuration promotion is granular; unverified mate families, design-table behavior, and other uncovered subfamilies remain partial/unavailable.

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
