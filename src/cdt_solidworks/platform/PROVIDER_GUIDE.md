# CDT-SolidWorks Provider Platform

This running contract exposes only the provider-platform tools implemented in this lane.

## Tools

- `help` — read-only provider identity, version, contract fingerprint and this guide content.
- `system_status` — current provider/backend dependency status without inferring SolidWorks availability from registration.
- `system_capabilities` — machine-readable capability state with separate `implemented` and `available` facts.

## Authentication

Network mode requires a Bearer token sourced from deployment-managed runtime secrets. Missing or invalid credentials fail closed before MCP tool execution.

## Native dependency boundary

This lane does not implement or simulate SolidWorks COM behavior. Until a native runtime is explicitly integrated, SolidWorks is reported unavailable and no native capability is claimed.

## Timeout semantics

Provider timeout primitives preserve `uncertain` when an operation has already been dispatched. They do not claim that a timed-out native operation was cancelled.

## Errors and observability

Errors use structured safe classes and do not expose raw stack traces. Observability records bounded tool/outcome/latency dimensions only; credentials and model payloads are not accepted as log dimensions.
