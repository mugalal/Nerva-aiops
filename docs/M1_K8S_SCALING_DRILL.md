# Real Kubernetes scaling drill

Run from `E:\AIOPS\final project` after the local `kind-nexus` cluster and its
payment, M1, Prometheus, Loki, and Alloy workloads are ready. Use the matching
kubectl downloaded into `.review-branches/tools/kubectl.exe` and the isolated
`.review-branches/kubeconfig`; this guide does not select a different cluster.

Load runs inside Kubernetes through payment Service DNS. A host
`kubectl port-forward service/payment-service` selects one pod and cannot prove
Service balancing across replicas.

## Open provider ports in three separate PowerShell terminals

Run the setup lines in each terminal, followed by its port-forward command. Keep
the three terminals open while running the drill from a fourth terminal.

```powershell
Set-Location 'E:\AIOPS\final project'
$env:KUBECONFIG = Join-Path (Get-Location) '.review-branches/kubeconfig'
$demoKubectl = Join-Path (Get-Location) '.review-branches/tools/kubectl.exe'
```

Terminal 1, M1:

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo port-forward service/telemetry-intelligence 18001:8001 --address 127.0.0.1
```

Terminal 2, Prometheus:

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo port-forward service/prometheus 19090:9090 --address 127.0.0.1
```

Terminal 3, Loki:

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo port-forward service/loki 13100:3100 --address 127.0.0.1
```

These ports are separate from the Compose stack's usual ports. Loki's readiness
URL is `http://localhost:13100/ready`; Prometheus uses
`http://localhost:19090/-/ready`. M1 uses `http://localhost:18001/health`.

## Configure the workload before measuring baseline

Run these commands in the fourth terminal. Fixed processing work is opt-in and
defaults to zero. The configured 20000 iterations run the same CPU computation
for every payment. Keep the image, work setting, fault settings, version, and
CPU/memory limits unchanged from baseline through recovery.

```powershell
Set-Location 'E:\AIOPS\final project'
$env:KUBECONFIG = Join-Path (Get-Location) '.review-branches/kubeconfig'
$demoKubectl = Join-Path (Get-Location) '.review-branches/tools/kubectl.exe'
$m1Url = 'http://localhost:18001'
$demoStamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$demoIncidentId = "INC-K8S-SCALE-$demoStamp"
$demoOutput = Join-Path (Get-Location) "docs/evidence/m1-k8s-$demoStamp"
New-Item -ItemType Directory -Path $demoOutput -Force | Out-Null

# Query each payment Pod directly; a Service health request selects only one Pod.
function Get-VerifiedPaymentPodHealth {
    param([string]$PodName)
    $podHealthPython = @'
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10) as response:
    print(response.read().decode())
'@
    $podHealthJson = $podHealthPython | & $demoKubectl --context kind-nexus -n nexus-demo exec -i $PodName -c payment-service -- python -
    if ($LASTEXITCODE -ne 0) { throw "Health request failed in payment Pod $PodName" }
    $podHealth = $podHealthJson | ConvertFrom-Json
    if ($podHealth.status -ne 'ok' -or $podHealth.version -ne 'v1' -or $podHealth.payment_work_iterations -ne 20000) {
        throw "Payment Pod $PodName must report healthy v1 with payment_work_iterations=20000"
    }
    [PSCustomObject]@{ pod = $PodName; health = $podHealth }
}

& $demoKubectl --context kind-nexus get nodes
& $demoKubectl --context kind-nexus -n nexus-demo get pvc
& $demoKubectl --context kind-nexus -n nexus-demo set env deployment/payment-service PAYMENT_WORK_ITERATIONS=20000
& $demoKubectl --context kind-nexus -n nexus-demo scale deployment/payment-service --replicas=1
& $demoKubectl --context kind-nexus -n nexus-demo rollout status deployment/payment-service --timeout=180s
if ($LASTEXITCODE -ne 0) { throw 'Payment rollout failed' }
& $demoKubectl --context kind-nexus -n nexus-demo create configmap payment-traffic-script --from-file=k8s-traffic.py=scripts/k8s-traffic.py --dry-run=client -o yaml | & $demoKubectl --context kind-nexus apply -f -
```

The payment `/health` response exposes `payment_work_iterations`. Do not change
that value during the action. The M1 service account only reads Kubernetes
evidence; the explicit scale command below performs the remediation.

## Start 30 RPS baseline traffic and measure the healthy window

