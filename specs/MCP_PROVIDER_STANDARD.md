# SlncTrZ-MCP Provider Standard

> Status: Draft v0.2
> Scope: SlncTrZ provider-integration convention for MCP servers connected through SlncTrZ-MCP

## 1. Purpose

This document defines the common contract for MCP providers used inside the SlncTrZ ecosystem.

The goal is to make every provider predictable to clients, easy to integrate with SlncTrZ-MCP, secure by default, and self-describing.

CyberBrain is intended to be the first reference implementation of this standard. The standard itself belongs to the gateway ecosystem, not to any individual provider.

---

## 2. Core Principles

1. **MCP first** — provider capabilities are exposed through standard MCP tools rather than ad-hoc per-tool HTTP APIs.
2. **Authenticated by default** — network-accessible providers fail closed when credentials are missing or invalid.
3. **Self-describing by SlncTrZ convention** — providers targeting first-class SlncTrZ integration expose a mandatory `help` tool. This is a SlncTrZ provider requirement, not a requirement of the MCP protocol itself.
4. **Provider owns business logic** — SlncTrZ-MCP owns routing, namespace, policy, provider lifecycle, and catalog composition.
5. **Stable contracts** — transport, tool schemas, errors, and versions must evolve deliberately.
6. **No secret leakage** — credentials never appear in URLs, prompts, logs, tool results, documentation payloads, or source-controlled configuration.
7. **Deterministic infrastructure logic** — routing, retries, authorization, validation, and protocol handling must not rely on LLM judgment.
8. **Fail loud, fail closed** — unknown or invalid state must surface clearly and must not silently broaden authority.

---

## 3. Transport Standard

### 3.1 Required transport

Providers SHOULD expose MCP using Streamable HTTP.

Default endpoint:

```text
/mcp
```

A provider MAY expose additional REST endpoints for non-MCP integrations, health probes, administration, or internal applications, but those endpoints do not replace the MCP contract.

### 3.2 Network behavior

The provider MUST:

- bind only to the intended interface/port for its deployment
- support reverse-proxy/tunnel deployment
- return explicit HTTP errors for invalid transport/authentication state
- avoid leaking internal stack traces or credentials in error responses

### 3.3 Health

Providers SHOULD expose a lightweight health mechanism suitable for Docker/systemd/orchestrator checks.

Health must test provider liveness without requiring execution of destructive business operations.

---

## 4. Authentication Standard

### 4.1 Primary authentication

Primary convention:

```text
Authorization: Bearer <token>
```

### 4.2 Optional compatibility authentication

A provider MAY additionally accept:

```text
X-API-Key: <token>
```

When multiple authentication forms are supported, they MUST resolve through one internal authorization layer rather than separate business logic paths.

### 4.3 Credential source

Credentials MUST come from deployment-managed secret sources such as:

- environment variables
- secret managers
- protected runtime configuration

Credentials MUST NOT be committed to the repository.

### 4.4 Prohibited credential handling

Never place credentials in:

```text
URL path
query parameters
command arguments when avoidable
prompts
MCP tool arguments
MCP tool results
logs
documentation payloads
Git-tracked config
```

### 4.5 Failure semantics

Recommended behavior:

```text
401 Unauthorized
```

for missing or invalid authentication.

Use:

```text
403 Forbidden
```

when identity/authentication is accepted but the requested operation is not authorized.

Authentication and authorization MUST fail closed.

---

## 5. Provider Identity and Namespace

Each provider has a stable provider ID.

Examples:

```text
cyberbrain
v2t
vmk
research
```

The provider itself may expose bare MCP tool names internally:

```text
help
knowledge_search
knowledge_store
```

When a provider is discovered/accepted into the gateway catalog, SlncTrZ-MCP canonicalizes bare provider tool names into:

```text
<provider>.<tool>
```

Examples:

```text
cyberbrain.help
cyberbrain.knowledge_search
v2t.help
v2t.transcribe
```

