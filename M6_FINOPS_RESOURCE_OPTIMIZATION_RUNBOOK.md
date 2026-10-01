# M6 — FinOps & Resource Optimization Intelligence Runbook

**AIOps question:** How can NEXUS optimize infrastructure cost and capacity without sacrificing reliability?  
**Timebox:** 14 days

## P0 Ownership

- resource utilization analysis
- transparent cost model
- rightsizing
- cost-aware scaling options
- reliability-risk annotation
- FinOps evaluation
- optional forecasting/predictive scaling bonus

## Core Integration

Consumes M1 utilization/replica data and M2/M3 incident context. Produces FinOpsRecommendation and scale-option cost/risk context for M4.

## Secondary / Backup

Backup: M1 for resource metrics. You are backup for M4 cost-aware decision logic.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

You own the shared persistence foundation because FinOps and evaluation depend heavily on reliable stored measurements.

Shared responsibilities:

- PostgreSQL connection/configuration,
- initialization/migration strategy,
- common repository helpers,
- experiment/result persistence,
- CSV/JSON export for final evaluation.

Minimum shared tables:
```text
incidents
anomalies
rca_results
decision_proposals
approvals
actions
recovery_results
deployment_events
incident_memory
finops_recommendations
timeline_events
experiment_runs
```

You do **not** own every module's business logic. Module owners define their fields/records; you provide the persistence foundation.

Create:
```text
services/shared-nexus-api/app/db/
scripts/init-db.*
scripts/export-experiments.*
```

Required outputs:
- shared DB starts from documented configuration,
- experiment timestamps/results can be exported without manual copying,
- MTTD/MTTR can be computed from stored timestamps.

Done when:
- all P0 lifecycle records persist,
- shared core can restart without losing required demo history,
- final evaluation data is exportable to CSV/JSON.


### Shared Step A — Create PostgreSQL foundation
Create shared database connection/config and initialization.

Minimum tables:
```text
incidents
anomalies
rca_results
decision_proposals
approvals
actions
recovery_results
deployment_events
incident_memory
finops_recommendations
timeline_events
experiment_runs
```

Use AI for boilerplate, but review schemas with each module owner.

**Test:** clean DB initializes successfully.

# Day 1 — FinOps Contract

Define P0 inputs:
```text
CPU request
memory request
replica count
runtime
observed CPU
observed memory
traffic pattern
scale duration
```

Define outputs:
```text
current configuration
proposed configuration
estimated cost
estimated savings
reliability risk
assumptions
```

Build:
```text
GET  /health
POST /internal/finops/recommend
POST /internal/finops/scale-options
```

Use mock utilization first.

# Day 2 — Cost Model

Build a transparent formula.

Do not invent provider pricing.

For local demo:
- use explicit documented cost units or a chosen provider pricing assumption
- label it clearly

Same inputs must produce same result.

# Day 3 — Mock Rightsizing

Given mock:
```text
replicas=3
CPU request=1000m
avg CPU=18%
memory request=2GB
avg memory=31%
```

Produce conservative recommendation.

Include:
- expected saving
- reliability risk
- reason

### Shared Step B — Add common repository/persistence helpers
Module owners provide records; shared helpers persist them consistently.

**Test:** create/read one record for incident, anomaly, RCA, action, recovery, memory, FinOps.

# Day 4 — Real M1 Utilization

Consume real measurement window.

Record:
```text
window
average
peak
resource requests
replicas
```

# Day 5 — Rightsizing P0

Produce one real recommendation for payment-service.

Do not automatically apply it.

Validate recommendation against observed peaks.

# Day 6 — Scale Option Cost Context

For traffic incident generate options such as:
```text
3→4 replicas
3→6 replicas
```

For each:
```text
temporary estimated cost delta
expected capacity margin
reliability risk
```

M4 consumes this.

# Day 7 — Flagship Support

Bad deployment may have minimal direct FinOps role.
Do not force cost analysis where it adds no value.

Provide resource context only if useful.

# Day 8 — Traffic Scenario

This is your main incident integration.

Flow:
```text
M1 utilization/load
→ M6 cost/resource options
→ M4 decision engine
```

Test:
- lower-cost option may have higher reliability risk
- recommendation context is explainable

# Day 9 — FinOps UI Data

Shared platform shows:
```text
current
observed
recommended
saving
risk
assumption
```

# Day 10 — P0 Gate

Required:
1. one measured rightsizing recommendation
2. traffic scale-option cost context used by M4

### Shared Step C — Build experiment export
Create:
```text
scripts/export-experiments
```
Export CSV/JSON with:
```text
scenario
run_id
injection_time
detection_time
RCA correctness
action time
recovery time
MTTD
MTTR
cost/SLO effect
```

No manual typing of final metrics.

# Day 11 — Evaluation

Document:
```text
resource baseline
measurement window
formula
assumptions
proposed resources
estimated saving
reliability caveat
temporary scaling cost
```

# Day 12 — Hardening

Handle:
- insufficient measurement window
- missing utilization
- extreme peaks
- recommendation that violates safety floor

Return:
```text
no recommendation / insufficient evidence
```
instead of aggressive downsizing.

# Day 13 — Rehearsal

Explain:
- FinOps is not "choose cheapest"
- reliability/performance/risk matter
- how M6 context changes M4 scaling decision

# Day 14 — Final Verification

P0 done when:
- real M1 data consumed
- one explainable rightsizing recommendation exists
- cost assumptions documented
- M4 consumes scale-option context
- no fake savings shown

# Bonus

- forecasting
- scheduled scaling
- predictive scaling
- AWS real pricing integration
- cost anomaly detection
