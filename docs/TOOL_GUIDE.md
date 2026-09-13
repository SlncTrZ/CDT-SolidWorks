# CDT-SolidWorks Tool Guide

> Status: integrated W0 provider surface; native Windows acceptance pending · Updated: 2026-09-13

## Current callable surface

The integrated provider now exposes `help`, `system_status`, `system_capabilities`, application probe/connect/disconnect, explicit document lifecycle, bounded feature/body/component queries, rebuild verification, and uncertain-state reconciliation.

Network mode remains fail-closed behind Bearer authentication. Document access remains fail-closed until allowed filesystem roots are configured.

## Capability honesty

The merged part/sketch, assembly, configuration, drawing, and export lanes are not advertised as provider-level implemented capabilities yet because their native SolidWorks adapters are not connected. Domain/mock test success is not treated as native support.

## Correctness rule

A mutation is successful only after required SolidWorks postconditions are read back. Parametric feature success additionally requires a clean SolidWorks rebuild/error state. In-flight timeout is `uncertain`, not ordinary failure or cancellation, until reconciliation proves final state.

## Verification status

Provider and domain logic are covered by automated integration tests on the Linux gateway. Native SolidWorks part/assembly/drawing/export claims remain blocked until verified on a supported Windows SolidWorks installation with save/reopen and negative/recovery evidence.


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
