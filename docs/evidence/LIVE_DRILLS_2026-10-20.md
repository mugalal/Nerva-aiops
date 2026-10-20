# Live drills on the kind cluster, 2026-10-19/20

All results below were measured on the local kind cluster `kind-nexus` (3 nodes, namespace `nexus-demo`), with
real Kubernetes actuation (`REMEDIATION_BACKEND=kubernetes`), PostgreSQL workflow storage and M1 measuring
recovery from Prometheus. Every approval went through the website gateway (M5) with the operator token.
Raw evidence (incident contexts, traffic-pod specs) is in `.review-branches/demo-runs/` on the drill machine.
Times are UTC; "T+" is relative to fault injection.

## 1. Traffic spike, single service (scale out)

`INC-20261019-234955-payment-service`

| Step | Result |
| --- | --- |
| Healthy baseline (M1) | 10.0 req/s, p95 9.50 ms, **recovery ceiling 25.00 ms** (the latency floor; the old 1.25x rule gave ~11.9 ms, inside histogram noise) |
| Injection | +150 req/s on 1 replica |
| Detected (M2) | T+12 s |
| Under load (M1) | p95 **9,697 ms** (previously clamped at 5,000 ms by the top histogram bucket); CPU throttled in **92.6%** of CFS periods |
| Diagnosis (M3) | `traffic_spike`, confidence 1.0; evidence cites the measured baseline and "CPU limit throttled the service in 100% of CFS periods" |
| Proposal (M4, after the 60 s settle) | SCALE to 8 replicas, risk MEDIUM |
| Approval through the website | without operator token: **HTTP 401**; with token: HTTP 200 |
| Recovery (M1, measured) | **RESOLVED**: p95 9,750 ms -> 9.7 ms, 90 s after the action |

## 2. Measured scale-down (right-sizing)

`RIGHTSIZE-20261020-000147-payment-service` (drill run with the window and quiet period shortened to 120 s / 60 s;
the defaults are 600 s / 600 s)

| Step | Result |
| --- | --- |
| Proposal | appeared 132 s after the spike stopped: **8 -> 6 replicas**, sized from measurements (peak 73.8 req/s still in the window, 10.0 req/s per replica at baseline, projected CPU 16%) |
| Approval through the website | 401 without token, 200 with token |
| Recovery (M1 `scale_down` scenario) | **RESOLVED**: p95 9.52 -> 9.50 ms on fewer replicas, 91 s |

With the defaults restored, three further right-sizing proposals (6 -> 3) were made: two **expired and were
withdrawn without any action**, one was **rejected** through the website (no infrastructure change, no memory
record).

## 3. Writer fencing and leader election

Each M4 restart (image rollouts and setting changes) re-elected the single writer on PostgreSQL and incremented
the fencing generation (observed 17, 18, 19 and 21 across the day's restarts), so a stale writer can no longer
commit. `/ready` reported
`status ok, standby false` after every takeover.

## 4. Two services: checkout-service -> payment-service

Load: 5 req/s straight to payment-service plus 5 checkouts/s (each checkout calls payment-service), then a surge
of +60 checkouts/s on checkout-service only.

**First run (finding).** checkout-service did the same CPU-heavy work as payment-service, so it became its own
bottleneck (p95 9,009 ms, throttled 100%) and payment-service was barely loaded (p95 41 ms, M3: `unknown`).
checkout-service was diagnosed `traffic_spike` but escalated with the generic "No safe automated action
available" because M6 offered only a HIGH-risk step: **the escalation did not say that a dependency was
involved**. Fixed in M4 (`3f17d90`): the dependency check now runs before any capacity costing and names the
struggling dependency; checkout-service became a light front service so payment-service carries the load, as
in a real checkout -> payment chain.

**Second run** (`INC-20261020-011015-*`):

| | payment-service | checkout-service |
| --- | --- | --- |
| Detected (M2, independently) | T+22 s | T+22 s |
| Diagnosis (M3) | `traffic_spike` | `traffic_spike` (0.75) |
| Decision (M4) | **SCALE to 7** | **ESCALATED by the dependency guard**: "checkout-service depends on payment-service, which has open incident INC-20261020-011015-payment-service. Adding checkout-service replicas would add load to the struggling dependency; fix payment-service first." |
| Approval | website: 401 without token, 200 with token | none (not scaled) |
| Result | **RESOLVED**: p95 543 ms -> 9.6 ms, measured by M1 in 91 s | unchanged, 1 replica |

The audit hash chain verified after every drill (29 chained records after the second run).

## 5. A trained M2 reference for checkout-service

Until now checkout-service fell back to the static threshold detector, which the synthetic benchmark shows
missing 40% of traffic spikes. Following the same method as payment-service (two training captures, one held
out), three real captures were recorded on the cluster: 5 checkouts/s healthy, then +60 checkouts/s at a
recorded instant.

| Capture | Use | Readings |
| --- | --- | --- |
| `capture4-checkout.json` | training | 57 |
| `capture5-checkout.json` | training | 45 |
| `capture6-checkout.json` | **held out**, recorded after the training data was frozen | 49 |

Training (`python -m app.realdata train capture1 capture2 capture4-checkout capture5-checkout`) kept the alert line
at 48.9 wobbles; checkout's noisiest normal reading was 4.7 wobbles and its quietest settled fault 1,199.
**payment-service is unchanged**: its statistics and the alert line are byte-identical, and its held-out
capture3 result is identical (precision 1.0, recall 1.0, 0 of 64 false alarms).

Held-out result on capture6:

| Detector | Precision | Recall | False alarms | Worst delay | Separation (AUC) |
| --- | --- | --- | --- | --- | --- |
| Trained per-service z-score (now deployed) | 1.000 | 1.000 | 0 of 43 | 0 s | 1.000 |
| Static threshold fallback (before) | | | | | 0.594; only 19% of fault readings above the normal maximum |

Limits: one fault shape (a traffic surge) at one load level; a bad release on checkout-service was not captured.

## 6. Rehearsals with the real demo scripts (`-Rehearse`)

* **Faulty release** (`scripts/demo-faulty-deployment.ps1 -Rehearse`): `INC-20261020-023257-payment-service`,
  `faulty_deployment` (0.9), ROLLBACK v2 -> v1 approved through the website (401 without token, 200 with),
  **RESOLVED**: p95 737 -> 9.5 ms, 5xx 16.0% -> 0.0%, 91 s; the script reset itself.
* **Two-service cascade** (`scripts/demo-cascade.ps1 -Rehearse`): payment-service detected after 12 s, scaled and
  RESOLVED. checkout-service was detected only ~3 minutes after the surge started, after payment had recovered,
  so M3 found nothing left to explain and it escalated as `unknown` (not by the dependency guard, which did
  apply in run 4). The late checkout detection is open and being investigated.

## 7. M3 on the recorded incidents (replay)

The learned-evidence M3 (section on M3 in docs/SAFETY_CONTROLS.md) was replayed on every recorded live incident
with saved evidence: same root cause in all five; confidences are now calibrated (faulty release 0.9 -> 0.98,
cascade payment 0.75 -> 1.0). A regression test pins the live spike (78 req/s on one replica, 10 req/s
operating point), which a naive use of the training-environment normal would have scored as zero load.

## 8. What these drills do not show

* Faults in more than two services at once: each incident is diagnosed on its own; there is no cross-service
  correlation into one root incident.
* Cloud conditions (zones, node loss, network partitions): this is a local cluster.
* Cost in money: M6 reports abstract cost units, not provider prices.
