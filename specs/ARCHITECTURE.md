# CDT_Engineer — Architecture

> Updated: 2026-09-09
> Scope: provider architecture, contract layering, backend boundaries, extensibility

## 1. Architectural Goal

CDT_Engineer must support multiple engineering applications without forcing them into one weak universal API. The architecture therefore separates:

```text
Transport / Auth / Provider lifecycle
              ↓
      SlncTrZ Provider Contract
              ↓
       CDT Common CAD Contract
              ↓
      Provider Extension Contract
              ↓
          Backend / Engine
              ↓
         Native application/API
```

The layers have different ownership and versioning rules.

## 2. Responsibility Boundaries

### 2.1 SlncTrZ Provider Contract

Defined by root `MCP_PROVIDER_STANDARD.md`.

Owns:

- Streamable HTTP `/mcp`;
- authentication behavior;
- mandatory `help` convention for first-class providers;
- provider identity;
- stable tool schemas;
- structured errors;
- versioning and contract fingerprinting;
- observability/security baseline;
- provider/gateway responsibility boundary.

It does **not** define CAD semantics.

### 2.2 CDT Common CAD Contract

Defines domain semantics which are useful across several engineering applications.

Examples:

- document lifecycle;
- object/entity discovery;
- object selection;
- transforms;
- organizational containers/layers;
- units and coordinate frames;
- import/export;
- transactions/undo where supported;
- validation/inspection;
- capability declaration.

The common contract is semantic, not implementation-specific. For example, `document_open` means opening a design document; it does not require COM, Ruby, `bpy`, or ezdxf.

### 2.3 Provider Extension Contract

Owns capabilities that would lose meaning if generalized.

Examples:

- AutoCAD layout/paperspace and DWG-specific operations;
- SketchUp scenes/components/tags;
- Blender sculpt brushes, face sets, dyntopo and modifiers;
- SolidWorks parametric features and assembly mates.

Extensions must not be promoted into common merely to make tool names look uniform.

### 2.4 Backend / Engine

A backend translates provider contracts into a concrete API.

Examples:

| Provider | Backend/engine |
| --- | --- |
| AutoCAD | `ezdxf` headless; Windows COM live |
| SketchUp | Ruby API/live bridge; future alternate engine if justified |
| Blender | in-process `bpy` addon/bridge; optional background/headless engine |
| SolidWorks | Windows COM/.NET automation |

Backend identity is runtime capability context, not provider identity.

## 3. Repository & Provider Shape

Runtime providers are independent repositories. `CDT_Engineer` is the architecture/specification hub.

```text
CDT_Engineer
CDT-AutoCAD
CDT-SketchUp
CDT-Blender
CDT-SolidWorks
```

Target logical shape inside each provider repository:

```text
<provider-repo>/
├── server/                 # MCP entry, lifecycle, registration
├── contracts/              # provider extension interfaces
├── backends/               # concrete engines
├── tools/                  # thin MCP tool adapters
├── models/                 # provider-specific models
├── security/               # provider-local validation
├── docs/
│   └── TOOL_GUIDE.md       # source for help contract
├── tests/
│   ├── contract/
│   ├── unit/
│   ├── integration/
│   └── correctness/
├── pyproject.toml or native build metadata
└── README.md
```

During migration, the current AutoCAD runtime may remain temporarily under `servers/autocad/` until a clean checkpoint is available. This transitional path is not the target architecture.

## 4. Tool Naming

Providers should expose bare MCP tool names. SlncTrZ-MCP owns canonical namespacing.

Provider side:

```text
help
document_open
object_get
```

Gateway catalog:

```text
autocad.help
autocad.document_open
autocad.object_get
```

Provider code must not depend on the gateway rewriting layer to execute business logic.

## 5. Common Contract vs Adapter Mapping

A common semantic may map to different native concepts.

| Common semantic | AutoCAD | SketchUp | Blender | SolidWorks |
| --- | --- | --- | --- | --- |
| document | drawing | model | blend file/scene | part/assembly/drawing doc |
| object | entity | entity/group/component | object/mesh | feature/body/component |
| organization | layer | tag/group | collection | feature tree/folder/config context |
| transform | move/rotate/scale | transformation | object/edit transforms | transform/mate/feature edit depending context |
| transaction | undo group/snapshot | operation/undo | undo stack/operator transaction boundary | command/undo/transaction-like API |

