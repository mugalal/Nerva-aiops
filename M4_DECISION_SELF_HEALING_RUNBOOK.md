# M4 — Decision Intelligence & Controlled Self-Healing Runbook

**AIOps question:** Given a diagnosed incident, what is the safest operational action and how do we execute it under guardrails?  
**Timebox:** 14 days

## P0 Ownership

- decision engine
- action comparison
- risk/policy model
- human approval
- ROLLBACK
- SCALE
- audit trail
- Kubernetes/Jenkins remediation
- shared orchestration coordination

## Core Integration

Consumes M3 RCA + M6 cost/resource context + M1 runtime context. Produces DecisionProposal and ActionResult. Requests M1 recovery validation.

## Secondary / Backup

Backup: M1 for Kubernetes execution. You coordinate shared state-machine integration.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

You are the **shared orchestration coordinator** because Decision/Self-Healing sits at the center of the lifecycle.

You own:

- FastAPI shared application bootstrap,
- incident state machine,
- public incident routes,
- approval/reject routes,
- provider wiring,
- end-to-end orchestration tests.

Create:
```text
services/shared-nexus-api/
  app/
    main.py
    api/incidents.py
    api/approvals.py
    state_machine/
    providers/
    config/
  tests/e2e/
```

Required public routes:
```text
GET  /health
POST /api/incidents
GET  /api/incidents
GET  /api/incidents/{id}
POST /api/incidents/{id}/approve
POST /api/incidents/{id}/reject
```

State machine:
```text
DETECTED
→ CORRELATING
→ DIAGNOSING
→ DIAGNOSED
→ ACTION_PROPOSED
→ AWAITING_APPROVAL
→ EXECUTING
→ VALIDATING
→ RESOLVED
```

Failure:
```text
VALIDATING
→ FAILED_REMEDIATION
→ ESCALATED
```

Critical invariant:
> `RESOLVED` is impossible unless M1 returns successful recovery validation.

Use AI to generate one component at a time. Never ask it to generate the whole backend in one shot.

Done when:
- Day-3 mock lifecycle works end to end,
- real providers can replace mocks by config,
- approval is persisted,
- no manual JSON copying is required in the final demo.


# Day 1 — Decision Contract

### Shared Step A — Bootstrap shared FastAPI core
Before decision logic, generate:
```text
shared-nexus-api/app/main.py
shared-nexus-api/app/api/
shared-nexus-api/app/state_machine/
shared-nexus-api/app/providers/
shared-nexus-api/tests/
```
Add `/health`.

### Shared Step B — Implement state machine tests
Tests must reject:
- illegal jumps,
- RESOLVED without recovery success,
- EXECUTING without approval.

P0 actions:
```text
ROLLBACK
SCALE
ESCALATE
```

Create:
```text
GET  /health
POST /internal/decisions/evaluate
POST /internal/remediation/execute
```

Use mocks first.

# Day 2 — Deterministic Policy Rules

Bad deployment:
```text
IF RCA = faulty_deployment
AND confidence >= threshold
AND previous version exists
→ propose ROLLBACK
→ approval required
```

Traffic:
```text
IF RCA = traffic_spike
AND SLO degraded
AND safe replica range allows
→ compare scale options
→ propose SCALE
→ approval required
```

Unknown:
```text
→ ESCALATE
```

### Shared Step C — Build mock incident API
Implement shared routes:
```text
POST /api/incidents
GET /api/incidents
GET /api/incidents/{id}
POST /api/incidents/{id}/approve
POST /api/incidents/{id}/reject
```

Then connect canonical mock providers to achieve the Day-3 vertical slice.

# Day 3 — Shared Orchestrator Integration

Coordinate mock vertical slice:
```text
anomaly
→ RCA
→ FinOps context
→ decision
→ approval
→ mock action
→ mock recovery
```

# Day 4 — Real Deployment/Executor Plumbing

Coordinate with platform:
- known v1/v2
- allowlisted namespace/service
- audit record

Manual rollback must work before automated rollback.

# Day 5 — Real Decision Input

Consume M3 real RCA.

If M6 cost context unavailable:
- use contract-valid mock
- do not block decision-engine implementation

# Day 6 — ROLLBACK + Approval

Enforce:
- approval required
- target allowlist
- parameter validation
- no arbitrary shell/kubectl

Return canonical ActionResult.

Then:
```text
action success
→ request M1 validation
```

Do not resolve incident yourself.

# Day 7 — Flagship Gate

3 consecutive runs:
```text
RCA
→ decision
→ approval
→ rollback
→ M1 validation
```

No manual kubectl rescue.

# Day 8 — SCALE

Implement safe scale action:
- min/max replicas
- allowlisted service
- approval
- M6 resource/cost options

Test:
- valid scale
- over-max reject
- negative reject
- unknown service reject
- no approval reject

# Day 9 — Cost-Aware Decision

Consume M6:
```text
scale option A
cost delta
risk

scale option B
cost delta
risk
```

Decision should prefer reliability within policy, not blindly cheapest.

### Shared Step D — Remove manual glue
Audit the P0 demo for:
- copied JSON,
- manual DB edits,
- manual state edits,
- hidden kubectl fixes.

Remove these before the gate.

# Day 10 — P0 Gate

Both scenarios:
- correct action proposed
- approval visible
- action executes
- audit stored
- M1 validation controls resolution

# Day 11 — Evaluation

Create:
```text
scenario
run
recommended action
approved
action success
action duration
recovery success
```

# Day 12 — Hardening

Handle:
- approval missing
- stale decision
- Kubernetes failure
- policy violation
- M1 validation timeout

# Day 13 — Rehearsal

Explain:
- how RCA becomes a structured decision
- how M6 cost context influences scale
- why LLM cannot execute
- why recovery validation is separate

# Day 14 — Final Verification

P0 done when:
- real M3/M6 inputs consumed
- rollback and scale safe
- approval enforced
- audit exists
- M1 validation determines resolution
- shared state machine stable

# Bonus

- RESTART action
- automatic low-risk remediation
- richer policy engine
- rollback confidence calibration
