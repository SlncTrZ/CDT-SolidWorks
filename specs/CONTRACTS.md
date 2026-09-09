# CDT_Engineer — Contract Model

> Updated: 2026-09-09
> Scope: common CAD contract, provider extensions, capability/refusal semantics

## 1. Contract Layers

CDT_Engineer uses three public contract layers:

```text
L0 — SlncTrZ Provider Contract
L1 — CDT Common CAD Contract
L2 — Provider Extension Contract
```

Backend APIs are implementation details below these layers.

Repository topology does not change these contract layers. Each runtime provider is independently versioned and depends on versioned CDT contracts rather than importing runtime code from another provider repository.

## 2. L0 — SlncTrZ Provider Contract

Source of truth: root `MCP_PROVIDER_STANDARD.md`.

Every first-class provider must implement:

- MCP Streamable HTTP at `/mcp` for network mode;
- fail-closed authentication;
- `help` read-only tool;
- stable provider identity;
- explicit schemas;
- structured safe errors;
- bounded timeout behavior;
- provider software + contract versioning;
- deterministic contract fingerprint;
- no secret leakage;
- provider-owned business logic.

Provider exposes bare tool names. Gateway canonicalizes `<provider>.<tool>`.

## 3. L1 — CDT Common CAD Contract

### 3.1 Identity & capability

Recommended tools:

```text
help
system_status
system_capabilities
```

Required semantics:

- identify provider and active backend;
- report current runtime capabilities truthfully;
- expose contract/software versions;
- report degraded/unavailable dependencies without broadening authority.

### 3.2 Document lifecycle

Semantic operations:

```text
document_new
document_open
document_info
document_save
document_save_as
document_close
```

Rules:

- document type is provider-specific;
- save format support is capability-driven;
- provider must never write bytes under a misleading extension;
- opening/saving must respect configured path boundaries.

### 3.3 Object query

Semantic operations:

```text
object_list
object_get
object_count
```

Common result fields should include stable identity/handle when the native API provides one, object type/category, organizational context and provider-specific properties.

Provider-specific payload belongs in an extension field rather than expanding the common schema endlessly.

### 3.4 Selection

Potential common operations:

```text
selection_by_ids
selection_filter
selection_clear
```

Geometric window/polygon selection may be common only where semantics are equivalent. AutoCAD-specific crossing/window behavior may stay in its extension until another provider proves the same contract.

### 3.5 Create / modify

Common semantics may include:

```text
object_delete
object_move
object_rotate
object_scale
object_copy
```

Creation primitives are common only when their geometry semantics are stable. `line`, `circle`, `polyline`, `text` are candidates; features such as SolidWorks extrusion or Blender sculpt stroke are not.

### 3.6 Organization

The common contract may define abstract organization operations:

```text
organization_list
organization_create
organization_assign
```

Native mappings vary:

- AutoCAD layer;
- SketchUp tag/group hierarchy;
- Blender collection;
- SolidWorks tree/configuration context.

Do not pretend these are identical if behavior differs. Providers may expose richer extension tools.

### 3.7 Units & coordinates

Every provider should be able to report:

- document units;
- coordinate frame/context;
- dimensionality;
- transform conventions.

Any conversion performed by the provider must be explicit and deterministic.

### 3.8 Transactions / undo

Conceptual operations:

```text
transaction_begin
transaction_commit
transaction_rollback
undo
redo
```

Support is optional and capability-driven. Snapshot-based rollback and native application undo are different modes and should be reported as such.

### 3.9 Import / export

Conceptual operations:

```text
import_asset
export_asset
```

Formats belong in capability metadata. Provider extensions may expose specialized exports such as AutoCAD PDF layout export or SolidWorks STEP.

### 3.10 Validation / inspection

Common semantic family:

```text
validate_document
inspect_object
measure_bounds
```

Engineering-specific validation remains extension-specific.

## 4. L2 — AutoCAD Extension Contract

Namespace ownership remains provider-side bare tool names; the labels below are semantic capability families.

### Drawing/DWG/DXF

- DXF read/write;
- native DWG read/write via COM;
- audit/purge;
- paperspace/layouts;
- PDF plotting/export.

### Drafting

- layers/linetypes;
- blocks/references;
- dimensions;
- hatch;
- trim/offset/fillet;
- viewport.

