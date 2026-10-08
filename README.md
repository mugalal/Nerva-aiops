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
The smoke checks allow this Compose limitation, but require real M1 providers,
a fresh payment snapshot with nonzero traffic, and queryable `/pay` logs from
the last two minutes. A ready Prometheus or Loki process alone is insufficient.

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
`1.5`. Both multipliers are environment variables; the resulting thresholds are
saved with the baseline. The local demo uses a one-minute Prometheus decision
window so recovery can be proven promptly; this is separate from how long a
baseline run is collected.

Baselines must come from a stable healthy version under real traffic. The default
quality gates require at least five samples over 60 seconds, 90% sample coverage,
request rate of at least `0.1` requests/second, and a 5xx ratio no higher than
`0.01`. Healthy samples also stay within a `500 ms` latency ceiling and CPU/memory
utilization ratios of `0.85` against their limits. These gates are configurable.
Historical windows retain the version observed at that historical time;
windows spanning a version change are rejected. Start/end timestamps must include
a timezone. The complete settings are in [runtime conventions](docs/runtime-conventions.md).

For example, after starting a healthy v1 stack, collect three minutes of traffic
and measure the last complete healthy window:

```powershell
.\scripts\generate-traffic.ps1 -DurationSeconds 180 -Concurrency 5
$baselineEnd = [DateTimeOffset]::UtcNow.AddSeconds(-15)
$baselineBody = @{
    service = "payment-service"
    start = $baselineEnd.AddSeconds(-75).ToString("o")
    end = $baselineEnd.ToString("o")
    step_seconds = 15
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8001/internal/baselines/measure -ContentType application/json -Body $baselineBody
```

Keep a separate traffic generator running during fault injection, remediation,
and recovery validation. Recovery must prove sustained health under load.

For the repeatable bad-deployment drill, start faulty v2 with the Compose
override, keep traffic running, capture evidence, then recreate v1 from the base
file:

```powershell
docker compose -f observability/docker-compose.yml -f observability/docker-compose.faulty.yml up -d --build payment-service
docker compose -f observability/docker-compose.yml up -d --force-recreate payment-service
```

M1 requires the post-action version to equal the measured healthy baseline
version before a bad-deployment incident can be marked recovered.
Use exactly `bad_deployment` or `traffic_spike` for both capture and validation;
the validation scenario must match the captured incident. `action_completed_at`
must be timezone-aware, no earlier than capture, and no later than the current
time. The validator waits for the longer metric query window to pass after the
action, then requires 30 seconds of healthy samples (at least three by default).
With the one-minute query windows this is at least 90 seconds after completion;
an early request returns retryable HTTP 409 `recovery_pending`. A traffic-spike
recovery additionally requires more replicas than before the action and continued
traffic. Exported recovery evidence includes the measured validation window.
Evidence capture pins the healthy baseline for that incident. A later baseline
measurement cannot relax its SLO or change its rollback version. When deployment
evidence is available, rollback must return to its recorded previous version.

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

Use Linux nodes and a default dynamic StorageClass; the three PVCs must bind.
Build the application images, then make them available on every node. Docker
Desktop Kubernetes can use images built in its local Docker engine. For kind or
minikube, load the images into that cluster; for a remote cluster, push them to a
registry and update the two application manifests' image references.

```powershell
docker build --build-arg VERSION=v1 -t nexus/payment-service:v1 apps/demo-microservices/payment-service
docker build -f services/telemetry-intelligence/Dockerfile -t nexus/telemetry-intelligence:dev .
kubectl get nodes
kubectl get storageclass
```

Create the namespace first, before applying any namespaced resource:

```powershell
kubectl apply -f observability/k8s/namespace.yaml
kubectl apply -f observability/k8s/prometheus.yaml
kubectl apply -f observability/k8s/loki.yaml
kubectl apply -f observability/k8s/alloy.yaml
kubectl apply -f apps/demo-microservices/k8s/payment-service.yaml
kubectl apply -f services/telemetry-intelligence/k8s/telemetry-intelligence.yaml
kubectl get pvc -n nexus-demo
kubectl rollout status -n nexus-demo deployment/prometheus --timeout=180s
kubectl rollout status -n nexus-demo deployment/loki --timeout=180s
kubectl rollout status -n nexus-demo daemonset/alloy --timeout=180s
kubectl rollout status -n nexus-demo deployment/payment-service --timeout=180s
kubectl rollout status -n nexus-demo deployment/telemetry-intelligence --timeout=180s
```

Loki and Prometheus persist their data in PVCs, and M1 persists incident evidence
in its own PVC. Alloy runs once per Linux node, reads that node's CRI log files
from `/var/log/pods`, and preserves its read positions in `/var/lib/nexus-alloy`
across pod restarts on the same node. Node replacement does not preserve that
node-local position directory. The log pipeline carries `service_name`,
`namespace`, `pod`, `version`, and `environment`, and sends records to the
`loki` service. Alloy only has read discovery access to pods in `nexus-demo`.

The M1 service account can only read pods, events, deployments, and ReplicaSets in
`nexus-demo`; it cannot scale, restart, or roll back workloads.

To check the cluster separately from the Compose ports, run each port-forward in
its own terminal:

```powershell
kubectl port-forward -n nexus-demo service/payment-service 18000:80
kubectl port-forward -n nexus-demo service/telemetry-intelligence 18001:8001
kubectl port-forward -n nexus-demo service/prometheus 19090:9090
kubectl port-forward -n nexus-demo service/loki 13100:3100
```

Then run traffic and the data checks from another terminal. These Kubernetes
manifests contain the M1 evidence stack; Grafana is provisioned in Compose.

```powershell
$env:M1_TELEMETRY_BASE_URL = "http://localhost:18001"
$env:PROMETHEUS_BASE_URL = "http://localhost:19090"
$env:LOKI_BASE_URL = "http://localhost:13100"
.\scripts\generate-traffic.ps1 -BaseUrl http://localhost:18000 -DurationSeconds 90 -Concurrency 5
.\scripts\smoke-check.ps1 -M1Only -SkipGrafana -RequireKubernetes
```

If a rollout waits indefinitely, inspect `kubectl get pods,pvc -n nexus-demo` and
the relevant pod's events. Pending PVCs indicate the storage provisioner must be
configured before the stack can start.

## Tests

M1 tests use only Python's built-in `unittest` runner after runtime dependencies
are installed:

```powershell
python -m unittest discover -s tests -t . -p "test_*.py"
```

The cross-module smoke checks remain in `scripts/smoke-check.sh` and
`scripts/smoke-check.ps1`. During parallel development, unfinished module ports
are expected to fail. Use `-M1Only` on Windows or `--m1-only` in Bash for the
M1 stack. Bash requires Python 3 for JSON validation; `PYTHON_BIN` can select its
executable. Both scripts fail if real telemetry or recent payment request logs
are missing and print instructions for supplying traffic. Use `-SkipGrafana` or
`--skip-grafana` for the Kubernetes stack above.
Add `-RequireKubernetes` or `--require-kubernetes` for cluster validation so an
unavailable Kubernetes evidence provider fails the smoke check.
