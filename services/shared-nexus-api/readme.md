# M4 shared API and approved remediation

M4 listens on port 8004. RCA, FinOps, incident evidence and recovery providers
default to `real`. Set their URL variables to reachable M1/M3/M6 services.
`/health` reports configured provider and actuator modes; dependencies are
checked by the operations that use them.

Create an incident using `POST /api/incidents/` with `incident_id`, `severity`
and a nonempty `affected_services` list. `started_at` defaults to current UTC;
`anomaly_ids` defaults to an empty list. Ingest an actual frozen AnomalyEvent
using `POST /internal/anomalies?incident_id=...`; its service must belong to that
incident. Retrieval returns the latest actual linked event, or 404.

Call `POST /internal/decisions/build/{incident_id}`, inspect the canonical
`GET /api/incidents/{incident_id}/proposal`, then approve with
`POST /api/incidents/{incident_id}/approve`. Approval returns the frozen
ActionResult. Repeat approval returns the recorded result without executing
twice. The compatibility route `/internal/remediation/execute` uses the same
orchestrator. All real actions need the stored proposal and incident target.

Recovery polls use `POST /internal/recovery/validate/{incident_id}`. M1 pending
measurements produce HTTP 202 plus `Retry-After: 15`, preserving VALIDATING.
Only matching incident evidence with both `recovered` and `slo_restored` true
can resolve the incident. Caller-supplied success booleans are disabled in real
recovery mode. Dependency failures retain a retryable workflow state.
Measured negative recovery remains VALIDATING with HTTP 202 while stabilization
is still within `RECOVERY_TIMEOUT_SECONDS` (default 360, finite and positive,
maximum 3600). This budget begins at action completion; M1 thresholds and its
complete recovery hold still govern success. At the deadline, missing or
negative measurements escalate and repeated polls return the cached terminal
state. Initial and subsequent negative measurements remain in the audit trail.
The PowerShell incident launcher waits up to 600 seconds by default; its client
timeout should exceed the configured server recovery budget.

Scaling requires RCA confidence at least 0.7 and a LOW-risk option with an
integer replica count greater than the current count and at most 10. Missing
or unsafe FinOps context produces an escalation. Rollbacks do not depend on
FinOps availability. A scenario-bound M1 capture happens after classification
and before proposing an executable action; M3 independently reads M1 preview
evidence before classification.
Real evidence must include a measured baseline from before the incident with
at least five samples covering 60 seconds. Its service and recovery version
must match the captured action. Both executable diagnoses bind the exact
captured service version; a rollout during diagnosis invalidates the cached
diagnosis and requires a fresh build.

Build from repository root:

```powershell
docker build -f services/shared-nexus-api/Dockerfile -t nexus/shared-nexus-api:dev .
```

The image installs kubectl v1.37.0 only after validating its official SHA-256.
The Kubernetes manifest grants namespaced access to payment deployment/scale
and read access to rollout ReplicaSets/pods. Execution waits for rollout status
with finite command deadlines before recording action completion. Local
execution supports `KUBECTL_PATH`, optional `KUBECTL_CONTEXT`, and `KUBECONFIG`;
in-cluster execution uses the mounted service account automatically.
Real proposals pin the live Deployment UID and complete pod template, and
scaling verifies the CPU request used by FinOps. Execution serializes actions
for the same service and rechecks these values. Scaling uses Kubernetes replica
and resource-version preconditions; rollback restores the pinned matching
revision template with an atomic resource-version test. Changed or incomplete
deployments fail closed. The legacy Jenkins actuator is disabled until its job
can enforce these captured deployment preconditions.

Compose can use real data providers with `REMEDIATION_BACKEND=mock`: this verifies
HTTP integration and records an explicit mock actuator result. Real Kubernetes
actions use `REMEDIATION_BACKEND=kubernetes` with a configured cluster runtime.
The incident/anomaly/audit store is currently process memory; restarting M4
loses its workflow state. The single-replica manifest does not provide durable
multi-replica orchestration.

Offline unit tests explicitly select all provider mocks in `tests/conftest.py`;
the real-provider regression cases use labelled HTTP doubles, without contacting
dependencies or executing Kubernetes/Jenkins commands.