Provider IDs and tool names SHOULD use lowercase ASCII identifiers with underscores where necessary.

Canonical namespace ownership belongs to SlncTrZ-MCP. Providers SHOULD advertise bare MCP tool names unless they have an explicit compatibility reason to pre-namespace them; the gateway namespacing path is idempotent and preserves an already-canonical `<provider>.<tool>` identifier.

---

## 6. Mandatory `.help` Tool for First-Class SlncTrZ Providers

Every provider that claims first-class compliance with this SlncTrZ provider standard MUST expose a zero-side-effect help tool. Generic third-party MCP servers can still be connected without implementing this convention when the owner explicitly accepts their discovered tool set.

Bare provider tool:

```text
help
```

Gateway canonical form:

```text
<provider>.help
```

Examples:

```text
cyberbrain.help
v2t.help
vmk.help
```

### 6.1 Purpose

`.help` allows any AI/client to discover the provider-specific operating contract without relying on account memory, stale prompts, or external documentation.

It is a read-only tool and MUST NOT mutate provider state.

### 6.2 Minimum response fields

The result SHOULD provide at least:

```text
provider_name
provider_version
protocol_version
contract_version
contract_hash
updated_at
authentication
capabilities
content
```

Where:

- `provider_name` — stable provider identity
- `provider_version` — running provider/software version
- `protocol_version` — MCP protocol version/compatibility declaration
- `contract_version` — version of the provider help/tool contract
- `contract_hash` — deterministic fingerprint of the current contract content
- `updated_at` — contract update timestamp when available
- `authentication` — safe description of supported authentication methods, never credentials
- `capabilities` — concise provider capability summary
- `content` — complete current provider usage guide

### 6.3 Source of truth

The help content SHOULD come from a runtime-readable guide/specification file.

Recommended pattern:

```text
host/provider guide
      ↓ read-only mount
container/runtime
      ↓
help tool
```

Do not duplicate the complete guide as a hard-coded application string unless there is a compelling deployment reason.

A mounted guide SHOULD be read-only.

This allows guide changes without rebuilding the provider image.

### 6.4 Contract hash

`contract_hash` SHOULD be a deterministic cryptographic hash such as SHA-256 over the canonical help content.

This enables clients/gateways to detect contract changes and avoid stale caching.

---

## 7. Tool Contract Rules

Each tool MUST have:

- a stable name
- explicit description
- explicit input schema
- explicit output/error behavior
- no hidden privilege escalation

Tool names SHOULD describe operations rather than UI actions.

Examples:

```text
knowledge_search
knowledge_store
transcribe
render_status
```

Avoid ambiguous names such as:

```text
do
run
process
misc
```

unless the provider domain makes their meaning unambiguous.

### 7.1 Read vs write

Providers SHOULD make read/write semantics obvious from tool descriptions and naming.

Write tools MUST document persistence and side effects.

Destructive operations SHOULD be separated from ordinary write/update operations.

### 7.2 Validation

Tool input MUST be validated before business logic executes.

Unknown/invalid fields SHOULD be rejected when permissive handling could hide client errors or create unsafe behavior.

---

## 8. Error Model

Errors SHOULD be structured, predictable, and safe.

At minimum distinguish:

```text
authentication_error
authorization_error
validation_error
not_found
conflict
rate_limited
timeout
provider_unavailable
internal_error
```

Do not expose:

- credentials
- secret environment values
- full stack traces to untrusted callers
- raw internal configuration

Errors SHOULD tell the client what class of failure occurred and whether retrying is reasonable.

---

## 9. Versioning

A provider SHOULD separately version:

```text
provider software
provider/tool contract
schema/data model when applicable
```

Do not assume software version and tool contract version are the same concept.

Breaking changes require an explicit compatibility decision.

When practical, prefer additive tool/schema evolution over silent breaking mutation.

`.help` MUST reflect the currently running contract, not the source tree's intended future contract.

---

## 10. Provider / Gateway Responsibility Boundary

