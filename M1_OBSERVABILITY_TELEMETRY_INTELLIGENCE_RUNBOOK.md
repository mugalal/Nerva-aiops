# M1 — Observability & Telemetry Intelligence Runbook

**AIOps question:** What is happening in the system, and how do we turn raw signals into reliable operational context?  
**Timebox:** 14 days

## P0 Ownership

- Prometheus metrics
- Grafana raw operational dashboards
- Loki logs
- Kubernetes events
- deployment-event visibility
- telemetry normalization
- baseline/SLO signals
- recovery validation
- optional OpenTelemetry/Tempo as bonus

## Core Integration

Produces telemetry for M2, evidence for M3, validation for M4/shared core, utilization data for M6.

## Secondary / Backup

Backup: M4 for Kubernetes/remediation telemetry path. You are backup for M6's utilization inputs.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

In addition to your AIOps work, you own these common utilities:

- shared `/health` response convention,
- environment/config naming conventions,
- structured application logging convention,
- runtime smoke-check script used by the team,
- integration-environment readiness check.

Use AI to generate boilerplate, but you must test it.

## Shared Deliverables

Create:
```text
shared/config/
shared/logging/
scripts/smoke-check.sh
docs/runtime-conventions.md
```

Minimum shared health response:
```json
{
  "service": "anomaly-engine",
  "status": "ok",
  "version": "0.1.0"
}
```

Done when:
- every module follows the same health/config/logging convention,
- `scripts/smoke-check.sh` can verify all P0 services and the shared API.


# Day 1 — Telemetry Foundation

### Shared Step A — Freeze common runtime conventions
Before module work, create `docs/runtime-conventions.md` covering:
```text
SERVICE_NAME
SERVICE_VERSION
ENVIRONMENT
LOG_LEVEL
DATABASE_URL (where applicable)
provider base URLs
```
Also define the common `/health` JSON format.

**Test:** M2 and M3 can use the same environment/health conventions without inventing their own.

## Step 1 — Freeze telemetry schema
Define P0 signals:
```text
request_rate
latency_p95_ms
http_5xx_rate
cpu
memory
replica_count
service_health
version
```
Optional:
```text
pod_restarts
db_connections
trace_id
```

## Step 2 — Bring up Prometheus/Grafana/Loki
Test:
- Prometheus reachable
- Grafana datasource works
- Loki accepts/query logs

## Step 3 — Define common labels
```text
service_name
namespace
pod
version
environment
```
Integration: M2/M3/M6 depend on these names.

# Day 2 — Instrument Real Services

## Step 4 — Expose request metrics
Generate traffic and prove counters/histograms change.

## Step 5 — Centralize structured logs
Required fields:
```text
timestamp level service version message
```

## Step 6 — P0 Grafana dashboard
Panels only:
- request rate
- P95 latency
- 5xx rate
- CPU
- memory
- replicas

# Day 3 — Machine-Consumable Telemetry API

## Step 7 — Implement normalized telemetry adapter
Endpoint:
```text
GET /internal/telemetry/snapshot?service=<name>
```
Return canonical Telemetry Snapshot.

## Step 8 — Historical window endpoint/helper
Needed by M2:
```text
service + start + end → time-series/features source
```

## Step 9 — Incident evidence helper
Needed by M3:
```text
incident window → metrics summary + selected logs + K8s/deployment context
```

# Day 4 — Healthy Baseline

## Step 10 — Run healthy measurement window
With team:
- stable traffic
- no intentional fault
- record run ID/timestamps

## Step 11 — Establish initial baseline ranges
Document:
```text
normal P95
normal 5xx
normal request rate
normal CPU/memory
```

## Step 12 — Give M2 clean baseline dataset
No manual spreadsheet transformation unless documented/reproducible.

# Day 5 — Support Real Anomaly/RCA Gate

### Shared Step B — Add team smoke-check script
Create `scripts/smoke-check.sh` that checks:
- shared API health,
- M1 health,
- M2 health,
- M3 health,
- M4 health,
- M5 health,
- M6 health,
- Prometheus readiness.

**Integration target:** used at every Day-7+ gate.

## Step 13 — Freeze metric/query names
No breaking changes after today.

## Step 14 — Confirm bad-deployment telemetry
M2/M3 must see:
- degradation after v2
- version/deployment context
- no fake values

# Day 6 — Recovery Validation

## Step 15 — Build Recovery Validator
Endpoint:
```text
POST /internal/recovery/validate
```

Input:
```text
incident_id
service
action_completed_at
scenario
```

Output: canonical RecoveryResult.

## Step 16 — Validation logic
At minimum:
- service health
- latency back inside baseline/SLO range
- 5xx under limit
- no immediate replacement anomaly if available

Test:
- faulty v2 without rollback → false
- restored v1 → true

# Day 7 — Flagship Gate

For 3 runs:
- capture before metrics
- capture anomaly window
- capture post-rollback metrics
- validate recovery

Your gate:
> 3/3 runs have objective telemetry proving recovery.

# Day 8 — Traffic Scenario

## Step 17 — Validate scale under continued load
Recovery cannot be declared only because traffic stopped.

Check:
- replicas increased
- latency improved
- health OK
- traffic still present during validation window

## Step 18 — Provide M6 scaling resource/timing data
M6 uses this for temporary cost impact.

# Day 9 — Persist Evidence

Store compact before/after snapshots with incident records.

# Day 10 — P0 Integration Gate

Verify:
```text
M1 → M2 real telemetry
M1 → M3 real evidence
M1 → M6 utilization
M1 → shared core recovery
```

# Day 11 — Evaluation

Export per-run:
```text
baseline
fault window
recovery window
SLO restored
telemetry timestamps
```

Cross-check MTTD/MTTR clocks with M2/M4/shared core.

# Day 12 — Hardening

Handle:
- Prometheus unavailable
- missing points
- stale data
- Loki unavailable
- validator timeout

Return unknown/degraded state, never fake recovery.

# Day 13 — Rehearsal

Explain:
- why these signals matter
- how M2 consumes them
- how M3 uses them as evidence
- how recovery is objectively measured

# Day 14 — Final Verification

P0 done when:
- telemetry queryable
- logs queryable
- normalized API stable
- M2/M3/M6 consume real outputs
- recovery validation works in both scenarios
- saved dashboards/evidence exist

# Bonus

Only after P0:
- OpenTelemetry full tracing
- Tempo
- automatic service dependency graph
- richer SLOs
