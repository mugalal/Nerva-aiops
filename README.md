# NEXUS AIOps & FinOps Platform

NEXUS connects observability, anomaly detection, root-cause analysis, controlled
remediation, incident memory, and FinOps. M1 owns the evidence layer used by the
other modules.

## What M1 Now Provides

- Prometheus request, latency, CPU, memory, replica, and version signals.
- Structured JSON logs shipped to Loki by Grafana Alloy.
- A provisioned six-panel Grafana dashboard.
- Strict real-time and historical telemetry APIs.
- Measured healthy baselines and transparent SLO thresholds.
- Incident evidence with logs, Kubernetes state, and deployment metadata.
- Objective recovery validation for bad deployment and traffic spike scenarios.
- Durable before/after evidence in Docker volumes or a Kubernetes PVC.
- Explicit errors for missing, stale, or unavailable data; no silent fake values.

## Local Start

From the repository root:

```powershell
docker compose -f observability/docker-compose.yml up -d --build
```

Then create real traffic so rate and latency queries have samples:

```powershell
.\scripts\generate-traffic.ps1 -DurationSeconds 90 -Concurrency 5
.\scripts\smoke-check.ps1 -M1Only
```

Open:

| Tool | URL | Purpose |
| --- | --- | --- |
| M1 API | `http://localhost:8001/docs` | Try the normalized API |
| Grafana | `http://localhost:3000` | View the six M1 panels; login `admin` / `admin` locally |
| Prometheus | `http://localhost:9090` | Inspect raw metrics and targets |
| Loki | `http://localhost:3100/ready` | Confirm the log store is ready |

The M1 health endpoint may say `degraded` in Docker Compose because no Kubernetes
API is attached. Prometheus telemetry and Loki still remain real. In Kubernetes,
M1 automatically uses its read-only service account instead of local `kubectl`.

## M1 API

| Method and path | Used for |
| --- | --- |
| `GET /health` | M1 and dependency readiness |
| `GET /internal/telemetry/snapshot?service=payment-service` | Frozen snapshot for M2/M6 |
| `GET /internal/telemetry/window?...` | Historical features and baseline source |
| `POST /internal/baselines/measure` | Measure and save a healthy baseline |
| `POST /internal/evidence/capture` | Freeze incident-time metrics, logs, K8s, deployment evidence |
| `GET /internal/telemetry?incident_id=...` | Stored incident telemetry for M3 |
| `GET /internal/deployments?incident_id=...` | Stored deployment event for M3 |
| `GET /internal/logs?incident_id=...` | Stored selected logs for M3 |
| `POST /internal/recovery/validate` | Return the frozen `RecoveryResult` contract |
| `GET /internal/recovery/{incident_id}` | Export complete before/after evidence |

Metric units are part of the contract:

| Metric | Meaning |
| --- | --- |
| `request_rate` | `/pay` requests per second |
| `latency_p95_ms` | 95th-percentile `/pay` latency in milliseconds |
| `http_5xx_rate` | failed-request ratio from `0.0` to `1.0` |
| `cpu` | used CPU divided by configured CPU limit |
| `memory` | resident memory divided by configured memory limit |
| `replica_count` | number of instrumented running instances |

The baseline latency limit is measured healthy P95 multiplied by `1.25`. The
error limit is the larger of `1%` or measured healthy P95 error multiplied by
`1.5`. Both multipliers are environment variables and are saved with the
resulting baseline values. The local demo uses a one-minute Prometheus decision
window so recovery can be proven promptly; this is separate from how long a
baseline run is collected.

For the repeatable bad-deployment drill, start faulty v2 with the Compose
override, keep traffic running, capture evidence, then recreate v1 from the base
file:

```powershell
docker-compose -f observability/docker-compose.yml -f observability/docker-compose.faulty.yml up -d --build payment-service
docker-compose -f observability/docker-compose.yml up -d --force-recreate payment-service
```

M1 requires the post-action version to equal the measured healthy baseline
version before a bad-deployment incident can be marked recovered.

## Team Integration

- M2 reads M1 snapshot/window responses directly; no manual JSON conversion.
- M3 first needs evidence capture for the same `incident_id`, then reads the
  compatibility telemetry/deployment routes. Its incident provider belongs to
  the shared incident service, not M1.
- M4 posts `incident_id`, `service`, `action_completed_at`, and `scenario` after
  an action. It must use `recovered` and `slo_restored` from M1's response.
- M6 reads CPU, memory, request rate, replica count, and timing from the M1 APIs.

Do not consume the files under `mocks/` in integration mode. Mock telemetry is
available only when `M1_PROVIDER_MODE=mock` is intentionally set.

## Kubernetes

After the payment images and M1 image are available to the cluster:

```powershell
kubectl apply -f observability/k8s/prometheus.yaml
kubectl apply -f apps/demo-microservices/k8s/payment-service.yaml
kubectl apply -f services/telemetry-intelligence/k8s/telemetry-intelligence.yaml
```

The M1 service account can only read pods, events, and deployments in
`nexus-demo`; it cannot scale, restart, or roll back workloads.

## Tests

M1 tests use only Python's built-in `unittest` runner after runtime dependencies
are installed:

```powershell
python -m unittest discover -s tests -t . -p "test_*.py"
```

The cross-module smoke checks remain in `scripts/smoke-check.sh` and
`scripts/smoke-check.ps1`. During parallel development, unfinished module ports
are expected to fail; `-M1Only` checks just the M1 stack on Windows.
