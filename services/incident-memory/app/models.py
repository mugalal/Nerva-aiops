"""M5 envelopes preserve the frozen eight-field IncidentMemory contract."""
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MemoryRecord(Model):
    incident_id: str = Field(min_length=1, max_length=120)
    incident_type: str = Field(min_length=1, max_length=120)
    service: str = Field(min_length=1, max_length=120)
    root_cause: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=120)
    action_success: StrictBool
    recovered: StrictBool
    tags: list[str] = Field(default_factory=list, max_length=100)


class Context(Model):
    # Original upstream records are retained, rather than reduced to summaries.
    incident: dict[str, Any] | None = None
    anomaly: dict[str, Any] | None = None
    rca: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    action_result: dict[str, Any] | None = None
    recovery_result: dict[str, Any] | None = None
    finops_context: dict[str, Any] | None = None
    deployment_event: dict[str, Any] | None = None
    mttd_seconds: float | None = Field(default=None, ge=0)
    mttr_seconds: float | None = Field(default=None, ge=0)
    cost_impact: dict[str, Any] | None = None
    slo_impact: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_upstream_shapes(self):
        root = Path(__file__).resolve().parents[3] / "contracts"
        for name, filename in {"incident": "incident", "anomaly": "anomaly_event", "rca": "rca_result",
                               "decision": "decision_proposal", "action_result": "action_result",
                               "recovery_result": "recovery_result", "finops_context": "finops_context",
                               "deployment_event": "deployment_event"}.items():
            value = getattr(self, name)
            if value is None:
                continue
            example = json.loads((root / f"{filename}.json").read_text(encoding="utf-8"))
            if set(value) != set(example):
                raise ValueError(f"context.{name} must match the frozen {filename} fields")
            for field, sample in example.items():
                actual = value[field]
                valid = (type(actual) is bool if type(sample) is bool else
                         type(actual) in (int, float) and math.isfinite(actual) if type(sample) in (int, float) else
                         isinstance(actual, type(sample)))
                if not valid:
                    raise ValueError(f"context.{name}.{field} has an invalid type")
            if name == "rca" and any(not isinstance(item, str) for item in value["evidence"]):
                raise ValueError("RCA evidence must be strings")
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class StoreRequest(Model):
    memory: MemoryRecord
    context: Context = Field(default_factory=Context)
    source: Literal["real", "mock"]
    resolved: StrictBool

    @model_validator(mode="after")
    def consistent_records(self):
        m, c = self.memory, self.context
        if not self.resolved and (not c.incident or c.incident.get("status") != "ESCALATED"):
            raise ValueError("Only resolved or escalated incidents can enter incident memory")
        for name in ("incident", "rca", "decision", "action_result", "recovery_result"):
            record = getattr(c, name)
            if record is not None and record.get("incident_id") != m.incident_id:
                raise ValueError(f"{name}.incident_id must match memory.incident_id")
        if c.incident:
            if c.incident.get("status") not in {"RESOLVED", "ESCALATED"}:
                raise ValueError("context.incident must be RESOLVED or ESCALATED")
            if m.service not in c.incident.get("affected_services", []):
                raise ValueError("memory.service must belong to affected_services")
        if c.anomaly:
            if c.anomaly.get("service") != m.service:
                raise ValueError("anomaly.service must match memory.service")
            if c.incident and c.anomaly.get("anomaly_id") not in c.incident.get("anomaly_ids", []):
                raise ValueError("anomaly must belong to the incident")
        if c.rca and c.rca.get("root_cause") != m.root_cause:
            raise ValueError("RCA root cause must match memory")
        if c.action_result:
            if c.action_result.get("action") != m.action:
                raise ValueError("Action must match memory")
            if c.action_result.get("status") not in {"SUCCESS", "FAILED", "FAILURE", "ERROR", "REJECTED", "CANCELLED"}:
                raise ValueError("Action result must have a terminal status")
            if (c.action_result["status"] == "SUCCESS") != m.action_success:
                raise ValueError("Action success must match recorded status")
        if c.recovery_result:
            if type(c.recovery_result.get("recovered")) is not bool:
                raise ValueError("Recovery requires an explicit boolean")
            if c.recovery_result.get("status") == "unknown":
                raise ValueError("Unknown recovery cannot be stored as a verified outcome")
            if c.recovery_result["recovered"] != m.recovered:
                raise ValueError("Recovery must match memory")
        for name in ("finops_context", "deployment_event"):
            record = getattr(c, name)
            if record and record.get("service") != m.service:
                raise ValueError(f"{name}.service must match memory.service")
        return self


class SearchRequest(Model):
    incident_type: str | None = Field(default=None, min_length=1)
    service: str | None = Field(default=None, min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=100)
    features: list[str] = Field(default_factory=list, max_length=100)
    exclude_incident_id: str | None = None
    source: Literal["real", "mock"] = "real"
    limit: int = Field(default=5, ge=1, le=50)

    @model_validator(mode="after")
    def has_criteria(self):
        if not (self.incident_type or self.service or self.tags or self.features):
            raise ValueError("At least one search criterion is required")
        return self


class CopilotRequest(Model):
    question: str = Field(min_length=1, max_length=2000)
    incident_id: str | None = None
    source: Literal["real", "mock"] = "real"
    search: SearchRequest | None = None


class ApprovalRequest(Model):
    decision: Literal["approve", "reject"]


class ChatMessage(Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(CopilotRequest):
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class ActionConfirmation(Model):
    confirmed: Literal[True]
