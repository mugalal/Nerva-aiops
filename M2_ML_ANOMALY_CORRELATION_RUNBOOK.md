# M2 — ML Anomaly Detection & Incident Correlation Runbook

**AIOps question:** When is behavior abnormal, and which abnormal signals belong to the same incident?  
**Timebox:** 14 days

## P0 Ownership

- feature extraction
- healthy/fault datasets
- threshold/statistical baseline
- one evaluated ML model
- anomaly scoring
- false-positive tuning
- basic incident correlation
- detection latency evaluation

## Core Integration

Consumes M1 telemetry. Produces AnomalyEvent/incident candidates for M3/shared core. Supplies feature evidence to M3.

## Secondary / Backup

Backup: M3 for feature interpretation. You are backup for M1 telemetry feature selection.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

You own the common contract-model layer because your module is the first structured intelligence producer.

Shared responsibilities:

- generate Pydantic models matching `/contracts`,
- create canonical valid/invalid fixtures,
- add contract tests,
- maintain schema version compatibility.

Create:
```text
services/shared-nexus-api/app/models/contracts.py
tests/contract/
mocks/validated/
```

Required tests:
- valid Telemetry Snapshot accepted,
- valid Anomaly Event accepted,
- invalid/missing required fields rejected,
- RCA/Decision/Recovery/FinOps contracts can be imported and validated.

Rule:
> You do not change a shared contract alone. Any breaking change requires agreement from its producer and all consumers.

Done when:
- all six modules can import/use the same shared models or generated equivalents,
- mock fixtures validate against the exact real schemas.


# Day 1 — Feature Contract

### Shared Step A — Generate canonical Pydantic contracts
Using the frozen JSON contracts, generate shared models for:
```text
TelemetrySnapshot
DeploymentEvent
AnomalyEvent
Incident
RCAResult
FinOpsContext
DecisionProposal
ActionResult
RecoveryResult
IncidentMemory
FinOpsRecommendation
```

**Test:** validate canonical mocks.

## Step 1 — Freeze P0 features
Use:
```text
request_rate
request_rate_change
latency_p95_ms
latency_change
http_5xx_rate
cpu
memory
replica_count
```

## Step 2 — Build extractor against mock telemetry
Output fixed feature vector.

## Step 3 — Anomaly service skeleton
```text
GET  /health
POST /internal/anomalies/evaluate
```

# Day 2 — Statistical Baseline

Implement a simple baseline first.

Test synthetic:
```text
healthy
bad deployment
traffic spike
```

Do not claim ML yet.

# Day 3 — Real M1 Telemetry

Consume M1 historical windows.
Persist:
```text
timestamp
service
raw values
features
scenario label
```

# Day 4 — Train Candidate Model

P0 comparison:
```text
threshold baseline
vs
Isolation Forest OR One-Class SVM
```

If time allows, compare all three; not required.

Evaluate:
```text
precision
recall
F1
false-positive rate
detection latency
```

# Day 5 — Select + Integrate

Select empirically.
Freeze canonical AnomalyEvent.

Real flow:
```text
M1 telemetry
→ M2 evaluate
→ anomaly event
→ shared incident
→ M3
```

# Day 6 — Incident Correlation

Group related anomaly signals by:
```text
service
time proximity
shared deployment/context
```

Output one incident candidate, not one incident per metric.

# Day 7 — Flagship Gate

For 3 bad-deployment runs:
- record deployment/fault time
- first anomaly time
- score
- features
- detection latency

Target:
> stable detection without manual threshold edits per run.

# Day 8 — Traffic Signature

Expose enough features to distinguish:
```text
traffic spike:
request_rate ↑
CPU ↑
latency ↑

bad deployment:
latency/5xx ↑
deployment context exists
request_rate not sufficient to explain degradation
```

M2 detects; M3 diagnoses.

# Day 9 — Store Feature Evidence

Persist anomaly score/features with incident for M5 memory and final evaluation.

# Day 10 — P0 Gate

Run a defined healthy interval.
Measure false positives.

Freeze model/config.

# Day 11 — Evaluation

Produce final table:
```text
run
scenario
ground_truth
detected
score
detection_delay
prediction
```

Aggregate:
```text
precision
recall
F1
false-positive rate
```

# Day 12 — Hardening

Handle:
- NaN
- missing data
- stale data
- Prometheus timeout

Do not turn missing telemetry into a high-confidence anomaly.

# Day 13 — Rehearsal

Explain:
- features
- why baseline exists
- why selected model won
- measured limitations
- how output becomes M3 RCA input

# Day 14 — Final Verification

P0 done when:
- real M1 telemetry consumed
- baseline + one ML model evaluated
- both scenarios detected
- correlation works
- false positives measured
- M3 consumes real anomaly events

# Bonus

- second/third ML model comparison
- forecasting-based anomaly detection
- adaptive thresholds
- confidence calibration
