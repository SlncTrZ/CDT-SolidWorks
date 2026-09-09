# CDT-SolidWorks Tool Guide

> Status: skeleton only · No running provider/tool surface yet · Updated: 2026-09-09

This file is reserved as the runtime help source. At this checkpoint there is no callable MCP server and no SolidWorks capability is claimed supported.

## First planned public surface

W0 targets `help`, `system_status`, `system_capabilities`, application/version/license availability, document lifecycle and basic feature/body/component query. W1 adds sketch/parametric part workflows only after native verification. These are roadmap targets, not current runtime claims.

## Correctness rule

A parametric mutation is successful only when SolidWorks reports a clean rebuild/error state appropriate to the operation.
