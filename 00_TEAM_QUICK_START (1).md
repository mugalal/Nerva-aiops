# NEXUS — AIOps Team Quick Start

**Project:** NEXUS — Autonomous AIOps & FinOps Platform  
**Program:** NTI — AIOps Track  
**Team:** 6 AIOps members  
**Timebox:** 14 days  
**Goal:** Ship one integrated AIOps system, not six disconnected assignments.

---

# 1. Final AIOps Team Structure

Every member owns a genuine AIOps capability.

| Member | AIOps Role | Main Question |
|---|---|---|
| **M1** | Observability & Telemetry Intelligence | What is happening in the system? |
| **M2** | ML Anomaly Detection & Incident Correlation | What behavior is abnormal, and which signals belong to one incident? |
| **M3** | AI Root Cause Analysis | Why did the incident happen? |
| **M4** | Decision Intelligence & Controlled Self-Healing | What is the safest action, and how do we execute it? |
| **M5** | Incident Memory, RAG & AIOps Copilot | What can we learn from past incidents, and how can operators query that knowledge? |
| **M6** | FinOps & Resource Optimization Intelligence | How do we reduce waste without hurting reliability? |

No member is assigned to generic backend/frontend as their primary contribution.

---

# 2. Shared Platform Work Is Distributed Across the 6 Members

There is **no separate backend/frontend member** and there is **no separate shared-platform runbook**.

The common engineering glue is split across the six AIOps members and embedded directly in their own runbooks:

| Shared task | Owner |
|---|---|
| FastAPI shared app bootstrap, state machine, approval/public incident routes | **M4** |
| Shared Pydantic contract models + contract-test fixtures | **M2** |
| Common evidence/provider adapter patterns + error/fallback conventions | **M3** |
| PostgreSQL connection/init + experiment/result persistence helpers | **M6** |
| Minimal NEXUS UI shell + incident history/Copilot presentation | **M5** |
| Common health/config/logging conventions + runtime integration checks | **M1** |

Each member still owns their AIOps module. These shared tasks are supporting engineering, not their main contribution.

AI may generate much of this boilerplate, but every owner must run, test, review, and understand the code they merge.

---

# 3. Final P0 Lifecycle

```text
M1 Telemetry
   ↓
M2 Anomaly Detection + Correlation
   ↓
M3 Root Cause Analysis
   ↓
M6 Cost/Resource Context
   ↓
M4 Decision Intelligence
   ↓
Human Approval
   ↓
M4 Controlled Remediation
   ↓
M1 Recovery Signals / Validation
   ↓
M5 Incident Memory + Copilot
```

The final NEXUS lifecycle is:

```text
DETECT
→ UNDERSTAND
→ DECIDE
→ HEAL
→ VALIDATE
→ LEARN
→ OPTIMIZE
```

---

# 4. Mandatory Live Scenarios

## Scenario A — Bad Deployment

```text
healthy payment-service:v1
→ deploy faulty v2
→ telemetry degrades
→ anomaly detected
→ incident created
→ RCA identifies faulty deployment
→ M6 supplies cost/resource context if relevant
→ M4 recommends rollback
→ human approves
→ rollback
→ M1 validates recovery
→ M5 stores incident
```

## Scenario B — Traffic Spike

```text
healthy system
→ controlled traffic spike
→ request rate / CPU / latency increase
→ anomaly detected
→ RCA identifies legitimate demand increase
→ M6 compares resource/cost options
→ M4 recommends safe scale-up
→ human approves
→ scale
→ M1 validates recovery
→ M5 stores incident
```

## Separate P0 FinOps Demonstration

```text
measured utilization
→ current resource configuration
→ cost model
→ rightsizing recommendation
→ estimated saving
→ reliability risk
```

---

# 5. The Anti-Waiting Rule

> No dependency may block productive work for more than 30 minutes.

If the real upstream module is unavailable:
1. use the canonical interface,
2. use the canonical mock,
3. continue implementation,
4. integrate the real provider at the next integration checkpoint.

Examples:

```text
M1 not ready → M2 uses mock_metrics.json
M2 not ready → M3 uses mock_anomaly_event.json
M3 not ready → M4 uses mock_rca_response.json
M6 not ready → M4 uses mock_finops_response.json
M4 not ready → M5 uses mock_action_result.json
```

---

# 6. Daily Operating Rhythm

## 09:00 — Standup
Each member answers:
```text
Yesterday
Today
Blocker
Dependency
Integration needed today
```

## 13:00 — Integration Checkpoint
Question:
> What mock can we replace with a real module today?

## 18:00 — Working Demo
Status language:
```text
works
does not work
integrated
not integrated
blocked
```

Do not use vague percentages.

---

# 7. Integration Captain Rotation

The captain does not stop their own AIOps work. They spend 1–2 hours validating integration and contracts.

| Day | Integration Captain |
|---|---|
| 1 | Team Lead + M4 |
| 2 | M1 |
| 3 | M2 |
| 4 | M3 |
| 5 | M4 |
| 6 | M5 |
| 7 | M6 |
| 8 | M1 |
| 9 | M2 |
| 10 | M3 |
| 11 | M4 |
| 12 | M5 |
| 13 | M6 |
| 14 | Team Lead + all |

---

# 8. Hard Gates

| Day | Gate |
|---|---|
| **1** | contracts, mocks, ownership, repo, shared skeleton frozen |
| **3** | mocked end-to-end lifecycle works |
| **5** | real telemetry → real anomaly → incident → real RCA |
| **7** | bad-deployment scenario works 3 consecutive times |
| **10** | both P0 scenarios + memory/Copilot + FinOps integrated |
| **11** | experiment/evaluation data collected |
| **12** | hardening only; bonus only if P0 stable |
| **13** | feature freeze + rehearsals |
| **14** | final verification |

---

# 9. Scope

## P0
- Prometheus + Grafana + Loki
- Kubernetes/deployment events
- one evaluated anomaly model + baseline
- simple incident correlation
- grounded RCA
- ROLLBACK
- SCALE
- human approval
- recovery validation
- structured incident memory
- basic grounded Copilot
- one real FinOps recommendation
- minimal NEXUS UI
- MTTD, MTTR, RCA correctness, false-positive behavior

## Bonus
Only after Day-10 P0 passes:
1. pod/resource failure + RESTART
2. pgvector semantic retrieval
3. Tempo + dependency graph
4. Terraform/AWS
5. predictive/scheduled scaling
6. automatic low-risk remediation
7. advanced UI polish

---

# 10. Definition of Done

A module is DONE only when:
- it runs,
- its contract is stable,
- it has tests,
- it is integrated or using a contract-valid mock,
- errors are handled,
- evidence/results are stored,
- another member can consume it,
- it contributes to the end-to-end lifecycle.

Local code alone is not completion.

---

# 11. What Everyone Reads

Everyone reads:
1. `00_TEAM_QUICK_START.md`
2. `01_ARCHITECTURE_CONTRACTS_AND_SHARED_PLATFORM.md`
3. their own member runbook

All shared-platform execution steps are now embedded in the relevant member runbooks. No separate shared-platform document is required.
