# NEXUS Runtime Conventions

This document defines the shared runtime conventions that every NEXUS module must follow.

## Required Environment Variables

Every service must support these variables:

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SERVICE_NAME` | yes | module-specific | Stable service identifier, for example `telemetry-intelligence`. |
| `SERVICE_VERSION` | yes | `0.1.0` | Service version returned by `/health` and logs. |
| `ENVIRONMENT` | yes | `development` | Runtime environment such as `development`, `integration`, or `demo`. |
| `LOG_LEVEL` | yes | `INFO` | Logging threshold. |
| `DATABASE_URL` | when persistent storage is used | none | PostgreSQL connection string. |

Provider base URLs must use this format:

| Variable | Owner | Example |
| --- | --- | --- |
| `M1_TELEMETRY_BASE_URL` | M1 | `http://localhost:8001` |
| `M2_ANOMALY_BASE_URL` | M2 | `http://localhost:8002` |
| `M3_RCA_BASE_URL` | M3 | `http://localhost:8003` |
| `M4_DECISION_BASE_URL` | M4 | `http://localhost:8004` |
| `M5_MEMORY_BASE_URL` | M5 | `http://localhost:8005` |
| `M6_FINOPS_BASE_URL` | M6 | `http://localhost:8006` |
| `SHARED_NEXUS_API_BASE_URL` | shared core | `http://localhost:8000` |

M1's real provider URLs are `PROMETHEUS_BASE_URL`, `LOKI_BASE_URL`, and
`PAYMENT_SERVICE_BASE_URL`. In Kubernetes they default in the supplied manifest
to `http://prometheus:9090`, `http://loki:3100`, and `http://payment-service` in
`nexus-demo`. `M1_DATA_DIR` holds durable baseline/incident/recovery records.

## M1 Baseline and Recovery Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `M1_PROVIDER_MODE` | `real` | Explicit `real` or `mock`; no automatic fallback. |
| `M1_STALE_AFTER_SECONDS` | `60` | Maximum age of the underlying metric scrape. |
| `M1_HISTORY_STEP_SECONDS` | `15` | Historical sample interval. |
| `M1_REQUEST_RATE_WINDOW` | `1m` | Rate query lookback. |
| `M1_LATENCY_WINDOW` | `1m` | Latency query lookback. |
| `M1_BASELINE_MIN_SAMPLES` | `5` | Minimum accepted baseline samples. |
| `M1_BASELINE_MIN_DURATION_SECONDS` | `60` | Minimum baseline duration. |
| `M1_BASELINE_MIN_COVERAGE_RATIO` | `0.9` | Required fraction of expected samples. |
| `M1_BASELINE_MIN_REQUEST_RATE` | `0.1` | Minimum healthy traffic in requests/second. |
| `M1_BASELINE_MAX_ERROR_RATE` | `0.01` | Maximum healthy 5xx ratio. |
| `M1_BASELINE_MAX_LATENCY_MS` | `500` | Maximum healthy P95 latency in milliseconds. |
| `M1_BASELINE_MAX_UTILIZATION_RATIO` | `0.85` | Maximum healthy CPU and memory ratios against limits. |
| `M1_BASELINE_LATENCY_MULTIPLIER` | `1.25` | Multiplier on healthy P95 latency. |
| `M1_BASELINE_ERROR_MULTIPLIER` | `1.5` | Multiplier on healthy P95 5xx ratio. |
| `M1_BASELINE_ERROR_FLOOR` | `0.01` | Minimum resulting 5xx SLO threshold. |
| `M1_TRAFFIC_CONTINUITY_RATIO` | `0.5` | Required post-action traffic relative to incident traffic. |
| `M1_RECOVERY_HOLD_SECONDS` | `30` | Sustained healthy window after rate/latency lookbacks flush. |
| `M1_RECOVERY_MIN_SAMPLES` | `3` | Minimum accepted recovery samples. |

All request timestamps must include timezone information. Historical windows
must contain aligned metric samples and one stable version observed at the
requested times. Do not tag historical data with today's deployed version.
Baseline collection requires healthy traffic and sufficient sample coverage;
keep real traffic running throughout the remediation and recovery drill.

