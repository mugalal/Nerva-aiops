# Actual Kubernetes integration evidence — 2026-10-09

The services used real HTTP providers, real Prometheus/Loki/Kubernetes evidence,
and M4's actual Kubernetes actuator in the isolated three-node `kind-nexus`
cluster, namespace `nexus-demo`. Both incidents were manually flagged from M1's
observed metrics, explicitly labelled `manual-m1-observation`. This does not
prove trained M2 detection.

| Flow | Actual action | Measured result | M4 outcome |
| --- | --- | --- | --- |
| `INC-K8S-ROLLBACK-20261009` | v2 → v1 rollback | P95 737.5 → 9.521 ms; 5xx 13.78% → 0%; full recovery hold passed | RESOLVED |
| `INC-K8S-SCALE-20261009` | 1 → 10 replicas | P95 4875 → 12.109 ms in first validation; 5xx 0%; strict 11.928 ms baseline ceiling exceeded | ESCALATED |

The scaling result is **not** reported as SLO recovery. All three first hold
points were healthy (9.625, 9.799, 9.971 ms), but the additional current snapshot
was 12.109 ms, above the pinned ceiling. A later unchanged-load check was also
negative (21.048 ms current snapshot). M4 retained the initial escalation.
`recovery.json` preserves the first result; `recovery-later.json` records the
later M1 measurement. No thresholds were loosened, caller recovery booleans
were used, or incident state overwritten to claim success.

## Rollback

`baseline.json` and `rollback/evidence.json` contain the original six-sample,
75-second healthy v1 baseline; its latency ceiling is 27.348 ms. The proposal
selected the actual captured previous version. Capture at 09:40:33.956 UTC
preceded action start at 09:40:34.677 and completed rollout at 09:40:41.582.
The three recovery samples cover 09:41:55–09:42:25, and every one-minute metric
lookback is entirely after action completion. Both recovered and slo_restored
are true.

The same 10-RPS generator ran through fault injection, rollout and recovery.
Its continuous log/counters support load continuity; a separate generator Pod
UID was not recorded for this first run. The deployment changed only for the
deliberate v2 fault and guarded rollback.

## Scaling

The initial 90-RPS attempt exposed synchronous payment probes starving behind
busy payment workers, causing readiness/liveness failures. The probe fix was
tested and deployed **before** the new baseline. A 60-RPS calibration remained
healthy and was not treated as an incident. The final 90-RPS generator was then
started and kept unchanged through diagnosis, scaling and both validations.

`scale-baseline.json` is the new six-sample/75-second healthy 10-RPS v1 baseline,
measured at 09:51:57 before the anomaly. Its latency ceiling is 11.928 ms.
The payment image is `nexus/payment-service:v1-probe-safe`; all ten pods report
v1, 20000 processing iterations, identical image IDs and zero restarts. CPU
request/limit remain 100m/500m and memory request/limit 128Mi/512Mi throughout
this run. The original baseline pod remains among the scaled replicas.

M3 returned traffic_spike, confidence 1.0, component payment-service:v1.
M6 used actual Kubernetes resources and offered 2/HIGH, 8/MEDIUM and 10/LOW
replicas. M4 proposed 10/LOW and completed the real rollout at 09:56:34.966.
Every replica received real Service traffic (approximately 8.44–9.98 RPS in the
recorded query). The generator UID matches its before/after identity files.

The generator was configured for 90 RPS with a 300-request in-flight bound.
Overload caused 3507 dropped scheduling slots and the run had 260 scheduler
skips; therefore it did not offer exactly 90 RPS throughout. Actual offered
rate rose from roughly 67 RPS during overload to roughly 90 RPS after scaling;
load was not reduced to obtain recovery. All 44660 submitted requests completed
successfully. The full-run generator P95 includes overload and is not the
post-action P95; use M1's saved measurement windows for recovery assessment.

## Environment and final state

Alloy's old containerd extraction was corrupted by the earlier disk-full run.
For this local proof its verified official v1.20.1 binary was repackaged on a
fresh working image chain (`nexus/alloy:verified`) without deleting persistent
logs or changing collection configuration. The repository manifest retains the
official upstream image. All three collectors were Ready and M1 read fresh
payment logs.

The fixed payment deployment was restored to one healthy v1 replica after the
proof, and test generators stopped. Fresh telemetry smoke checks passed while
steady traffic was running. Idle demo metrics can become unavailable after load
stops; generate payment traffic before rerunning telemetry smoke checks.

M4 workflow/anomaly/audit state is in memory; M1 baseline/evidence/recovery data
is persisted on its PVC. Jenkins execution remains disabled until its job can
enforce the same live-state preconditions. No main branch merge was performed.
