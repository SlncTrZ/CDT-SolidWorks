# Spec Baseline — CDT-SolidWorks

> Pinned: 2026-09-09
> Architecture source: `SlncTrZ/CDT_Engineer@643019c`

## Pinned inputs

| Local snapshot | Source at `643019c` |
| --- | --- |
| `specs/MCP_PROVIDER_STANDARD.md` | `MCP_PROVIDER_STANDARD.md` |
| `specs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` |
| `specs/CONTRACTS.md` | `docs/CONTRACTS.md` |
| `docs/ROADMAP.md` | `docs/PLAN_SOLIDWORKS.md` |

## Rules

- `specs/**` is read-only provider input.
- `CDT_Engineer` owns architecture/common-contract changes.
- This repo owns SolidWorks extension semantics and runtime implementation.
- Never silently track hub `main`; spec updates require an explicit pin update and conformance review.
- No runtime import from another CDT provider repository.
- Preserve parametric feature-tree, rebuild and assembly semantics instead of flattening them into generic mesh/object operations.

## Initial lane state

This repository starts as a clean provider-native skeleton. W0 must research the supported SolidWorks version/API boundary and choose the COM/.NET implementation shape before feature code is added.