Build a baseline Pod from the supplied traffic manifest by changing only its
name and load environment. Delete commands here target only previous local
traffic Pods, allowing a repeated drill.

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo delete pod payment-baseline-traffic payment-traffic --ignore-not-found --wait=true
$baselinePod = & $demoKubectl --context kind-nexus create -f infrastructure/k8s/traffic-demo.yaml --dry-run=client -o json | ConvertFrom-Json
$baselinePod.metadata.name = 'payment-baseline-traffic'
($baselinePod.spec.containers[0].env | Where-Object name -eq 'RPS').value = '30'
($baselinePod.spec.containers[0].env | Where-Object name -eq 'DURATION_SECONDS').value = '600'
$baselinePod | ConvertTo-Json -Depth 30 | & $demoKubectl --context kind-nexus apply -f -
& $demoKubectl --context kind-nexus -n nexus-demo wait --for=condition=Ready pod/payment-baseline-traffic --timeout=120s
if ($LASTEXITCODE -ne 0) { throw 'Baseline traffic Pod did not start' }

# 60s lookback + 75s measured window + 20s end offset.
Start-Sleep -Seconds 155
$baselineEnd = [DateTime]::UtcNow.AddSeconds(-20)
$baselineStart = $baselineEnd.AddSeconds(-75)
$baselineBody = @{
    service = 'payment-service'
    start = $baselineStart.ToString('o')
    end = $baselineEnd.ToString('o')
    step_seconds = 15
} | ConvertTo-Json
$m1Baseline = Invoke-RestMethod -Method Post -Uri "$m1Url/internal/baselines/measure" -ContentType 'application/json' -Body $baselineBody
$m1Baseline | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'baseline.json')
$m1Baseline.thresholds

$m1Health = Invoke-RestMethod "$m1Url/health"
$m1Health | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'health.json')
if ($m1Health.status -ne 'ok') { throw 'Resolve the reported M1 dependency before continuing' }
```

The window contains six 15s samples. Every baseline sample must have CPU/memory
ratios at most 0.85, 5xx ratio at most 0.01, P95 at most 500ms, nonzero replicas,
and at least 0.1 requests/sec. The stored latency threshold is the measured P95
statistic multiplied by 1.25. A failed quality check requires a new healthy
measurement; do not loosen thresholds to pass the drill.

## Replace baseline load with the 90 RPS spike

Stop scheduling baseline requests with SIGTERM, drain in-flight work, and export
its final summary before deleting that completed Pod. Start the spike Pod with
its existing 90 RPS/600s settings. Keep this same Pod running through scaling.

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo exec payment-baseline-traffic -- python -c 'import os, signal; os.kill(1, signal.SIGTERM)'
& $demoKubectl --context kind-nexus -n nexus-demo wait '--for=jsonpath={.status.phase}=Succeeded' pod/payment-baseline-traffic --timeout=45s
& $demoKubectl --context kind-nexus -n nexus-demo logs payment-baseline-traffic | Set-Content -Encoding utf8 (Join-Path $demoOutput 'baseline-traffic.jsonl')
& $demoKubectl --context kind-nexus -n nexus-demo delete pod payment-baseline-traffic --wait=true
& $demoKubectl --context kind-nexus apply -f infrastructure/k8s/traffic-demo.yaml
& $demoKubectl --context kind-nexus -n nexus-demo wait --for=condition=Ready pod/payment-traffic --timeout=120s
if ($LASTEXITCODE -ne 0) { throw 'Spike traffic Pod did not start' }
$spikePodBefore = & $demoKubectl --context kind-nexus -n nexus-demo get pod payment-traffic -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $spikePodBefore.status.phase -ne 'Running') { throw 'Spike traffic Pod must be Running' }
$spikePodBefore | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'spike-pod-before.json')
$spikePodUid = $spikePodBefore.metadata.uid
Start-Sleep -Seconds 90

$beforeSnapshot = Invoke-RestMethod "$m1Url/internal/telemetry/snapshot?service=payment-service"
$beforeSnapshot | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'before-snapshot.json')
if ($beforeSnapshot.metrics.latency_p95_ms -le $m1Baseline.thresholds.latency_p95_ms_max) {
    throw 'Spike did not exceed the healthy latency threshold; calibrate load and restart the whole drill before capture'
}
$captureBody = @{
    incident_id = $demoIncidentId
    service = 'payment-service'
    scenario = 'traffic_spike'
} | ConvertTo-Json
$incidentEvidence = Invoke-RestMethod -Method Post -Uri "$m1Url/internal/evidence/capture" -ContentType 'application/json' -Body $captureBody
$incidentEvidence | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'incident-evidence.json')
$deploymentBefore = & $demoKubectl --context kind-nexus -n nexus-demo get deployment payment-service -o json | ConvertFrom-Json
$deploymentBefore | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'deployment-before.json')
$paymentPodsBefore = & $demoKubectl --context kind-nexus -n nexus-demo get pods -l app=payment-service -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read payment Pod statuses before scaling' }
$paymentPodsBefore | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'payment-pods-before.json')
if (@($paymentPodsBefore.items).Count -ne 1) { throw 'Capture requires exactly one payment Pod' }
$paymentPodBefore = $paymentPodsBefore.items[0]
$paymentContainerBefore = $paymentPodBefore.status.containerStatuses | Where-Object name -eq 'payment-service'
$paymentReadyBefore = $paymentPodBefore.status.conditions | Where-Object type -eq 'Ready'
if ($paymentPodBefore.status.phase -ne 'Running' -or $paymentReadyBefore.status -ne 'True' -or -not $paymentContainerBefore.ready -or $paymentPodBefore.metadata.deletionTimestamp) {
    throw 'The captured payment Pod must be Running, Ready, and not terminating'
}
$capturedPaymentImageId = $paymentContainerBefore.imageID
if (-not $capturedPaymentImageId) { throw 'Captured payment Pod has no imageID; cannot verify unchanged code' }
Get-VerifiedPaymentPodHealth $paymentPodBefore.metadata.name | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'payment-health-before.json')
```

