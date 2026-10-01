# NEXUS — Architecture, Contracts & Shared Platform

This file is the common technical contract for all six AIOps members.

---

# 1. Architecture

```text
                    ┌───────────────────────┐
                    │      NEXUS UI         │
                    │  Shared Platform      │
                    └───────────┬───────────┘
                                │
                                ▼
                     Shared FastAPI Core
                                │
      ┌───────────────┬─────────┼─────────┬───────────────┐
      │               │         │         │               │
      ▼               ▼         ▼         ▼               ▼
 M1 Telemetry     M2 Anomaly   M3 RCA   M5 Memory      M6 FinOps
 Intelligence     + Correlation          + Copilot
      │               │         │         │               │
      └───────────────┴─────────┴────┬────┴───────────────┘
                                     │
                                     ▼
                               M4 Decision
                              + Guardrails
                              + Remediation
                                     │
                                     ▼
                                 Kubernetes
                                     │
                                     ▼
                          M1 Recovery Validation
                                     │
                                     ▼
                               M5 Incident Memory
```

---

# 2. Shared Platform Responsibilities — Distributed Ownership

The shared platform is not a separate team role and does not have a separate runbook.

| Shared component | Primary owner | Review/support |
|---|---|---|
| FastAPI shared bootstrap | M4 | Team Lead |
| Incident state machine | M4 | M2 + M3 |
| Public incident/approval routes | M4 | M5 |
| Shared Pydantic contracts | M2 | all consumers |
| Contract fixtures/tests | M2 | M4 |
| Provider/error/fallback conventions | M3 | M4 |
| PostgreSQL connection/init | M6 | M5 |
| Experiment/result persistence/export | M6 | M2 + M3 |
| Minimal NEXUS UI shell | M5 | M4 |
| Incident history/Copilot UI | M5 | M6 |
| Common health/config/logging conventions | M1 | all |
| Runtime integration/smoke checks | M1 | rotating Integration Captain |

Each AIOps module owner still owns their own endpoint implementation and module-specific tables/models.

---

# 3. Shared Repository

```text
nexus/
├── apps/
│   └── demo-microservices/
├── services/
│   ├── shared-nexus-api/
│   ├── telemetry-intelligence/
│   ├── anomaly-engine/
│   ├── rca-engine/
│   ├── decision-engine/
│   ├── memory-copilot/
│   └── finops-engine/
├── observability/
├── infrastructure/
├── ci/
├── contracts/
├── mocks/
├── data/
├── tests/
├── scripts/
└── docs/
```

---

# 4. Incident State Machine

```text
DETECTED
  ↓
CORRELATING
  ↓
DIAGNOSING
  ↓
DIAGNOSED
  ↓
ACTION_PROPOSED
  ↓
AWAITING_APPROVAL
  ↓
EXECUTING
  ↓
VALIDATING
  ↓
RESOLVED
```

Failure:
```text
VALIDATING
  ↓
FAILED_REMEDIATION
  ↓
ESCALATED
```

---

# 5. API Ownership

| Path | Owner |
|---|---|
| `/internal/telemetry/*` | M1 |
| `/internal/anomalies/*` | M2 |
| `/internal/correlation/*` | M2 |
| `/internal/rca/*` | M3 |
| `/internal/decisions/*` | M4 |
| `/internal/remediation/*` | M4 |
| `/internal/recovery/*` | M1 |
| `/internal/memory/*` | M5 |
| `/internal/copilot/*` | M5 |
| `/internal/finops/*` | M6 |
| `/api/incidents/*` | shared core |
| `/api/dashboard/*` | shared core |

Every module exposes:
```text
GET /health
```

---

# 6. Canonical Contracts

## Telemetry Snapshot
```json
{
  "timestamp": "2026-09-27T10:00:00Z",
  "service": "payment-service",
  "version": "v2",
  "metrics": {
    "cpu": 0.72,
    "memory": 0.61,
    "request_rate": 180,
    "latency_p95_ms": 820,
    "http_5xx_rate": 0.14,
    "replica_count": 3
  }
}
```

## Deployment Event
```json
{
  "event_id": "DEP-001",
  "service": "payment-service",
  "old_version": "v1",
  "new_version": "v2",
  "commit_sha": "abc123",
  "pipeline_id": "jenkins-42",
  "timestamp": "2026-09-27T10:00:00Z",
  "status": "SUCCESS"
}
```

## Anomaly Event
```json
{
  "anomaly_id": "ANO-001",
  "timestamp": "2026-09-27T10:00:42Z",
  "service": "payment-service",
  "score": 0.94,
  "severity": "high",
  "model": "isolation_forest",
  "features": {
    "request_rate": 180,
    "latency_p95_ms": 820,
    "http_5xx_rate": 0.14,
    "cpu": 0.72
  }
}
```

## Incident
```json
{
  "incident_id": "INC-001",
  "started_at": "2026-09-27T10:00:42Z",
  "status": "DETECTED",
  "severity": "high",
  "affected_services": ["payment-service"],
  "anomaly_ids": ["ANO-001"]
}
```

