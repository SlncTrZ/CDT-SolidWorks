# CDT-SolidWorks Provider

The running provider exposes a capability-honest W0 native/document surface. Parametric part, assembly, configuration, drawing, and export domain code is present in this integration branch but remains unavailable until a real SolidWorks native adapter and Windows acceptance evidence exist.

## Platform tools

- `help` — read-only provider identity, version, contract fingerprint and this guide content.
- `system_status` — provider/backend dependency state.
- `system_capabilities` — separates `implemented` from currently `available`.

## Application tools

- `application_probe` — read-only SolidWorks registration/running/version probe.
- `application_connect` — attach to or start a SolidWorks application session using an explicit attach policy.
- `application_disconnect` — disconnect; only provider-owned applications may be exited.

## Document tools

- `document_open`
- `document_info`
- `document_save`
- `document_save_as`
- `document_close`
- `document_reopen`
- `document_list_features`
- `document_list_bodies`
- `document_list_components`
- `document_rebuild`
- `document_reconcile`

Document access is disabled unless deployment config supplies at least one allowed filesystem root. Mutating document tools require explicit document identity/revision context; stale identity is rejected.

## Authentication

Network mode requires a Bearer token sourced from deployment-managed runtime secrets. Missing or invalid credentials fail closed before MCP tool execution.

## Native correctness

A COM return value alone is not success. Document lifecycle operations verify native postconditions. `document_rebuild` rejects success when SolidWorks rebuild/feature error state is not clean. A mutation that times out after native dispatch becomes `uncertain` and blocks dependent mutation until reconciliation succeeds.

## Capability limits

`solidworks.license`, `solidworks.part.parametric`, `solidworks.assembly.mates`, `solidworks.configurations`, `solidworks.drawing`, and `solidworks.export` are intentionally reported `implemented=false` at provider level until their domain lanes are connected to real native operations and verified on a supported Windows SolidWorks installation.

## Verification boundary

Linux/unit/contract tests can validate provider composition and safety semantics but cannot certify native SolidWorks behavior. Native release claims require Windows + supported SolidWorks evidence, including save/reopen and negative rebuild/recovery cases.


## Launch

Install the package and run `cdt-solidworks`. Network startup reads runtime configuration from these environment variable names only:

- `CDT_SOLIDWORKS_BEARER_TOKEN`
- `CDT_SOLIDWORKS_AUTH_ISSUER_URL`
- `CDT_SOLIDWORKS_RESOURCE_URL`
- `CDT_SOLIDWORKS_ALLOWED_ROOTS`
- `CDT_SOLIDWORKS_BIND_HOST`
- `CDT_SOLIDWORKS_PORT`
- `CDT_SOLIDWORKS_VERSION`

Authentication configuration is mandatory and startup fails closed when it is incomplete. If allowed roots are omitted, document path operations remain disabled by policy.
