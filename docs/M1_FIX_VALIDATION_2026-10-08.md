# M1 fixes and validation — 8 October 2026

Implemented on m1/prometheus-integration after the readiness review. Changes are local; no commits, merges, or pushes were made.

## Changes

- Historical telemetry retains the version observed in the requested interval, including the metric lookback. Mixed versions, missing samples, down scrape targets, or stale source timestamps return explicit errors.
- Healthy baseline collection requires stable real telemetry, at least five samples over 60 seconds, coverage, live traffic, and error/latency/utilization ceilings. Failed measurements preserve the existing baseline.
- Recovery rejects future or pre-capture completion times and unknown/mismatched scenarios. It pins each incident's baseline and uses the captured previous deployment version when available. It waits for metric windows to flush after the action and then checks a complete 30-second hold. Every measurement must meet the SLO; exported evidence includes the validation window.
- Kubernetes providers require completed rollout generations/updated replicas, derive running versions from pods/templates, and use matching ReplicaSet timestamps for deployment evidence.
- Evidence storage uses collision-free filenames, validates stored IDs, retains safe legacy reads, and supports concurrent atomic writes.
- Invalid time windows return 422. Repository-root unittest discovery resolves the M1 application.
- Kubernetes manifests now create namespace first and provide persistent Prometheus/Loki, Alloy CRI log collection, and read-only ReplicaSet access for M1.
- Smoke checks validate actual fresh payment metrics and recent request logs. Optional Kubernetes enforcement distinguishes the Compose demo from a complete cluster setup.

## Automated checks

All 53 M1 unit/API/provider/storage tests passed with pinned runtime dependencies. Coverage includes missing/stale telemetry, historical version changes, insufficient or unhealthy baseline evidence, recovery timing and sustained health, scenario mismatches, baseline replacement, Kubernetes rollout/provenance, and storage identity/concurrency.

Pinned Alloy configuration validation and Loki configuration verification passed. Kubernetes YAML bundles parsed locally; Bash and PowerShell smoke scripts passed syntax checks. `git diff --check` passed.

## Live Docker proof

Rebuilt and restarted M1 with the changes. Kept real payment traffic running throughout baseline collection, faulty deployment, restoration, and recovery validation.

- Healthy baseline: five samples over 60 seconds, v1, zero errors, latency threshold 12.11 ms.
- Captured incident: `M1-FIX-20261008-1012`, faulty v2, P95 577.72 ms, 5xx ratio 0.010095, 20 selected logs.
- Restored payment-service v1. Immediate validation returned retryable 409 `recovery_pending`.
- Completion timestamp: `2026-10-08T10:13:03.5233399Z`.
- First successful validation: recovered=true, slo_restored=true, after P95 9.54 ms, after 5xx ratio zero, elapsed 134.27 seconds.
- Its validation window: `10:14:37.429Z` to `10:15:07.429Z`, three samples, v1 throughout.
- Real API rejected future completion and unknown scenario with HTTP 422.
- Final smoke passed: about 81.38 requests/sec, P95 9.53 ms, v1, recent payment logs; Prometheus/Loki/Grafana healthy.

The stored before/after record is available at `GET /internal/recovery/M1-FIX-20261008-1012` on M1 and remains in its Docker evidence volume. The payment demo was restored to healthy v1.
After deploying the final baseline-pinning changes and restarting M1, the stored result and its three-sample validation window were still readable. The 360-second drill traffic run sent 25,335 requests; its failures include the deliberate faulty deployment and transient connections during container replacement.
The final healthy traffic check sent 4,676 requests, all successful. Smoke passed on the final image at 80.16 requests/sec and 9.53 ms P95. Recovery was validated again successfully; subsequent validations update the stored record with their latest measurement window and elapsed time.

## Runtime limits

This machine has no configured Kubernetes context. Cluster manifests and configurations are implemented and validated locally, but live installation, PVC binding, Kubernetes evidence/RBAC, and traffic-spike scaling still require a cluster run. Scaling recovery logic passed automated tests. Compose honestly remains degraded for unavailable Kubernetes evidence; idle telemetry requires fresh payment traffic.
