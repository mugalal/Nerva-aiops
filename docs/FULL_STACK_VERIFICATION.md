# Integrated runtime and verification

All six modules share the current APIs. M5 adapts M4's bare incident list and consumes
`GET /api/incidents/{incident_id}/context`, which retains original anomaly, RCA,
proposal, action, recovery, FinOps, deployment, audit and provenance records.
The frozen incident and memory contracts are unchanged.

## Start the Compose integration stack

From repository root, with Docker Desktop running:

```powershell
./scripts/start-stack.ps1 -Operator mugalal
```

This builds M1–M6 and payment, starts Prometheus/Loki/Alloy/Grafana and PostgreSQL,
and waits for serving checks. It generates local database and approval credentials
once in the ignored `.review-branches/stack-runtime.json`; reruns reuse them so the
persistent database remains accessible. Existing `NEXUS_DB_PASSWORD` and
`NEXUS_APPROVAL_TOKEN` environment variables override the generated values.
Credentials remain in backend configuration and are never returned to the UI.
Use `-NoBuild` only when current images are already built.

The UI is at <http://127.0.0.1:8005/>. Compose's actuator is explicitly `mock`:
approval exercises the workflow, while real infrastructure execution is verified
on the isolated Kubernetes stack. M4 therefore reports degraded mode in Compose.
Provider availability and a running container do not establish recovery.

Named volumes retain PostgreSQL, M1 baseline/evidence, M2 alert evidence and
observability history. The start script does not delete volumes or seed fixtures.
Before M1 starts, a one-time root init service prepares its existing data volume
for uid/gid 10001. It updates ownership and permissions, skips symbolic links and
retains existing baseline/evidence files; the running telemetry service stays nonroot.
A healthy measured M1 baseline is still required before M3 diagnoses an incident.
After collecting it, run:

```powershell
./scripts/check-stack.ps1 -AllowMockActuator
```

The gate requires all six health responses, durable M4 storage, enabled M2 polling
and handoff, and M4/M5/M6 readiness. The explicit Compose exception accepts only a
mock actuator with real configured providers and working durable storage.
For Kubernetes, omit that exception and supply the forwarded module URLs.

## Kubernetes dependencies

The isolated `nexus-demo` namespace needs a privately created `nexus-runtime`
Secret containing `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`,
`NEXUS_APPROVAL_TOKEN` and `M5_OPERATOR_ID`. Never commit the Secret. `DATABASE_URL`
uses the `postgres:5432` Service hostname. Apply dependencies in this order:

Build a single-platform local PostgreSQL image and import that exact tag before
applying its StatefulSet. The committed Dockerfile adds a distinct image config,
which avoids the missing-content/CRI-alias issue seen when importing the upstream
multi-platform index or a FROM-only repack into kind.

```powershell
docker build --provenance=false --platform linux/amd64 -f observability/postgres/Dockerfile -t nexus/postgres:dev .
kind load docker-image nexus/postgres:dev --name nexus
```

If an existing StatefulSet uses a differently tagged verified PostgreSQL image,
keep that explicit tag or update it intentionally. Never remove its PVC to fix
an image-loading error. For an initial failed Pod, diagnose its events and verify
the exact image is present on the scheduled node before recreating only that Pod.

1. Namespace, runtime Secret and `observability/k8s/postgres.yaml`; wait for PostgreSQL.
2. Observability and payment, then M1 and its measured baseline.
3. M3, M4 and M6, then M5; M4 retries memory delivery if M5 is unavailable.
4. M2 with polling/handoff enabled and a validated reference.

M4/M5/M6 read the same database connection Secret. PostgreSQL has a retained PVC;
M2 has a separate alert-evidence PVC. M4's recovery worker polls after approval;
only the measured M1 recovery result can resolve an incident. An ambiguous
execution after restart fails closed instead of repeating the action.

## Operator decisions and memory

M5 forwards Approve to `/approve` and Reject to `/reject` independently, using a
backend token and configured operator ID. The UI disables both buttons unless an
incident is `AWAITING_APPROVAL`; cross-origin writes are rejected. Action success
does not set recovery success. Missing FinOps data or recovery appears as missing.

M4's durable outbox archives terminal incidents. Retries use M5's immutable,
idempotent store, so an acknowledgement lost in transit does not create duplicates.
The live store accepts only authenticated backend writes; the browser never receives
its token and uses automatic M4 archival instead of submitting arbitrary snapshots.
Resolved, failed and escalated outcomes retain their original facts. Mixed or mock
workflows enter mock memory with explicit provenance; they cannot become real
successful-recovery evidence. The UI displays archive timestamps and exact source
citations. Copilot's grounded recorded-answer mode requires no model key.

## Automated checks

`.github/workflows/integration.yml` runs frozen-contract/module tests, UI behavior,
grounded-answer evaluation, controlled HTTP integration, actual PostgreSQL
restart/persistence checks, and all six service image builds. The dedicated database
job supplies disposable PostgreSQL credentials, so database tests run rather than
skip. These are configured CI gates; a GitHub run must pass before claiming remote
CI success. Image builds do not push images or commits.

Controlled HTTP tests use named fixtures and a mock actuator. They prove protocol,
validation, retry and persistence behavior. Live Kubernetes evidence must separately
show both fault scenarios with constant workload, pinned SLO, actual actions,
post-action recovery, restart survival and real memory/Copilot citations.
