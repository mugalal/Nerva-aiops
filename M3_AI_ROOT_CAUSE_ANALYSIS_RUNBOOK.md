# M3 — AI Root Cause Analysis Runbook

**AIOps question:** Why did the incident happen, and what evidence supports that conclusion?  
**Timebox:** 14 days

## P0 Ownership

- evidence collection
- deployment/metric/log correlation
- candidate root-cause set
- deterministic evidence scoring
- grounded LLM explanation
- RCA confidence/score
- RCA evaluation

## Core Integration

Consumes M2 anomaly + M1 operational evidence + deployment events. Produces RCAResult for M4 and incident record for M5.

## Secondary / Backup

Backup: M2 for evidence features. You are backup for M5 grounding/retrieval quality.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

You own the common provider/error/fallback conventions because RCA consumes the widest set of evidence sources.

Shared responsibilities:

- define internal provider interface pattern,
- define timeout/error result format,
- define mock-vs-real provider switching convention,
- define evidence/source traceability metadata.

Create:
```text
services/shared-nexus-api/app/providers/base.py
services/shared-nexus-api/app/providers/errors.py
docs/provider-conventions.md
```

Provider rule:
```text
same consumer interface
→ mock provider in development
→ real provider in integration
```

Error/fallback must distinguish:
```text
unavailable
timeout
insufficient_data
invalid_response
```

Done when:
- M4 shared core can swap mock/real providers by configuration,
- consumers never need to rewrite business logic when a provider becomes real.


# Day 1 — RCA Contract

### Shared Step A — Create provider base/error model
Generate and test the common provider adapter pattern before RCA-specific adapters.

Required error categories:
```text
timeout
unavailable
invalid_response
insufficient_data
```

**Integration:** M4 orchestrator uses this convention for every module.

Candidate causes P0:
```text
faulty_deployment
traffic_spike
unknown
```

Build:
```text
GET  /health
POST /internal/rca/analyze
```

Use mocks first.

# Day 2 — Evidence Model

Create normalized EvidenceBundle:
```text
incident
anomaly features
recent deployment
metric changes
selected logs
Kubernetes events
optional traces
```

Build deterministic scoring.

# Day 3 — Real Evidence Adapters

Integrate:
- M1 telemetry/evidence
- deployment event source
- M2 anomaly event

If unavailable, keep same interface with mocks.

# Day 4 — Flagship RCA

On saved real bad-deploy data:
- identify v2 deployment
- cite latency/5xx change
- reject unsupported causes

# Day 5 — Real RCA Gate

Shared core sends real incident.
Return stable RCAResult.

No manual copying.

# Day 6 — Grounded LLM Explanation

Optional LLM layer:
- receives only structured evidence
- cannot invent metrics
- cannot execute actions

Fallback:
- deterministic templated explanation

Test hallucination:
- remove DB evidence
- explanation must not claim DB failure

# Day 7 — Flagship Evaluation

For 3 runs record:
```text
ground truth
predicted cause
score
evidence
correct?
```

# Day 8 — Traffic RCA

Traffic logic uses:
```text
request rate ↑
CPU/load ↑
latency ↑
no causally relevant bad deploy
```

Ambiguous case:
- lower confidence or unknown
- do not force certainty

# Day 9 — RCA Context for Memory

Provide M5:
```text
root cause
evidence
affected component
confidence
action class recommendation
```

# Day 10 — Freeze Contract

No breaking RCA output changes.

Both scenarios must return grounded, different causes.

# Day 11 — RCA Evaluation

Create table:
```text
run
scenario
ground_truth
predicted
confidence
correct
evidence_complete
```

# Day 12 — Hardening

Fallback behavior:
- logs unavailable
- LLM unavailable
- insufficient evidence
- deployment history unavailable

Return degraded/unknown explicitly.

# Day 13 — Rehearsal

Explain:
- evidence collection
- deterministic scoring
- LLM role
- grounding
- measured correctness

# Day 14 — Final Verification

P0 done when:
- M1/M2 real inputs consumed
- both causes distinguished
- no hallucinated evidence
- M4 consumes RCA
- RCA correctness measured

# Bonus

- trace evidence
- graph-based RCA
- service dependency reasoning
- confidence calibration
