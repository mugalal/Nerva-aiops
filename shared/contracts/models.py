"""
Pydantic models for the 11 frozen contracts in /contracts.

Owned by M2 (see contracts/README.md). Rules this file follows:

* The frozen JSON in /contracts is the source of truth. A model must accept
  every file in /contracts and /mocks, and must reject anything with extra
  fields ("contract wins, no extra fields").
* A model only enforces what the contract actually pins down. The contracts
  show example values, not enumerations, so fields like `severity`, `risk`,
  `status` (except Incident.status) and `recommended_action` are plain `str`.
  Making them Literal would reject values other modules may legitimately
  need (e.g. severity "critical", action "SCALE_UP"). Tighten a field only
  by agreement with the team lead, as a contract version bump.
* The one real enumeration in the architecture doc is the incident state
  machine, so Incident.status is a Literal of exactly those 11 states.

Two checks go slightly beyond the frozen JSON, both on probability-like
fields: `score` and `confidence` must be within 0..1. Every example value is
in that range; if another module needs otherwise, raise it with the lead.

Do not change a model without updating /contracts and /mocks in the same
commit, and getting team-lead + M2 approval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base for every contract: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# M1 -> M2 : telemetry
# ---------------------------------------------------------------------------

class Metrics(ContractModel):
    cpu: float
    memory: float
    request_rate: float
    latency_p95_ms: float
    http_5xx_rate: float
    replica_count: int


class TelemetrySnapshot(ContractModel):
    timestamp: datetime
    service: str
    version: str
    metrics: Metrics


class DeploymentEvent(ContractModel):
    event_id: str
    service: str
    old_version: str
    new_version: str
    commit_sha: str
    pipeline_id: str
    timestamp: datetime
    status: str


# ---------------------------------------------------------------------------
# M2 -> M3 : anomaly + incident
# ---------------------------------------------------------------------------

class AnomalyFeatures(ContractModel):
    """The evidence carried on an AnomalyEvent.

    The frozen contract carries exactly these four. M2's internal feature
    vector is larger (see anomaly-engine/app/features.py); only this subset
    crosses the module boundary until the contract is versioned.
    """

    request_rate: float
    latency_p95_ms: float
    http_5xx_rate: float
    cpu: float


class AnomalyEvent(ContractModel):
    anomaly_id: str
    timestamp: datetime
    service: str
    score: float = Field(ge=0.0, le=1.0)
    severity: str
    model: str
    features: AnomalyFeatures


IncidentStatus = Literal[
    "DETECTED",
    "CORRELATING",
    "DIAGNOSING",
    "DIAGNOSED",
    "ACTION_PROPOSED",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VALIDATING",
    "RESOLVED",
    "FAILED_REMEDIATION",
    "ESCALATED",
]


class Incident(ContractModel):
    incident_id: str
    started_at: datetime
    status: IncidentStatus
    severity: str
    affected_services: list[str]
    anomaly_ids: list[str]


# ---------------------------------------------------------------------------
# M3 : root cause
# ---------------------------------------------------------------------------

class RCAResult(ContractModel):
    incident_id: str
    root_cause: str
    affected_component: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]


# ---------------------------------------------------------------------------
# M6 -> M4 : FinOps
# ---------------------------------------------------------------------------

class ScaleOption(ContractModel):
    replicas: int
    estimated_cost_delta: float
    risk: str


class FinOpsContext(ContractModel):
    service: str
    current_replicas: int
    current_cpu_request_m: int
    observed_cpu_pct: float
    temporary_scale_options: list[ScaleOption]


class ResourceSpec(ContractModel):
    replicas: int
    cpu_request_m: int
    memory_request_mb: int


class ObservedUsage(ContractModel):
    avg_cpu_pct: float
    avg_memory_pct: float


class FinOpsRecommendation(ContractModel):
    service: str
    current: ResourceSpec
    observed: ObservedUsage
    recommended: ResourceSpec
    estimated_monthly_saving_pct: float
    reliability_risk: str


# ---------------------------------------------------------------------------
# M4 : decision, action, recovery
# ---------------------------------------------------------------------------

class DecisionProposal(ContractModel):
    incident_id: str
    recommended_action: str
    target: str
    # What goes in `parameters` depends on the action (ROLLBACK uses
    # from_version/to_version; a scale action would use other keys), so the
    # contract's single example can't pin its shape. Kept open on purpose.
    parameters: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    risk: str
    reason: str
    approval_required: bool


class ActionResult(ContractModel):
    action_id: str
    incident_id: str
    action: str
    status: str
    started_at: datetime
    completed_at: datetime


class RecoveryMetrics(ContractModel):
    latency_p95_ms: float
    http_5xx_rate: float


class RecoveryResult(ContractModel):
    incident_id: str
    recovered: bool
    before: RecoveryMetrics
    after: RecoveryMetrics
    recovery_time_seconds: float
    slo_restored: bool


# ---------------------------------------------------------------------------
# M5 : memory
# ---------------------------------------------------------------------------

class IncidentMemory(ContractModel):
    incident_id: str
    incident_type: str
    service: str
    root_cause: str
    action: str
    action_success: bool
    recovered: bool
    tags: list[str]
