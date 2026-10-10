# M4 shared API and approved remediation

M4 listens on port 8004. RCA, FinOps, incident evidence and recovery providers
default to `real`. Set their URL variables to reachable M1/M3/M6 services.
`/health` reports provider/actuator modes and probes workflow storage. `/ready`
returns 503 when real operation lacks durable storage or a required mode.

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
Approve, reject, direct execution, experiment registration and archive retries
share the same operator authentication: configure `NEXUS_APPROVAL_TOKEN` (or
explicitly `M5_SHARED_API_TOKEN`) and send `Authorization: Bearer <token>` or
`X-Nexus-Approver-Token`. `X-Nexus-Approver` records the operator identity.
There is no default secret; real remediation refuses startup without one.
Rejection applies only to an awaiting approval proposal.

`GET /api/incidents/{incident_id}/context` returns the original incident,
linked anomalies, RCA, decision, action, recovery, FinOps and deployment
evidence plus audit and workflow facts. Its `source` is `real`, `mock` or
`mixed`, based on the providers and actuator used, not the connection URL.

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
Set `DATABASE_URL` for PostgreSQL workflow storage. Startup initializes the
schema and restores incidents, linked anomalies, proposals/preconditions,
actions, recovery deadlines/measurements and audits. Normalized lifecycle
tables are updated with the authoritative incident JSON in the same transaction.
Storage failures return an explicit 503; real execution cannot start with a
memory or SQLite backend. SQLite is an explicitly selected controlled-test
backend (`M4_STATE_BACKEND=sqlite`, `M4_SQLITE_PATH=<path>`).

An action intent is persisted before Kubernetes is invoked. If M4 restarts
without a recorded result, the incident escalates with an unknown execution
outcome and refuses replay until an operator reconciles it. Completed actions
and validation deadlines survive restart. One M4 writer holds a PostgreSQL
advisory lease; run one worker/replica until distributed orchestration is added.
Loss of that database session fails subsequent storage operations closed.

Ingested anomalies queue durable diagnosis work (`M4_AUTO_BUILD_DECISIONS=true`
by default). `M4_AUTO_VALIDATE_RECOVERY=true` enables post-approval recovery
polling; otherwise the caller polls explicitly. The lifecycle worker checks
pending work once per second; recovery checks occur at least 15 seconds apart.

Terminal outcomes queue an immutable M5 memory outbox. Retryable network/HTTP
errors retain the same payload with exponential backoff capped at 300 seconds.
Delivery requires M5 to acknowledge the exact outcome and provenance. Permanent
4xx responses remain visible as `BLOCKED`; an authenticated
`POST /api/incidents/{incident_id}/archive/retry` retries the retained payload.
Real resolved records require successful action evidence and measured M1 SLO
recovery. Escalated outcomes carry `resolved=false`; ambiguous executions and
missing evidence remain blocked rather than receiving invented facts. Mixed
executions are isolated in the mock memory namespace with a provenance tag.

Register evaluation runs using authenticated
`POST /api/incidents/{incident_id}/experiment` with `run_id`, `scenario` and,
when known, observed `injection_time` plus `injection_evidence`. Unknown
injection time stays null, so MTTD stays null. Detection time is M4's recorded
incident receipt, action time is execution start, and recovery time is the UTC
instant M4 confirms measured recovery. Failed/escalated runs have no successful
recovery timestamp. Export stored runs with `python -m app.db.export_experiments`.

Offline unit tests explicitly select all provider mocks in `tests/conftest.py`;
the real-provider regression cases use labelled HTTP doubles, without contacting
dependencies or executing Kubernetes/Jenkins commands.
`tests/test_workflow_postgres.py` is an optional real PostgreSQL gate selected
with `M4_TEST_DATABASE_URL` pointing to a disposable verification database. It
uses an isolated schema and a separate Python process to prove restart retention,
normalized lifecycle records, experiment timing and ambiguous-action safety;
the actuator in this database check is explicitly mock.
