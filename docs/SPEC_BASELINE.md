# Spec Baseline — CDT-SolidWorks

> Pinned: 2026-09-09
> Architecture source: `SlncTrZ/CDT_Engineer@643019c`

## Pinned normative inputs

| Local snapshot | Source at `643019c` |
| --- | --- |
| `specs/MCP_PROVIDER_STANDARD.md` | `MCP_PROVIDER_STANDARD.md` |
| `specs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` |
| `specs/CONTRACTS.md` | `docs/CONTRACTS.md` |

The initial hub planning document was useful to bootstrap the lane, but implementation roadmaps are development artifacts rather than normative product contracts and are not maintained as public source-of-truth documents in this repo.

## Rules

- `specs/**` is read-only provider input.
- `CDT_Engineer` owns architecture/common-contract changes.
- This repo owns SOLIDWORKS extension semantics and runtime implementation.
- Never silently track hub `main`; spec updates require an explicit pin update and conformance review.
- No runtime import from another CDT provider repository.
- Preserve parametric feature-tree, rebuild, document, configuration and assembly semantics instead of flattening them into generic mesh/object operations.
- Capability declarations must match callable behavior and current backend/version/license/context.

## Current implementation relationship to the pin

The provider has advanced beyond the original skeleton state and now has native SOLIDWORKS 2024 evidence for a bounded CAD-core surface. This implementation progress does not change the pinned contract baseline. New capability families remain provider extensions until the governing contract is deliberately updated.
