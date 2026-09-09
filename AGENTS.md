# AGENTS.md — CDT-SolidWorks

## Role

Lane W owns the SolidWorks MCP provider runtime only. `CDT_Engineer` is the architecture/spec/control source and is read-only to this lane.

## Governing spec baseline

- Source: `SlncTrZ/CDT_Engineer@643019c`
- Read first: `docs/SPEC_BASELINE.md`, `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, `specs/CONTRACTS.md`, `docs/ROADMAP.md`.
- `specs/**` is a pinned read-only snapshot; contract changes belong in `CDT_Engineer`.

## Ownership boundary

Allowed: `CDT-SolidWorks/**` only.

Forbidden unless explicitly assigned:

- AutoCAD/SketchUp/Blender runtime repositories;
- provider business logic in `CDT_Engineer`;
- importing runtime code from another provider;
- creating `CDT-Provider-Kit` before Rule-of-Two evidence;
- arbitrary macro/script execution surfaces;
- reporting feature success when SolidWorks rebuild/error state says otherwise.

## First objective — W0/W1

1. Research and choose the COM/.NET boundary against the supported SolidWorks installation/version matrix.
2. Implement provider identity/help/status/capabilities, including application/license/version availability.
3. Implement document lifecycle and basic feature/body/component query.
4. Build sketch + parametric part baseline: constraints, dimensions, extrude/cut/revolve.
5. Every parametric mutation must check rebuild/error state before returning success.

## Required workflow

1. Follow the global SlncTrZ Agent Harness from `context.bootstrap`.
2. Research API/version semantics before implementation; preserve feature-tree/parametric concepts.
3. TDD and capability/context honesty tests are mandatory.
4. Validate document type, selection/context, units and identity before side effects.
5. COM calls need bounded timeout/error normalization where feasible.
6. Run focused + full tests and hygiene before commit.
7. Log every code/deploy change with CyberBrain `kb.knowledge_store`.
8. End each session with episodic save then `dream_enqueue`.
9. Commit/push only this repo; default branch `main` unless a task defines otherwise.

## Native-verification rule

Part, assembly and drawing claims must be verified against a real supported SolidWorks installation. A generated feature that fails rebuild is a failure, not a successful tool result.
