# M1, M3, M4 and M6 integration

The integration branch connects the actual service APIs. M3 diagnoses an incident
from its linked anomaly and M1's read-only evidence preview. M4 asks M6 for scale
options only when RCA identifies a traffic spike. M4 captures incident evidence
before presenting an executable proposal, executes the approved action, and polls
M1's measured recovery before marking the incident RESOLVED.

```text
Observed anomaly -> M4 incident + linked anomaly
                         |
                         v
                       M3 RCA -> M1 preview (metrics, deployment, logs, baseline)
                         |
                         v
                       M4 decision -> M6 resources + scale risk (for traffic)
                         |
                         v
                       M1 capture -> proposal -> approval -> Kubernetes action
                         |
                         v
                       M1 post-action recovery -> M4 RESOLVED or ESCALATED
```

No incident ID or scenario is required to preview M1 evidence. This avoids the
previous circular dependency where RCA needed evidence that M4 could capture
only after RCA selected a scenario. Existing frozen contracts and mock fixtures
remain unchanged. M1 preview/resource metadata and M4 anomaly ingress are internal
extensions.

## Run the services

All image builds use the repository root as context:

```powershell
docker build -t nexus/telemetry-intelligence:dev -f services/telemetry-intelligence/Dockerfile .
docker build -t nexus/root-cause-analysis:dev -f services/root-cause-analysis/Dockerfile .
docker build -t nexus/shared-nexus-api:dev -f services/shared-nexus-api/Dockerfile .
docker build -t nexus/finops-engine:dev -f services/finops-engine/Dockerfile .
```

Compose starts the connected HTTP services with real data providers:

```powershell
docker compose -f observability/docker-compose.yml --profile integration up -d --build
```

Compose's default remediation actuator is explicitly **mock**. Its payment CPU
limit is 1000m and memory limit 512 MiB, with explicit demo request assumptions
100m/128 MiB supplied to M6. These assumptions are needed because Compose has no
Kubernetes resource requests. A Compose action therefore does not prove a real
scale or rollback.

For actual Kubernetes execution, load the built images into your selected
cluster, deploy the payment/observability/M1 manifests, then apply:

```powershell
# Set these to your own isolated cluster; do not implicitly select another one.
$env:KUBECONFIG = Join-Path (Get-Location) '.review-branches/kubeconfig'
$demoKubectl = Join-Path (Get-Location) '.review-branches/tools/kubectl.exe'
& $demoKubectl --context kind-nexus apply -f services/root-cause-analysis/k8s/root-cause-analysis.yaml
& $demoKubectl --context kind-nexus apply -f services/finops-engine/k8s/deployment.yaml
& $demoKubectl --context kind-nexus apply -f services/shared-nexus-api/k8s/shared-nexus-api.yaml
```

M4's Kubernetes manifest uses the actual actuator and a namespaced service
account limited to the payment deployment. M1 remains read-only. M3 and M6 have
no service-account token. Cluster service DNS uses ports 8001/8003/8004/8006;
URLs are configured in the manifests and Compose file.

Open M1 and M4 port forwards in separate terminals:

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo port-forward service/telemetry-intelligence 18001:8001 --address 127.0.0.1
```

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo port-forward service/shared-nexus-api 18004:8004 --address 127.0.0.1
```

Measure a healthy baseline before incident creation. See
[the measured-load drill](M1_K8S_SCALING_DRILL.md) for baseline quality checks,
in-cluster Service traffic, and preserving offered load through recovery.
Use a healthy rate appropriate to the host; a failed baseline is rejected.
Never weaken the quality ceiling to make an unhealthy baseline pass.

## Send an observed incident

M2/detector or an operator must supply a fresh actual-shaped `AnomalyEvent`.
Automatic trained M2 detection is not established by this integration. For a
manual demo, observe an actual failure in M1's snapshot and explicitly label
the event `model: manual-m1-observation`; copy its timestamp and metric values.
The score is the operator's flag, not a measured ML confidence.

Save the event as JSON with exactly `anomaly_id`, `timestamp`, `service`, `score`,
`severity`, `model`, and `features`.

`features` contains exactly `request_rate`, `latency_p95_ms`, `http_5xx_rate`,
and `cpu` from the observed snapshot. Do not copy memory/replica metadata into
that object; M3 reads those separately from M1's real preview. Then run:

```powershell
.\scripts\run-integration-incident.ps1 -AnomalyPath .review-branches/observed-anomaly.json
```

This creates/links the incident, asks real M3 to diagnose it, asks real M6 to size
it when appropriate, and saves the decision/proposal. `-Approve` also executes
the saved proposal and polls recovery. Execution should be used only in a demo
where the service can be scaled or rolled back.

The equivalent API sequence is:

1. `POST /api/incidents/` with ID, severity, affected_services and started_at.
2. `POST /internal/anomalies?incident_id=...` with the observed event.
3. `POST /internal/decisions/build/{incident_id}`; read
   `GET /api/incidents/{incident_id}/proposal`.
4. `POST /api/incidents/{incident_id}/approve`; save its frozen ActionResult.
5. `POST /internal/recovery/validate/{incident_id}` until it stops returning
   HTTP 202. Pending measurements leave the incident VALIDATING.

Real mode rejects caller-supplied recovery booleans. Recovery must report both
`recovered=true` and `slo_restored=true` from M1. M1 measures a complete
post-action hold after metric lookbacks have flushed. Scale recovery also
requires increased replicas, continued traffic and the unchanged captured
version. Rollback recovery requires the captured previous version.

## Safety and current limits

- M3 checks incident/anomaly/service/version/time identity and measured baseline
  coverage. Missing mandatory providers fail clearly; incomplete optional evidence
  yields an unknown diagnosis. Real mode never silently substitutes mock data.
- M6 uses actual deployment requests/limits from M1 and uncapped utilization to
  calculate risk. Its displayed observed CPU percentage remains capped at 100
  to preserve the frozen response contract. M4 selects only increasing LOW-risk
  options at most 10 replicas; insufficient safe capacity is escalated.
- Kubernetes actions validate stored proposal state and wait for completed
  rollout. Stale deployments/proposals fail instead of applying an unrelated
  rollback or decreasing replicas. Concurrent service actions and external edits
  are guarded by service locking and Kubernetes resource-version preconditions.
- The legacy Jenkins job cannot enforce these live preconditions, so the executor
  rejects that actuator mode. Jenkins remediation needs its own guarded job.
- M1 evidence/baselines/recovery persist on its PVC. M4's incident/anomaly/audit
  stores remain in memory; restarting M4 loses workflow state. Multi-worker or
  multi-instance durable coordination requires a shared database implementation.
- The controlled HTTP regression harness starts actual M1/M3/M4/M6 processes
  with real HTTP providers, test-only M1 telemetry fixtures and a mock actuator.
  It proves contracts, ordering, pending recovery and resolution, not physical
  cluster telemetry or action execution. Live cluster evidence is reported
  separately.

## Verification

The final suites passed: M1/payment 64, M3 45, M4 93, M6 83 (one database-only
check skipped), and two controlled four-process HTTP flows. PowerShell launcher
syntax/UTC timestamp conversion and Compose configuration passed. The existing
M1 smoke check passed against actual Kubernetes, Prometheus and Loki.

The payment concurrency regression saturates the only worker with a 500 ms
payment and requires health/readiness/metrics/version to respond within 200 ms.
Those probes now run asynchronously so payment thread-pool overload cannot
prevent the telemetry needed for a safe scaling decision.

Live evidence and limitations are recorded in
[the Kubernetes integration proof](evidence/real-integration-2026-10-09/README.md).
