# Initial Handoff — Agent W / SolidWorks

## Repository

- Path: `/mnt/pc-dev/CDT-SolidWorks`
- GitHub: `SlncTrZ/CDT-SolidWorks`
- Branch: `main`
- Governing hub pin: `CDT_Engineer@643019c`

## Read first

1. `AGENTS.md`
2. `docs/SPEC_BASELINE.md`
3. `docs/ROADMAP.md`
4. `specs/MCP_PROVIDER_STANDARD.md`
5. `specs/ARCHITECTURE.md`
6. `specs/CONTRACTS.md`
7. `README.md`

## Starting state

Clean provider-native skeleton. No AutoCAD/Blender/SketchUp runtime code is present. The roadmap reference is MIT research input.

## First task

Research the supported SolidWorks version/API matrix and choose the COM/.NET boundary without prematurely pinning TargetFramework. Implement W0 identity/status/capability and native application smoke tests before W1 feature work.

## Restrictions

Do not modify another provider repo. Treat `CDT_Engineer` as read-only. Never return feature success without validating rebuild/error state.