The mapping is allowed to be imperfect. Capability declaration tells the client what is safe and meaningful.

## 6. Capability Model

Each provider exposes a machine-readable capability map.

Recommended conceptual shape:

```json
{
  "provider": "autocad",
  "backend": "ezdxf",
  "capabilities": {
    "common.document.open": {"supported": true, "mode": "native"},
    "common.transaction.undo": {"supported": true, "mode": "snapshot"},
    "autocad.dwg.write": {"supported": false, "reason": "live_autocad_required"}
  }
}
```

Rules:

1. Capability keys are stable contract identifiers.
2. A declared unsupported capability must fail with a typed refusal, not fake success.
3. Tests must compare declared capabilities with callable behavior.
4. Backend-specific limitations belong in the capability result.
5. Capability presence does not grant authorization; auth/policy remains separate.

## 7. Common Contract Evolution

Promotion rule:

A concept may enter the common contract only when all are true:

1. It has stable semantics independent of one application's API.
2. At least two providers need it, or it is unambiguously foundational.
3. Mapping does not distort provider-native behavior.
4. A conformance test can define observable behavior.

Otherwise it stays a provider extension.

Changes should prefer additive versioning. Breaking semantic changes require a new contract version.

## 8. Backend Selection

Provider may have one or several engines.

### AutoCAD

```text
request
   ↓
provider contract
   ├── ezdxf backend  -> headless DXF
   └── COM backend    -> live AutoCAD + native DWG
```

The provider must not silently switch to a weaker backend when that would change semantics. If a requested capability requires COM and only ezdxf is active, return typed unsupported capability.

### Blender

Blender needs explicit execution context because sculpting and interactive modeling may require an open Blender process/context. A background renderer is not equivalent to an interactive `bpy` context.

## 9. Safety Architecture

Every provider must apply validation before side effects.

Required controls:

- path allow roots for file operations;
- file size bounds where applicable;
- bounded execution timeout;
- input/schema validation;
- no arbitrary command/script execution unless explicitly designed and separately authorized;
- separate destructive operations;
- safe save/export semantics;
- structured redacted errors;
- no credentials in arguments/results/logs.

For mutable design documents, timeouts that may leave background work running must be treated as integrity risks. If an engine cannot cancel safely, quarantine or rebind semantics should be considered rather than pretending cancellation succeeded.

## 10. Reuse Architecture

Reference repositories are treated as implementation evidence.

Reuse priority:

```text
behavior / tests / edge cases
        > small proven helper
        > backend implementation slice
        > whole module
        > whole server (avoid)
```

When code is reused directly:

- verify license;
- preserve required attribution;
- adapt to CDT contract instead of preserving upstream public API by accident;
- add local tests that define CDT behavior.

A reference without verified license is research-only until licensing is resolved.

## 11. Shared Runtime Extraction Rule

Do not build a framework before providers prove reuse. There is no permanent `servers/core/` target in the multi-repo architecture.

A separate `CDT-Provider-Kit` may be created only when at least two independent provider repositories demonstrate the same implementation need and equivalent semantics through conformance tests.

Good shared-package candidates:

- help/contract fingerprint utilities;
- capability/result models;
- error vocabulary;
- safe path validator;
- common telemetry primitives;
- contract conformance test harness.

Bad shared-package candidates:

- AutoCAD entity classes;
- SketchUp component logic;
- Blender mesh/sculpt implementation;
- SolidWorks feature/mate logic.

## 12. Adding a New Provider

A future provider such as Revit, Fusion 360, Rhino, FreeCAD, Inventor or CATIA follows:

```text
Provider identity
   ↓
SlncTrZ compliance
   ↓
Map Common CAD Contract
   ↓
Declare provider extensions
   ↓
Implement engine(s)
   ↓
Capability honesty tests
   ↓
MCP integration test
```

Existing providers should not need modification.

## 13. Architecture Invariants

These are non-negotiable:

- Gateway owns routing/policy/namespace; provider owns engineering logic.
- Common contract is semantic, not a union of every provider's tools.
- Provider-specific capability may remain provider-specific forever.
- No silent capability downgrade.
- No speculative shared core or `CDT-Provider-Kit` before Rule-of-Two evidence.
- Provider repositories release independently and must not import runtime code from another provider repository.
- Runtime docs/help must describe what is running, not future roadmap.
- Blender capability definition includes both modeling and sculpting.
- AutoCAD is the first implementation/reference provider.