## RCA Result
```json
{
  "incident_id": "INC-001",
  "root_cause": "faulty_deployment",
  "affected_component": "payment-service:v2",
  "confidence": 0.92,
  "evidence": [
    "payment-service:v2 deployed 42 seconds before anomaly",
    "P95 latency increased after deployment",
    "HTTP 5xx increased",
    "traffic increase was insufficient to explain degradation"
  ]
}
```

## FinOps Context
```json
{
  "service": "payment-service",
  "current_replicas": 3,
  "current_cpu_request_m": 1000,
  "observed_cpu_pct": 72,
  "temporary_scale_options": [
    {"replicas": 4, "estimated_cost_delta": 0.30, "risk": "MEDIUM"},
    {"replicas": 6, "estimated_cost_delta": 0.75, "risk": "LOW"}
  ]
}
```

## Decision Proposal
```json
{
  "incident_id": "INC-001",
  "recommended_action": "ROLLBACK",
  "target": "payment-service",
  "parameters": {
    "from_version": "v2",
    "to_version": "v1"
  },
  "confidence": 0.92,
  "risk": "MEDIUM",
  "reason": "degradation began immediately after deployment",
  "approval_required": true
}
```

## Action Result
```json
{
  "action_id": "ACT-001",
  "incident_id": "INC-001",
  "action": "ROLLBACK",
  "status": "SUCCESS",
  "started_at": "2026-09-27T10:01:00Z",
  "completed_at": "2026-09-27T10:01:18Z"
}
```

## Recovery Result
```json
{
  "incident_id": "INC-001",
  "recovered": true,
  "before": {
    "latency_p95_ms": 820,
    "http_5xx_rate": 0.14
  },
  "after": {
    "latency_p95_ms": 145,
    "http_5xx_rate": 0.004
  },
  "recovery_time_seconds": 47,
  "slo_restored": true
}
```

## Incident Memory
```json
{
  "incident_id": "INC-001",
  "incident_type": "faulty_deployment",
  "service": "payment-service",
  "root_cause": "faulty_deployment",
  "action": "ROLLBACK",
  "action_success": true,
  "recovered": true,
  "tags": ["deployment", "latency", "5xx"]
}
```

## FinOps Recommendation
```json
{
  "service": "payment-service",
  "current": {
    "replicas": 3,
    "cpu_request_m": 1000,
    "memory_request_mb": 2048
  },
  "observed": {
    "avg_cpu_pct": 18,
    "avg_memory_pct": 31
  },
  "recommended": {
    "replicas": 2,
    "cpu_request_m": 600,
    "memory_request_mb": 1024
  },
  "estimated_monthly_saving_pct": 28.0,
  "reliability_risk": "LOW"
}
```

---

# 7. Required Mocks

```text
mock_metrics.json
mock_deployment_event.json
mock_anomaly_event.json
mock_incident.json
mock_rca_response.json
mock_finops_context.json
mock_decision.json
mock_action_result.json
mock_recovery.json
mock_incident_memory.json
```

Mocks must use the same schema as real responses.

---

# 8. Module Integration Map

```text
M1 → M2
Telemetry/features

M2 → M3
Anomaly + incident context

M1 + deployment events → M3
Evidence

M3 → M4
RCA

M6 → M4
Cost/resource options

M4 → Kubernetes
Approved action

M4 → M1
Action completion / validation request

M1 → Shared core
Recovery result

Shared core → M5
Resolved incident

M5 → Shared core/UI
Memory + Copilot response
```

---

# 9. Integration Tests

Each provider must have:
1. health test,
2. schema-valid request,
3. schema-invalid request,
4. valid response contract test,
5. timeout/error behavior,
6. one real consumer integration test.

---

# 10. Guardrail Architecture

Never:
```text
LLM → shell
LLM → kubectl
```

Always:
```text
RCA / LLM explanation
→ structured RCAResult
→ M6 FinOps context if needed
→ M4 deterministic Decision Engine
→ policy validation
→ human approval
→ allowlisted executor
→ M1 recovery validation
```

---

# 11. Integration Order

## Day 1–3
Mocks:
```text
mock telemetry
→ mock anomaly
→ mock RCA
→ mock FinOps context
→ mock decision
→ mock action
→ mock recovery
→ mock memory
```

## Day 3–5
Replace:
```text
M1 real telemetry
→ M2 real anomaly
→ M3 real RCA
```

## Day 6–7
Replace:
```text
M4 real decision/remediation
→ M1 real recovery validation
```

## Day 8–10
Add:
```text
M6 real cost/resource intelligence
M5 real memory/Copilot
```

---

# 12. Definition of Shared Platform Done

Shared platform is complete when:
- state machine works,
- modules communicate via contracts,
- mock/real providers are swappable by configuration,
- UI shows real incident lifecycle,
- approval is visible,
- recovery state is visible,
- no manual JSON copying is needed in the final P0 demo.