### Provider owns

- business/domain logic
- provider-specific validation
- provider-specific tool schemas
- persistence behavior
- provider-specific documentation/help
- internal retries required by its domain
- provider-local health logic

### SlncTrZ-MCP owns

- provider registration/configuration
- canonical `<provider>.<tool>` namespace
- provider readiness/catalog state
- workspace/policy authorization
- routing to providers
- gateway-level audit
- provider lifecycle visibility
- catalog Fingerprinting
- client-facing aggregation

The gateway SHOULD NOT absorb provider business logic merely to make integration easier.

The provider SHOULD NOT attempt to bypass gateway policy or self-expand gateway authority.

---

## 11. Logging and Observability

Providers SHOULD emit enough observability to diagnose failures without exposing sensitive content.

Recommended metrics/log dimensions:

```text
request count
tool name
success/failure
latency
timeout count
validation failures
auth failures
provider dependency latency
```

Never log raw credentials.

Sensitive tool arguments/results SHOULD be redacted or omitted according to provider policy.

---

## 12. Timeouts and Reliability

Providers SHOULD define bounded timeouts for external dependencies and long-running work.

The provider must not hang indefinitely waiting for:

- model APIs
- databases
- storage
- remote services

Retry behavior SHOULD be deterministic and bounded.

The gateway and provider may have separate timeout layers; provider-local timeout behavior should remain explicit.

---

## 13. Docker / Deployment Expectations

Containerized providers SHOULD:

- keep mutable data outside the image
- avoid embedding secrets into image layers
- provide reproducible builds
- expose explicit ports
- define restart behavior
- provide healthchecks where practical
- support runtime configuration through safe environment/config mechanisms

Provider documentation SHOULD identify required dependencies and volumes.

---

## 14. Security Baseline

Every provider MUST:

- validate inputs
- fail closed on invalid auth
- avoid secret leakage
- avoid implicit privilege expansion
- keep credentials out of source control
- reject unsafe or malformed requests before side effects
- separate administrative configuration from ordinary tool calls

Administrative/provider configuration SHOULD remain owner-managed unless a dedicated secured management interface explicitly exists.

---

## 15. Reference Provider Shape

Recommended repository/runtime structure:

```text
provider/
├── server/
├── tools/
├── auth/
├── config/
├── docs/
│   └── TOOL_GUIDE.md
├── tests/
├── Dockerfile
└── README.md
```

Logical runtime flow:

```text
MCP request
   ↓
authentication
   ↓
input validation
   ↓
tool dispatch
   ↓
business logic
   ↓
structured result/error
```

`.help` reads the provider's current runtime contract and returns it without mutation.

---

## 16. SlncTrZ-MCP Integration Checklist

A provider is ready for integration when:

- [ ] Streamable HTTP MCP endpoint is available
- [ ] `/mcp` is the documented default endpoint or an explicit exception is documented
- [ ] Bearer authentication is implemented for network-accessible deployments
- [ ] credentials are externalized from source/image
- [ ] all tool schemas are explicit
- [ ] `help` exists and is read-only for providers claiming first-class SlncTrZ provider-standard compliance
- [ ] help content is current and versioned/fingerprinted
- [ ] provider/tool IDs are stable
- [ ] error behavior is documented
- [ ] health behavior is defined
- [ ] timeouts are bounded
- [ ] logs do not expose credentials
- [ ] gateway canonical namespace is `<provider>.<tool>`
- [ ] provider business logic remains outside SlncTrZ-MCP
- [ ] integration tests verify discovery and at least one safe tool call

---

## 17. Reference Implementation

CyberBrain should implement this standard first and act as the practical validation target.

The current MeiLin MCP deployment is the behavioral starting point because its Streamable HTTP + authenticated MCP integration has already proven compatible with SlncTrZ-MCP.

The goal is not to preserve MeiLin-specific naming. The goal is to preserve the good provider mechanics and generalize them into a reusable standard for every future MCP provider in the system.
