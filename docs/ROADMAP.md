# PLAN — SolidWorks Provider

> Lane: W · Target repo: `CDT-SolidWorks` · Updated: 2026-09-09
> Governing docs: `MCP_PROVIDER_STANDARD.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`

## 1. Objective

Build a SolidWorks MCP provider in an independent parallel delivery lane for parametric mechanical CAD automation: sketches, features, parts, assemblies, mates, configurations, drawings and engineering export.

SolidWorks must keep its parametric/feature-tree semantics. It must not be flattened into generic mesh-object operations.

## 2. Runtime Architecture

```text
MCP provider
   ↓
SolidWorks extension contract
   ↓
Windows COM/.NET automation backend
   ↓
SolidWorks application/document
```

Reference: `_private/reference/solidworks/solidworks-automation-skill` — MIT.

Reuse is allowed under license with attribution, but the provider contract/auth/error surface must be normalized to CDT/SlncTrZ.

## 3. Common Contract Mapping

| Common semantic | SolidWorks mapping |
| --- | --- |
| document lifecycle | part/assembly/drawing documents |
| object query | features, bodies, components, sketch entities |
| organization | feature tree/folders/configuration context |
| units/coordinates | document units + sketch/model transforms |
| transforms | component transforms; feature/sketch edits where semantically appropriate |
| selection | SolidWorks selection manager/context |
| import/export | native + STEP/IGES/PDF and supported formats |
| validation | rebuild status, feature errors, geometry/document checks |

## 4. SolidWorks Extension Contract

### Sketches

- sketch lifecycle;
- lines/arcs/circles/splines where supported;
- geometric constraints;
- dimensions;
- fully/under/over-defined status;
- sketch plane/context.

### Parametric Features

- extrude/cut;
- revolve;
- fillet/chamfer;
- patterns where justified;
- feature edit/suppress/unsuppress;
- feature parameters.

### Parts / Bodies

- body list/query;
- mass properties;
- material/properties;
- rebuild/check.

### Assemblies

- components;
- insert/replace components;
- transforms;
- mates;
- suppression states;
- lightweight/resolved state where relevant.

### Configurations

- list/create/activate;
- configuration-specific dimensions/properties;
- suppression differences.

### Drawings

- sheets/views;
- dimensions/annotations;
- BOM where API supports it reliably;
- PDF/export.

## 5. Implementation Phases

Lane W starts from versioned CDT contracts and remains runtime-independent from AutoCAD, SketchUp and Blender. Shared implementation is not a prerequisite for SolidWorks delivery.

### W0 — Provider Foundation

- SlncTrZ compliance;
- connect to live SolidWorks;
- `help`, status, capabilities;
- active document info;
- document open/save/close;
- basic feature/body/component query;
- MCP integration smoke tests.

### W1 — Part / Sketch Baseline

- sketch creation/query;
- primitive sketch geometry;
- constraints/dimensions;
- extrude/cut/revolve;
- rebuild/error status;
- correctness tests on generated parts.

### W2 — Assembly Baseline

- components;
- component transforms;
- mates;
- suppression;
- assembly rebuild;
- failure/refusal semantics for invalid mate state.

### W3 — Drawing / Configuration / Export

- configurations;
- engineering drawings;
- views/dimensions;
- BOM if stable;
- STEP/IGES/PDF export;
- document properties.

### W4 — Engineering Hardening

- mass properties;
- tolerance/GD&T integration if API behavior is reliable;
- interference checks;
- richer validation;
- performance tests on larger assemblies.

## 6. Capability Honesty

SolidWorks operations are strongly dependent on:

- document type;
- active selection/context;
- current configuration;
- feature state;
- application/license/version availability.

Capability/status reporting should distinguish global support from current context availability.

Examples:

```text
solidworks.sketch.constraints
solidworks.feature.extrude
solidworks.assembly.mates
solidworks.drawing.bom
```

A tool must not report success if a rebuild fails or a feature remains in an error state.

## 7. Safety / Correctness

- COM calls use bounded timeout/error normalization where feasible.
- Write operations identify the target document explicitly.
- Save/export paths are constrained.
- Feature creation validates units and parameter ranges before mutation.
- Rebuild status is checked after parametric edits.
- Assembly operations validate component/mate identity before side effects.
- No arbitrary macro/script execution by default.

## 8. Completion Gate

SolidWorks baseline is complete when:

- part/sketch parametric workflow passes;
- assembly/mate workflow passes;
- configuration/drawing/export baseline passes;
- rebuild failures are surfaced correctly;
- capability map matches runtime/context;
- SlncTrZ integration checklist passes on a Windows machine with SolidWorks available.
