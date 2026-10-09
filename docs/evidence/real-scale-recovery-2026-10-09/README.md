# Actual Kubernetes scale and SLO recovery — 2026-10-09

`INC-K8S-SCALE-RECOVERY-20261009` passed measured recovery after M4's actual
Kubernetes SCALE action from one to ten payment replicas. M1 returned
`recovered=true` and `slo_restored=true`; M4 returned `RESOLVED`. The workflow
used the real M1/M3/M4/M6 service APIs and Prometheus/Loki/Kubernetes evidence
in the isolated `kind-nexus` cluster, namespace `nexus-demo`.

The anomaly was explicitly labelled `manual-m1-observation` and copied from
M1's observed metrics. Its score is an operator flag. This run does not prove
trained M2 detection.

| Measurement | Saved result |
| --- | --- |
| Healthy baseline | 30 RPS, one v1 replica, six samples over 75 seconds |
| Pinned latency/5xx ceilings | 12.064922148681125 ms / 0.01 |
| Incident capture | P95 4875 ms, 0% 5xx, one replica |
| Actual action | SCALE to 10; completed 10:37:41.487744 UTC |
| Recovery snapshot | P95 9.892133292678936 ms, 0% 5xx, 89.136 RPS, ten replicas |
| Recovery hold P95 | 9.866889 / 9.890276 / 9.892133 ms |
| Official validation | 10:42:19.543096 UTC; both recovery flags true; RESOLVED |

## Baseline, action and validation timing

[baseline.json](baseline.json) was measured at 10:35:10.222585 UTC, before
the incident's 10:37:07.782 timestamp. Its healthy window was
10:33:34.036604–10:34:49.036604 UTC, with average observed rate 30.001 RPS.
The same baseline is pinned in the incident
[evidence.json](INC-K8S-SCALE-RECOVERY-20261009/evidence.json); no threshold was
loosened after capture. Capture at 10:37:11.335918 preceded the
[action](INC-K8S-SCALE-RECOVERY-20261009/action.json), which began at
10:37:30.822545 and completed at 10:37:41.487744 UTC. The
[proposal](INC-K8S-SCALE-RECOVERY-20261009/proposal.json) selected ten replicas
with LOW risk.

The first official recovery validation was **manually initiated approximately
278 seconds after action completion**. The saved
[recovery.json](INC-K8S-SCALE-RECOVERY-20261009/recovery.json) reports elapsed
time 278.055352 seconds. This is the validation request's timing, not a measured
minimum recovery time. The 30-second hold covers
10:41:48.692–10:42:18.692 UTC; its one-minute metric lookbacks are entirely
after action completion. All three points and the latest snapshot meet the
pinned ceilings, retain v1, report ten replicas and sustain roughly 89 RPS.
[recovery-incident.json](INC-K8S-SCALE-RECOVERY-20261009/recovery-incident.json)
records M4's final `RESOLVED` state.

This run establishes the actual scale and measured SLO recovery with that
manual timing. It does not yet prove that the launcher's `-Approve` path handles
an initial negative recovery measurement. M4's bounded stabilization policy
has since passed its 103-test suite and both controlled HTTP flows: a negative
measurement remains pending within the pinned deadline, and timeout produces
a cached terminal escalation. A fresh real `-Approve` run of that policy is
pending and must be reported separately. The default M4 deadline is 360 seconds
after action completion; the launcher's separate default polling limit is
600 seconds. See [the integration guide](../../REAL_SERVICE_INTEGRATION.md)
for the full policy and configuration bounds.

## Workload identity and continued traffic

Payment used `nexus/payment-service:v1-recovery-safe`, version v1, with
`PAYMENT_WORK_ITERATIONS=20000`. CPU request/limit stayed 100m/500m and memory
request/limit stayed 128Mi/512Mi. The Pod template is identical in
[deployment-baseline.json](deployment-baseline.json),
[deployment-before.json](deployment-before.json) and
[deployment-after.json](deployment-after.json); only replicas changed.

The [baseline Pod](payment-pods-baseline.json),
`payment-service-5d9466d7dd-6p6f7`, retains UID
`3eca47c9-1ab8-4e54-aa71-a9b04936c032` among the
[ten final Pods](payment-pods-after.json). All are Running and Ready with zero
restarts and the same exact image digest
`sha256:45c1185b7dde5e902b487e8907ed74f621cb9ecd6b246ecf603c9d484ff99f93`.
[Direct per-Pod health](payment-health-after.json) confirms all ten report
healthy v1 and the same 20000 processing iterations.

The baseline generator's before/after files retain UID
`3e8046f5-c2f5-4e98-be4e-d24a66a7c323`; its final summary records 5383 completed
HTTP 200 responses. It drained before the separate spike generator started.
The spike generator's
[before](integration-recovery-spike-traffic-before.json) and
[after](spike-traffic-after.json) identity files retain UID
`ef72a1f4-cd95-4059-b822-0fae7c32582c`, unchanged 90-RPS settings, zero restarts
and Running state. It used payment Service DNS throughout scaling and validation.
The [per-Pod query](per-pod-request-rate.json) records positive request rates
for every replica, approximately 8.56–9.93 RPS each.

[spike-traffic.jsonl](spike-traffic.jsonl) records 52456 submitted and completed
requests, all HTTP 200, with zero transport errors. It also records **4012
capacity drops and 158 scheduler skips**. Consequently the full-run offered
rate was 83.373 RPS, not a constant achieved 90 RPS. At validation, completed
server traffic was roughly 89 RPS and nearby generator intervals offered
89.898/89.399 RPS with no failed completions. Load settings were not reduced
after the action. The cumulative client P95 is 4947 ms because it includes
overload; interval client P95 and M1's server histogram show current behavior
separately.

## Telemetry and logging checks

[snapshot-history-consistency.json](snapshot-history-consistency.json) records
identical snapshot and historical metrics at the shared evaluation time
10:45:46.182 UTC. Raw scrape freshness checks remained enabled.
The saved [log-drop counter query](dropped-log-records.json) returned zero;
this single saved query is not a guarantee of complete log delivery for the
whole run. Bounded structured logging and interval traffic reporting were
deployed before the new baseline.

The [earlier failed scale proof](../real-integration-2026-10-09/README.md)
remains factual and unchanged. This directory records a separate passing
incident with a fresh baseline and the corrected runtime.