30/90 RPS are starting values. CPU performance varies. At three replicas,
90 RPS should distribute near 30 RPS per pod, matching the one-pod baseline.
If one replica does not degrade, or three cannot sustain the spike, calibrate
rates before capturing a new incident and repeat the full measurement. Never
reduce spike traffic or processing work after capture to make recovery pass.

## Scale and validate under unchanged spike load

Only the replica count changes during the action. Save the time after successful
rollout completion, then wait 120s for the 60s metric lookback, 30s healthy hold,
and scrape/runtime margin. The spike Pod must still be running.

```powershell
& $demoKubectl --context kind-nexus -n nexus-demo scale deployment/payment-service --replicas=3
& $demoKubectl --context kind-nexus -n nexus-demo rollout status deployment/payment-service --timeout=180s
if ($LASTEXITCODE -ne 0) { throw 'Scaling rollout did not complete' }
$actionCompletedAt = [DateTime]::UtcNow.ToString('o')
$actionCompletedAt | Set-Content -Encoding utf8 (Join-Path $demoOutput 'action-completed-at.txt')
Start-Sleep -Seconds 120

$deploymentAfter = & $demoKubectl --context kind-nexus -n nexus-demo get deployment payment-service -o json | ConvertFrom-Json
$deploymentAfter | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'deployment-after.json')
if (($deploymentBefore.spec.template | ConvertTo-Json -Depth 30 -Compress) -ne ($deploymentAfter.spec.template | ConvertTo-Json -Depth 30 -Compress)) {
    throw 'Payment image or Pod configuration changed during scale; this run does not prove scaling alone'
}
$paymentPodsAfter = & $demoKubectl --context kind-nexus -n nexus-demo get pods -l app=payment-service -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read payment Pod statuses after scaling' }
$paymentPodsAfter | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'payment-pods-after.json')
if (@($paymentPodsAfter.items).Count -ne 3) { throw 'Scaling proof requires exactly three payment Pods' }
$paymentHealthAfter = @(foreach ($paymentPod in $paymentPodsAfter.items) {
    $paymentContainer = $paymentPod.status.containerStatuses | Where-Object name -eq 'payment-service'
    $paymentReady = $paymentPod.status.conditions | Where-Object type -eq 'Ready'
    if ($paymentPod.status.phase -ne 'Running' -or $paymentReady.status -ne 'True' -or -not $paymentContainer.ready -or $paymentPod.metadata.deletionTimestamp) {
        throw "Payment Pod $($paymentPod.metadata.name) must be Running, Ready, and not terminating"
    }
    if ($paymentContainer.imageID -ne $capturedPaymentImageId) {
        throw "Payment Pod $($paymentPod.metadata.name) changed imageID; this run does not prove scaling alone"
    }
    Get-VerifiedPaymentPodHealth $paymentPod.metadata.name
})
$paymentHealthAfter | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'payment-health-after.json')
$paymentPodNamesAfter = @($paymentPodsAfter.items | ForEach-Object { $_.metadata.name })
$spikePodAfter = & $demoKubectl --context kind-nexus -n nexus-demo get pod payment-traffic -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the spike traffic Pod after scaling' }
$spikePodAfter | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'spike-pod-after.json')
if ($spikePodAfter.status.phase -ne 'Running' -or $spikePodAfter.metadata.uid -ne $spikePodUid -or $spikePodAfter.metadata.deletionTimestamp) {
    throw 'The same spike Pod must still be Running during recovery'
}
$afterSnapshot = Invoke-RestMethod "$m1Url/internal/telemetry/snapshot?service=payment-service"
$afterSnapshot | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'after-snapshot.json')
$recoveryBody = @{
    incident_id = $demoIncidentId
    service = 'payment-service'
    scenario = 'traffic_spike'
    action_completed_at = $actionCompletedAt
} | ConvertTo-Json
$recoveryResult = Invoke-RestMethod -Method Post -Uri "$m1Url/internal/recovery/validate" -ContentType 'application/json' -Body $recoveryBody
$recoveryResult | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'recovery-result.json')
$recoveryEvidence = Invoke-RestMethod "$m1Url/internal/recovery/$demoIncidentId"
$recoveryEvidence | ConvertTo-Json -Depth 40 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'recovery-evidence.json')
$recoveryResult
if (-not $recoveryResult.recovered -or -not $recoveryResult.slo_restored) { throw 'Measured recovery did not pass' }
```

