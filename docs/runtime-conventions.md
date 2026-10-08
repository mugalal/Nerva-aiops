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
