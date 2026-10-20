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
| Hash-chained audit trail | Silent edits or deletions in the audit trail | M4 | `NEXUS_AUDIT_HMAC_KEY` (unset: SHA-256; set: HMAC-SHA256) |
| Personal approver tokens | Every approval recorded under one shared identity | M4 | `NEXUS_APPROVER_TOKENS=name:secret,...`, `NEXUS_REQUIRE_NAMED_APPROVERS` (false) |
| Authenticated reads | Anyone on the network reading incident data | M4 | `NEXUS_REQUIRE_READ_AUTH` (false; the demo scripts read through the Kubernetes API proxy), `NEXUS_READ_TOKEN` |
| Leader election | A second M4 replica crash-looping; no standby to take over | M4 | `M4_LEADER_ELECTION` (true on PostgreSQL), `M4_LEADER_RETRY_SECONDS` (2) |
| Durable M2 handoff | A restart forgetting incidents not yet delivered to M4 | M2 | `M2_STATE_PATH` (next to the evidence log; `/data/candidates.json` in Kubernetes) |
| Configured service onboarding | Every service measured on payment-service's `/pay` route | M1, M3 | `M1_SERVICE_ROUTES=service=/route,...`, `M1_SERVICE_HEALTH_URLS`, `M3_HEALTH_SERVICE` |

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
measured by M1 on `/checkout`, and watched by M2 with the threshold detector (the frozen z-score reference is
trained on payment-service only). When load on checkout overloads payment-service, M4 proposes scaling
payment-service and escalates the checkout incident instead of scaling the caller.

## What is still future work

GitOps-driven rollbacks (pull requests instead of direct actuation), coordinated HPA control beyond
`raise-min`, service-mesh mTLS, seasonal/learned baselines, multi-tenancy and causal (topology-wide) RCA.
