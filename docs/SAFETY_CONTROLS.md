# NERVA safety controls: what they do and how to configure them

Every control below is on by default unless stated otherwise, is covered by
automated tests, and is visible to the operator in the Incident Center
(summary notes, the Decide panel and the Audit trail panel).

| Control | What it prevents | Where | Setting (default) |
| :--- | :--- | :--- | :--- |
| Failed-diagnosis escalation | An incident stuck forever in DIAGNOSING after M3/M6/M1 fail | M4 | `M4_DIAGNOSIS_MAX_ATTEMPTS` (20); a non-retryable error escalates at once |
| Recovery latency floor | Healthy noise failing recovery (1.25 x a ~10 ms baseline sits inside one histogram bucket) | M1 | `M1_BASELINE_LATENCY_FLOOR_MS` (25) |
| Scale-aware recovery deadline | A large scale-out timing out while pods are still starting | M4 | `RECOVERY_TIMEOUT_SECONDS` (360) + `RECOVERY_SECONDS_PER_ADDED_REPLICA` (15), capped at 1 h |
| Incident coalescing and cooldown | One incident (and one proposed action) per alert burst on a flapping service | M4 | `M4_COALESCE_INCIDENTS` (true), `M4_INCIDENT_COOLDOWN_SECONDS` (300) |
| Uncapped latency measurement | An overload p95 reading as exactly 5000 ms | payment-service | 10 s and 30 s histogram buckets |
| Autoscaler guard | NERVA and a HorizontalPodAutoscaler fighting over replicas | M4 | `M4_HPA_POLICY`: `refuse` (default), `raise-min`, `ignore` |
| Dependency guard | Scaling a caller while the service it depends on is the bottleneck | M4 | registry `depends_on` (config/services.yaml) |
| Measured scale-down | Capacity only ever going up after spikes (the one-way ratchet) | M4 + M1 | `M4_SCALE_DOWN_ENABLED` (true with real telemetry), `M4_SCALE_DOWN_WINDOW_SECONDS` (600), `M4_SCALE_DOWN_QUIET_SECONDS` (600), `M4_SCALE_DOWN_LOAD_FACTOR` (1.25), `M4_SCALE_DOWN_MAX_UTILIZATION` (0.6); registry `max_scale_down_step` (3) |
| CPU throttling evidence | A CPU limit starving a pod while average CPU looks moderate | payment-service, M1, M3 | exported automatically from the container cgroup |
| Hash-chained audit trail | Silent edits or deletions in the audit trail | M4 | `NEXUS_AUDIT_HMAC_KEY`: **set in Kubernetes** (HMAC-SHA256, from the `nexus-runtime` secret); unset elsewhere means plain SHA-256. Records written before the key existed stay verifiable as SHA-256. |
| Personal approver tokens | Every approval recorded under one shared identity | M4 | `NEXUS_APPROVER_TOKENS=name:secret,...` (optional key in `nexus-runtime`), `NEXUS_REQUIRE_NAMED_APPROVERS` (false). The website still approves with its own service identity (`M5_OPERATOR_ID`); personal tokens are for API/CLI approvals. |
| Authenticated reads | Anyone on the network reading incident data | M4, M3, tooling | **On in Kubernetes** (`NEXUS_REQUIRE_READ_AUTH=true`). M3 reads with a dedicated read-only `NEXUS_READ_TOKEN` (least privilege: it never holds the ingest token); M5 uses its service token; the demo tooling reads from inside the M4 pod. Probes (`/live`, `/health`, `/ready`) stay open. Off by default in Docker Compose. |
| Leader election | A second M4 replica crash-looping; no standby to take over | M4 | `M4_LEADER_ELECTION` (true on PostgreSQL), `M4_LEADER_RETRY_SECONDS` (2) |
| Durable M2 handoff | A restart forgetting incidents not yet delivered to M4 | M2 | `M2_STATE_PATH` (next to the evidence log; `/data/candidates.json` in Kubernetes) |
| Configured service onboarding | Every service measured on payment-service's `/pay` route | M1, M3 | `M1_SERVICE_ROUTES=service=/route,...`, `M1_SERVICE_HEALTH_URLS`, `M3_HEALTH_SERVICE` |

## How M3 diagnoses (learned evidence, rule gates, calibrated confidence)

* **Evidence is learned.** Every signal is measured in usual deviations from the service's normal: M2's learned
  noise shape (trained on real captures) anchored at the operating point M1 measured just before the incident.
  The training environment's traffic level is not assumed to hold in the demo environment (payment-service was
  captured at ~85 req/s per replica; the cluster demo runs at 10).
* **Decisions are gated by explicit rules.** A release is blamed only if it changed the version within five
  minutes and the live telemetry shows it; traffic is never blamed right after a release; a release during a
  traffic surge is ambiguous; a signal counts as degraded only when it is far from normal *and* past the SLO
  limit M1 judges recovery by; incomplete evidence never yields a confident cause.
* **Confidence is calibrated** (`services/root-cause-analysis/scripts/calibrate.py`, `app/calibration.json`):
  a logistic map fitted on real captures 1, 2, 4, 5 with leave-one-capture-out normals, evaluated on held-out
  captures 3 and 6: Brier 0.011 (release) and 0.010 (traffic), precision 1.0 at the 0.6 decision threshold,
  recall 0.95 and 0.97; readings predicted 0.8-1.0 were faults 19/19 and 31/31.

## Audit endpoints

* `GET /internal/remediation/audit/verify`: recomputes the whole hash chain.
* `GET /internal/remediation/audit/export?format=json|csv&incident_id=...`: downloads the trail with the verification attached.
* The Incident Center shows the chain status and a **Download audit trail** button (proxied by M5).

What the chain proves: an edited record, or a record removed from the middle, is detected. It cannot detect records cut off the end of the trail; keep an exported copy for that.

## Seeing a scale-down in the Kubernetes demo

Right-sizing runs only with real telemetry (Path B). After Demo 2 (scale-out) resolves, leave the steady
traffic running. About 10 minutes later (`M4_SCALE_DOWN_QUIET_SECONDS` after the incident closed, with a full
`M4_SCALE_DOWN_WINDOW_SECONDS` of stable, healthy measurements), a `RIGHTSIZE-...` incident appears awaiting
approval, for example "Scale down payment-service from 7 to 4 replicas". Approve it like any other proposal;
M1 then checks that the SLO held on fewer replicas. The one-command demo scripts reset the replica count after
each run, so use the manual Demo 2 steps if you want to show this.

## Two services

`checkout-service` (apps/demo-microservices/k8s/checkout-service.yaml) runs the same instrumented demo app
and calls payment-service on every `POST /checkout`. It is registered with `depends_on: [payment-service]`,
measured by M1 on `/checkout`, and scored by M2's per-service z-score with its own reference, trained on two real
captures from the cluster and evaluated on a third held-out capture (precision 1.0, recall 1.0, 0 of 43 false
alarms; see docs/evidence/LIVE_DRILLS_2026-10-20.md). When load on checkout overloads payment-service, M4 proposes scaling
payment-service and escalates the checkout incident instead of scaling the caller.

## What is still future work

GitOps-driven rollbacks (pull requests instead of direct actuation), coordinated HPA control beyond
`raise-min`, service-mesh mTLS, seasonal/learned baselines, multi-tenancy and causal (topology-wide) RCA.