Capture and recovery support `bad_deployment` and `traffic_spike`. A validation
request must use the captured incident's service and scenario. Its
`action_completed_at` must be no earlier than capture and no later than now.
After action completion, wait for the longer query lookback plus the healthy
hold interval. The defaults require at least 90 seconds and three samples;
validating sooner returns HTTP 409 `recovery_pending` with `retryable=true`.
Bad deployment recovery requires the healthy baseline version. Traffic spike
recovery requires increased replicas and continued traffic.

## M1 Kubernetes Evidence Stack

Apply `observability/k8s/namespace.yaml` before all namespaced manifests. Install
Prometheus, Loki, Alloy, payment-service, and telemetry-intelligence using the
order in the root README. Prometheus, Loki, and M1 require a dynamic StorageClass
and PVCs; single-replica data deployments use `Recreate` for their `ReadWriteOnce`
volumes. Alloy's DaemonSet tails Linux node CRI files and stores read positions
on the same node. It only discovers labeled pods in `nexus-demo` on its own node.

Alloy has `get/list/watch` access to pods. M1 has read-only access to pods, events,
deployments, and ReplicaSets in `nexus-demo`. M1 reads the active ReplicaSet to
obtain deployment timing. These service accounts have no workload mutation
permissions. In Compose, the missing Kubernetes dependency is explicitly
`degraded`; it does not make metrics or logs fabricated.

Smoke checks require real mode, a fresh nonzero payment snapshot, and `/pay`
logs from the last two minutes. They accept Compose's degraded Kubernetes state
when the metric and Loki dependencies are healthy. Use `-M1Only` (PowerShell) or
`--m1-only` (Bash) for M1 only. Skip Grafana only when using the Kubernetes
manifests with `-SkipGrafana` or `--skip-grafana`; Compose supplies Grafana.
For Kubernetes validation, also pass `-RequireKubernetes` or
`--require-kubernetes` to require the Kubernetes evidence provider to be healthy.

## Health Response

Every service must expose:

```text
GET /health
```

Minimum response:

```json
{
  "service": "telemetry-intelligence",
  "status": "ok",
  "version": "0.1.0",
  "environment": "development"
}
```

Allowed `status` values:

| Status | Meaning |
| --- | --- |
| `ok` | Service is ready for P0 integration. |
| `degraded` | Service is running but one or more dependencies are unavailable. |
| `unavailable` | Service cannot perform its P0 responsibility. |

Services must not return `ok` when a required downstream dependency is unavailable for a request path that claims readiness.

## Structured Logging

Application logs must be JSON objects with these fields:

```json
{
  "timestamp": "2026-09-29T10:00:00Z",
  "level": "INFO",
  "service": "telemetry-intelligence",
  "version": "0.1.0",
  "environment": "development",
  "message": "service started"
}
```

Additional fields are allowed when useful:

```text
incident_id
correlation_id
request_id
service_name
namespace
pod
scenario
provider
duration_ms
error_category
```

## Telemetry Labels

M1-owned telemetry must use these labels consistently:

```text
service_name
namespace
pod
version
environment
```

## P0 Signals

The frozen `TelemetrySnapshot` contract has `timestamp`, `service`, and `version` at
the top level. Its `metrics` object contains exactly:

```text
request_rate
latency_p95_ms
http_5xx_rate
cpu
memory
replica_count
```

`service_health` is evidence, but it is not inside the frozen snapshot. Read it
from `GET /health` or the Kubernetes context returned by M1 evidence capture.

Units are fixed:

| Field | Unit |
| --- | --- |
| `request_rate` | requests per second |
| `latency_p95_ms` | milliseconds |
| `http_5xx_rate` | ratio from `0.0` to `1.0` |
| `cpu` | ratio of used CPU to configured CPU limit |
| `memory` | ratio of resident bytes to configured memory limit |
| `replica_count` | running instrumented service instances |

Optional but recommended:

```text
pod_restarts
db_connections
trace_id
```

## Missing Data Rules

Never fabricate telemetry, recovery, or health data.

When data is missing or stale:

1. Health endpoints return `degraded` or `unavailable` with dependency details.
2. Frozen data endpoints return a typed `4xx`/`5xx` error instead of inventing a
   snapshot that looks real.
3. Include a human-readable reason and stable error category.
4. Use canonical mocks only in explicit `M1_PROVIDER_MODE=mock`; never silently
   fall back from real providers to mock data.
