"""Provider platform models for CDT-SolidWorks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable strict model used for externally visible platform state."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class HelpResponse(FrozenModel):
    provider_name: str
    provider_version: str
    protocol_version: str
    contract_version: str
    contract_hash: str
    updated_at: str
    authentication: str
    capabilities: tuple[str, ...]
    content: str


class DependencyState(FrozenModel):
    name: str
    available: bool
    reason: str | None = None
    version: str | None = None
    license: str | None = None


class CapabilityState(FrozenModel):
    name: str
    implemented: bool
    available: bool
    reason: str | None = None
    backend: str
    dependencies: tuple[str, ...] = ()


class RuntimeContext(FrozenModel):
    backend: str = "provider_platform"
    dependencies: tuple[DependencyState, ...] = ()
    capabilities: tuple[CapabilityState, ...] = ()


class SystemStatus(FrozenModel):
    provider: str
    provider_version: str
    backend: str
    state: Literal["ok", "degraded", "unavailable"]
    dependencies: tuple[DependencyState, ...]


class SystemCapabilities(FrozenModel):
    provider: str
    provider_version: str
    backend: str
    capabilities: tuple[CapabilityState, ...]


class SafeErrorPayload(FrozenModel):
    code: str
    message: str
    retryable: bool = False
    state: Literal["not_started", "failed", "uncertain"] | None = None


class ObservabilitySnapshot(FrozenModel):
    request_count: int
    success_count: int
    failure_count: int
    uncertain_count: int
    timeout_count: int
    reconciliation_count: int
    queue_wait_ms_total: float
    provider_latency_ms_total: float
    dependency_latency_ms_total: float
    last_tool: str | None
    last_outcome: str | None
    last_operation_id: str | None
    last_document_identity_hash: str | None
    last_failure_class: str | None