Recovery requires all three hold samples and the latest snapshot to stay within
baseline latency/5xx thresholds, have more replicas than the captured incident,
have at least half its observed request rate, and have lower P95 than incident
capture. M1 also checks payment health. Traffic-spike validation does not compare
incident and recovery versions explicitly, so preserve and independently verify
the unchanged payment Pod template and version.
The saved Pod statuses include image IDs. Every added replica must use the
captured replica's actual image ID, because an unchanged mutable image tag alone
does not prove the running code stayed unchanged. Direct per-Pod health requests
verify all three replicas still run v1 with the same processing work.

## Export balance and load evidence

Use Prometheus to confirm all three payment pods receive requests. This query
measures actual completed payments per pod, independently of configured load.

```powershell
$podRateQuery = 'sum by (pod) (rate(nexus_http_requests_total{service_name="payment-service",route="/pay"}[1m]))'
$podRates = Invoke-RestMethod ("http://localhost:19090/api/v1/query?query=" + [Uri]::EscapeDataString($podRateQuery))
$podRates | ConvertTo-Json -Depth 30 | Set-Content -Encoding utf8 (Join-Path $demoOutput 'per-pod-request-rate.json')
if ($podRates.status -ne 'success') { throw 'Prometheus per-Pod request-rate query failed' }
foreach ($paymentPodName in $paymentPodNamesAfter) {
    $currentPodRate = @($podRates.data.result | Where-Object { $_.metric.pod -eq $paymentPodName })
    if ($currentPodRate.Count -ne 1) {
        throw "Payment Pod $paymentPodName has no unique request-rate series; Service balancing is not proven"
    }
    $currentPodRps = [double]::Parse($currentPodRate[0].value[1], [Globalization.CultureInfo]::InvariantCulture)
    if ([double]::IsNaN($currentPodRps) -or [double]::IsInfinity($currentPodRps) -or $currentPodRps -le 0) {
        throw "Payment Pod $paymentPodName has no positive request rate; Service balancing is not proven"
    }
}
$podRates.data.result | Select-Object @{Name='pod';Expression={$_.metric.pod}}, @{Name='rps';Expression={$_.value[1]}}
& $demoKubectl --context kind-nexus -n nexus-demo logs payment-traffic | Set-Content -Encoding utf8 (Join-Path $demoOutput 'spike-traffic-running.jsonl')

# Finish the same spike Pod only after recovery and balance evidence are saved.
& $demoKubectl --context kind-nexus -n nexus-demo exec payment-traffic -- python -c 'import os, signal; os.kill(1, signal.SIGTERM)'
& $demoKubectl --context kind-nexus -n nexus-demo wait '--for=jsonpath={.status.phase}=Succeeded' pod/payment-traffic --timeout=45s
& $demoKubectl --context kind-nexus -n nexus-demo logs payment-traffic | Set-Content -Encoding utf8 (Join-Path $demoOutput 'spike-traffic.jsonl')
Get-Content (Join-Path $demoOutput 'spike-traffic.jsonl') -Tail 1 | ConvertFrom-Json
```

Each load log is JSON; the final `summary` includes submitted/completed requests,
successes, status codes, client P95 rounded up to 1ms, actual offered RPS,
scheduler skips, and requests dropped at the in-flight bound. Interval statistics
show whether post-scale offered load stays near the configured 90 RPS. Nonzero
`capacity_dropped` or `scheduler_skipped` means achieved offered load was lower;
report this explicitly. Generator P95 measures client HTTP latency; M1 measures
server P95 independently from payment histogram buckets.
