# CDT-SolidWorks

Independent SolidWorks MCP provider for the CDT engineering program.

> Status: **W0 skeleton ready** · Runtime implementation not started · Spec pin: `CDT_Engineer@643019c`

## Repository role

This repo owns SolidWorks-native runtime code, tests, COM/.NET integration and provider documentation. Common architecture/contracts live in `SlncTrZ/CDT_Engineer`; pinned read-only snapshots are under `specs/`.

## First objective

W0/W1 establishes the supported SolidWorks/API boundary and delivers:

- provider identity/help/status/capabilities;
- application/license/version availability;
- document lifecycle;
- feature/body/component query;
- sketch entities, constraints and dimensions;
- parametric extrude/cut/revolve with rebuild/error validation.

A feature is not successful if SolidWorks rebuild state reports failure.

## Start here

1. Read `AGENTS.md`.
2. Read `docs/INITIAL_HANDOFF.md` and `docs/ROADMAP.md`.
3. Read `docs/SPEC_BASELINE.md` plus `specs/**`.
4. Research the current supported SolidWorks COM/.NET API/version matrix before choosing project structure/TargetFramework.

The roadmap reference `solidworks-automation-skill` is MIT-licensed research input; normalize behavior to CDT/SlncTrZ contracts.
