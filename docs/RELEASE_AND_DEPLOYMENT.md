# CDT-SolidWorks — Release and deployment

**CDT package:** `0.1.1` · tag `v.0.1.1` · **separate** API contract `0.1.0`. Native acceptance targets **SOLIDWORKS 2024 SP0.1 on Windows** with the required STA/COM session.

[GitHub release](https://github.com/SlncTrZ/CDT-SolidWorks/releases) availability alone does not prove that a native COM deployment is installed or working.

## Install and connect

1. From a trusted stable checkout and supported Windows environment, run `python -m pip install .` in an isolated environment, then start `cdt-solidworks` using the documented [runtime guide](TOOL_GUIDE.md). No general CAD automation is permitted without verified STA ownership.
2. Configure managed bearer authentication, topology-reference signing secret, issuer/resource URL and explicit allowed file roots. Never publish credentials or put them in release artifacts.
3. Before native work, call `help`, `system_status`, `system_capabilities` and `application_probe`. Check source/runtime contract, signing/token validation and the actual Windows SOLIDWORKS build. A healthy service does not prove a drawing or part has passed rebuild/verification.

## Upgrade and rollback

Use an immutable side-by-side provider install and retain the previous profile, signing/auth material and bridge identity. Execute only safe scratch-document tests, independently verify rebuild state and object readback, and reconcile timeouts before replay. If native, signing or OAuth auth fails, restore the previous managed provider registration; do not overwrite or automatically close a user's dirty document.

[Tool guide](TOOL_GUIDE.md) · [Specification baseline](SPEC_BASELINE.md) · [Contributor tests](../tests/README.md).