### Engineering

- GDT/tolerance annotations;
- measurements;
- 3D solids when supported by live AutoCAD.

### Engines

- `ezdxf`: headless, cross-platform, DXF-first;
- `com`: live Windows AutoCAD, native DWG and application capabilities.

Unsupported engine capabilities must raise typed refusal.

## 5. L2 — SketchUp Extension Contract

Provider-specific families:

- groups;
- component definitions/instances;
- tags;
- materials/textures;
- scenes/pages;
- faces/edges;
- push-pull/extrusion;
- component transforms;
- model hierarchy;
- SketchUp inference/context-sensitive modeling where deterministic automation is possible.

A group/component is not forced into AutoCAD block semantics even when conceptually related.

## 6. L2 — Blender Extension Contract

Blender has three major capability families; first two are mandatory for provider completion.

### 6.1 Modeling — mandatory

- object mode lifecycle;
- edit mode;
- mesh vertices/edges/faces;
- topology selection/edit;
- extrude/inset/bevel/loop operations;
- modifiers;
- transforms;
- collections;
- UV;
- materials;
- normals/topology inspection.

### 6.2 Sculpting — mandatory

- sculpt mode;
- brush selection/settings;
- deterministic stroke execution where possible;
- masks;
- face sets;
- voxel remesh;
- dynamic topology (dyntopo);
- multiresolution workflow;
- symmetry settings;
- mesh density/topology health checks.

Sculpting capability must report interactive-context requirements. A headless engine must not claim sculpt operations it cannot execute reliably.

### 6.3 Scene/render — additional

- cameras;
- lights;
- scene settings;
- render;
- image/export;
- animation later if justified.

Rendering alone does not satisfy Blender provider completion.

## 7. L2 — SolidWorks Extension Contract

Provider-specific families:

- sketch entities/constraints;
- dimensions/parameters;
- features (extrude, cut, revolve, fillet, chamfer…);
- bodies;
- parts;
- assemblies;
- components;
- mates;
- configurations;
- engineering drawings;
- BOM/properties where supported;
- STEP/IGES/PDF/native export.

Parametric feature semantics remain SolidWorks-specific; do not reduce them to generic mesh/object operations.

## 8. Capability Key Convention

Recommended stable key shape:

```text
common.<domain>.<capability>
autocad.<domain>.<capability>
sketchup.<domain>.<capability>
blender.<domain>.<capability>
solidworks.<domain>.<capability>
```

Examples:

```text
common.document.open
autocad.dwg.write
blender.sculpt.voxel_remesh
solidworks.assembly.mates
```

Capability metadata should include:

```text
supported
mode
reason
dependency (optional)
```

## 9. Refusal Contract

Unsupported capability is not a normal success result.

Conceptual structured error:

```json
{
  "kind": "unsupported_capability",
  "capability": "autocad.dwg.write",
  "backend": "ezdxf",
  "retryable": false,
  "message": "Native DWG writing requires the live AutoCAD backend."
}
```

Rules:

- no fake `{ok: true}`;
- no silent fallback that changes semantics;
- client can preflight through `system_capabilities`;
- tests guarantee declaration/runtime parity.

## 10. Versioning

Each provider tracks separately:

```text
provider_version
contract_version
common_contract_version
provider_extension_version
```

A provider may advance its extension contract without changing common contract.

Promoting a capability from extension to common should be additive first; old extension aliases may remain temporarily for compatibility if needed.

## 11. Conformance Testing

Every provider should run common contract tests for the capabilities it declares supported.

Test categories:

1. schema/validation;
2. capability honesty;
3. document lifecycle;
4. result normalization;
5. read/write side effects;
6. timeout/error behavior;
7. security/path containment;
8. MCP discovery/help;
9. provider-specific correctness.

A common test harness may later be extracted into `CDT-Provider-Kit` only after at least two independent provider repositories demonstrate equivalent behavior and Rule-of-Two evidence justifies the dependency. Until then, common conformance remains specification/test-definition work owned by `CDT_Engineer`.

## 12. Contract Promotion Rule

Promote an extension capability into common only when:

- at least two providers implement equivalent semantics;
- result/error behavior can be specified independently from native API;
- promotion improves interoperability rather than hiding differences;
- conformance tests can prove equivalence.

Otherwise keep it provider-specific.
