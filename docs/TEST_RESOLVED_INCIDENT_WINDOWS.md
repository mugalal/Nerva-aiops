# Test a real RESOLVED rollback incident on Windows

Verified on October 12, 2026 (Cairo): `INC-20261011-214110-payment-service` reached **RESOLVED** after NERVA rolled v2 back to v1. M1 measured p95 latency falling from 737.5 ms to 9.52 ms, errors from 15.5% to 0%, and recovery in 91.52 seconds with `recovered=true` and `slo_restored=true`. All eight v1 replicas, environment settings, resources and probes matched the healthy checkpoint. The resolved outcome was archived and displayed by M5; the temporary test traffic was stopped. Local evidence: `.review-branches/payment-resolution-final-context.json`.

Use the existing isolated `kind-nexus` / `nexus-demo` stack. This creates a new incident; the older ESCALATED incident remains archived with its original outcome. Keep the current **8 healthy v1 replicas, processing work, resources and probes** unchanged. Both payment images must already be loaded. Prerequisite: M2/M4/M5 run the current source images (`nexus/<module>:resolved-test-44264ef` for this workspace), M2/M4 share the configured ingest-token Secret reference, and M5 has its UI-token reference. Existing database/approval credentials stay intact. M4 must support and configure `M4_AUTO_BUILD_DELAY_SECONDS=60` and automatic M1 recovery validation; an environment setting alone cannot add missing code to an older image.

Run these blocks in one PowerShell terminal from this workspace, except the two dedicated port-forward terminals. They use the existing diagnostic kubeconfig without displaying credentials.

```powershell
Set-Location 'E:\AIOPS\final project'
$ErrorActionPreference = 'Stop'
$taskKubectl = 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe'
$taskKubeArguments = @('--kubeconfig', 'E:\AIOPS\final project\.review-branches\nada-diagnostic-kubeconfig.yaml', '--context', 'kind-nexus', '--namespace', 'nexus-demo')
function Invoke-ResolvedKube {
    if ($MyInvocation.ExpectingInput) { $input | & $taskKubectl @taskKubeArguments @args }
    else { & $taskKubectl @taskKubeArguments @args }
    if ($LASTEXITCODE -ne 0) { throw 'kubectl failed; inspect the error before continuing.' }
}
$taskM4 = 'http://127.0.0.1:18004'
$taskRun = [DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmss')
$taskCheckpoint = ".review-branches/resolved-$taskRun"
New-Item -ItemType Directory -Path $taskCheckpoint | Out-Null
```

In **two separate terminals**, leave these forwards running. Kubernetes uses ports 18004/18005 so existing Docker services on 8004/8005 can coexist.

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\nada-diagnostic-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/shared-nexus-api 18004:8004 --address 127.0.0.1
```

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\nada-diagnostic-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/incident-memory 18005:8005 --address 127.0.0.1
```

Open [the Kubernetes dashboard](http://127.0.0.1:18005/). Return to the main terminal. Require M4 readiness, real providers and `remediation_backend=kubernetes`. Restart a forward if its pod restarts.

```powershell
$taskHealth = Invoke-RestMethod "$taskM4/ready"
if ($taskHealth.status -ne 'ok' -or $taskHealth.remediation_backend -ne 'kubernetes') { throw 'Real Kubernetes M4 is not ready.' }
$taskPaymentBefore = Invoke-ResolvedKube get deployment payment-service -o json | ConvertFrom-Json
if ($taskPaymentBefore.spec.replicas -ne 8 -or $taskPaymentBefore.spec.template.metadata.labels.version -ne 'v1') { throw 'Start with the existing eight healthy v1 replicas.' }
$taskPaymentBefore | ConvertTo-Json -Depth 60 | Set-Content "$taskCheckpoint/payment-before.json" -Encoding utf8
Invoke-ResolvedKube create configmap payment-traffic-script --from-file=k8s-traffic.py=scripts/k8s-traffic.py --dry-run=client -o json | Invoke-ResolvedKube apply -f -
$taskTrafficName = "payment-resolved-$taskRun"
$taskTraffic = Get-Content 'infrastructure/k8s/traffic-demo.yaml' -Raw
if ($taskTraffic -notmatch '(?m)^\s*- name: RPS\r?\n\s*value: "10"') { throw 'Inspect the traffic manifest: this test requires 10 RPS.' }
$taskTraffic = [regex]::Replace($taskTraffic, '(?m)^  name: payment-traffic\r?$', "  name: $taskTrafficName")
$taskTraffic | Set-Content "$taskCheckpoint/traffic.yaml" -Encoding utf8
Invoke-ResolvedKube apply -f "$taskCheckpoint/traffic.yaml"
Invoke-ResolvedKube wait --for=condition=Ready "pod/$taskTrafficName" --timeout=120s
Start-Sleep -Seconds 240
$taskBaseline = @(Get-Content 'scripts/live-drill.py' -Raw | Invoke-ResolvedKube exec -i deployment/shared-nexus-api '--' python - baseline | ConvertFrom-Json)
if ($taskBaseline[0] -ne 200 -or $taskBaseline[1].version -ne 'v1') { throw 'Healthy measured baseline failed; keep traffic running and investigate.' }
$taskBaseline[1] | ConvertTo-Json -Depth 20 | Set-Content "$taskCheckpoint/baseline.json" -Encoding utf8
$taskStatus = Get-Content 'scripts/live-drill.py' -Raw | Invoke-ResolvedKube exec -i deployment/shared-nexus-api '--' python - status | ConvertFrom-Json
if ($taskStatus.m2.Count) {
    $taskLastM2 = Get-Content 'scripts/live-drill.py' -Raw | Invoke-ResolvedKube exec -i deployment/shared-nexus-api '--' python - m2detail $taskStatus.m2[0][0] | ConvertFrom-Json
    if (([DateTimeOffset]::UtcNow - [DateTimeOffset]$taskLastM2.last_alert_at).TotalSeconds -le 120) { throw 'Wait for more than 120 seconds without an M2 alert, then recheck before injecting.' }
}
$taskKnownIds = @((Invoke-RestMethod "$taskM4/api/incidents/") | ForEach-Object { $_.incident_id })
```

The unique traffic pod uses Service DNS at 10 RPS for 3600 seconds; it does not restart an older completed pod. The baseline covers 75 seconds ending 15 seconds ago at 15-second steps. The M2 guard requires **more than 120 seconds without an alert** before injection; otherwise the next fault can join an old terminal incident. Extend healthy warmup if necessary. Keep this traffic running through recovery; do not replace the baseline after injection.

Inject only the v2 fault fields and release provenance on the pod template. A full faulty Deployment manifest would change the saved workload configuration.

```powershell
$taskFaultPatch = @{ spec = @{ template = @{
    metadata = @{ labels = @{ version = 'v2' }; annotations = @{
        'nexus.io/previous-version' = 'v1'; 'nexus.io/version' = 'v2'
        'nexus.io/commit-sha' = "local-resolved-$taskRun"; 'nexus.io/pipeline-id' = "local-rollback-$taskRun"
    } }
    spec = @{ containers = @(@{ name = 'payment-service'; image = 'nexus/payment-service:v2'; env = @(
        @{ name = 'VERSION'; value = 'v2' }, @{ name = 'FAULT_LATENCY_MS'; value = '600' }, @{ name = 'FAULT_5XX_RATE'; value = '0.14' }
    ) }) }
} } }
$taskFaultPatch | ConvertTo-Json -Depth 20 | Set-Content "$taskCheckpoint/fault-patch.json" -Encoding utf8
Invoke-ResolvedKube patch deployment payment-service --type strategic --patch-file "$taskCheckpoint/fault-patch.json"
Invoke-ResolvedKube rollout status deployment/payment-service --timeout=300s
$taskWaitUntil = [DateTimeOffset]::UtcNow.AddMinutes(8)
do {
    $taskNewIncident = (Invoke-RestMethod "$taskM4/api/incidents/") | Where-Object { $_.incident_id -notin $taskKnownIds -and 'payment-service' -in $_.affected_services } | Sort-Object started_at -Descending | Select-Object -First 1
    if ($taskNewIncident.status -in @('AWAITING_APPROVAL', 'ESCALATED')) { break }
    Start-Sleep -Seconds 10
} while ([DateTimeOffset]::UtcNow -lt $taskWaitUntil)
if ($taskNewIncident.status -ne 'AWAITING_APPROVAL') { throw 'No new actionable proposal; use the manual restoration checkpoint below if needed.' }
$taskIncidentId = $taskNewIncident.incident_id
$taskContext = Invoke-RestMethod "$taskM4/api/incidents/$taskIncidentId/context"
$taskContext | ConvertTo-Json -Depth 30
```

Review this incident in the dashboard/context. Require `faulty_deployment`, `ROLLBACK`, target `payment-service`, v2→v1, real evidence and an unexpired proposal. Approve promptly (default proposal lifetime: 300 seconds). The following code sends the **reviewed digest**, rechecks it against the current proposal, and reads M4's own approval token without printing it. It does not depend on an operator token in the older M5 image.

```powershell
if ($taskContext.rca.root_cause -ne 'faulty_deployment' -or $taskContext.decision.recommended_action -ne 'ROLLBACK') { throw 'Review failed: do not approve this proposal.' }
$taskApproveCode = @'
import json, os, sys, urllib.request, urllib.error
incident, reviewed_digest = sys.argv[1:]
base = "http://127.0.0.1:8004/api/incidents/" + incident
with urllib.request.urlopen(base + "/proposal", timeout=30) as response:
    proposal = json.load(response)
    current_digest = response.headers.get("X-Proposal-Digest")
if not reviewed_digest or current_digest != reviewed_digest:
    raise SystemExit("Proposal changed; reload context and review again.")
if proposal["recommended_action"] != "ROLLBACK" or proposal["target"] != "payment-service":
    raise SystemExit("Unexpected proposal; approval stopped.")
request = urllib.request.Request(base + "/approve", method="POST", headers={
    "Authorization": "Bearer " + os.environ["NEXUS_APPROVAL_TOKEN"],
    "X-Nexus-Approver": "resolved-test-operator", "X-Proposal-Digest": reviewed_digest})
try:
    with urllib.request.urlopen(request, timeout=300) as response:
        print(json.dumps(json.load(response), indent=2))
except urllib.error.HTTPError as error:
    raise SystemExit("Approval failed: HTTP %s; reload incident context." % error.code)
'@
$taskApproveCode | Invoke-ResolvedKube exec -i deployment/shared-nexus-api '--' python - $taskIncidentId $taskContext.proposal_digest
```

Let M4 validate automatically. Recovery normally needs at least 90 seconds after action completion: a 60-second metric lookback flush and a complete 30-second healthy hold. Default M4 recovery deadline: 360 seconds. A successful Kubernetes action alone does not resolve an incident.

```powershell
$taskWaitUntil = [DateTimeOffset]::UtcNow.AddMinutes(8)
do {
    $taskResult = Invoke-RestMethod "$taskM4/api/incidents/$taskIncidentId/context"
    if ($taskResult.incident.status -in @('RESOLVED', 'ESCALATED')) { break }
    Start-Sleep -Seconds 15
} while ([DateTimeOffset]::UtcNow -lt $taskWaitUntil)
$taskResult | ConvertTo-Json -Depth 40 | Set-Content "$taskCheckpoint/final-context.json" -Encoding utf8
$taskPaymentAfter = Invoke-ResolvedKube get deployment payment-service -o json | ConvertFrom-Json
if ($taskResult.incident.status -ne 'RESOLVED' -or $taskResult.recovery_result.recovered -ne $true -or $taskResult.recovery_result.slo_restored -ne $true -or $taskPaymentAfter.spec.template.metadata.labels.version -ne 'v1' -or $taskPaymentAfter.spec.replicas -ne 8) { throw 'Test did not pass; inspect saved context and restore manually if needed.' }
Invoke-ResolvedKube rollout status deployment/payment-service --timeout=300s
"PASS: $taskIncidentId RESOLVED; M1 recovered and restored SLO; eight v1 replicas. Evidence: $taskCheckpoint"
```

After success, check incident memory/Copilot in the Kubernetes dashboard; archival can lag briefly. Leave older outcomes intact. Stop only this test's traffic when verification is complete:

```powershell
Invoke-ResolvedKube delete pod $taskTrafficName --ignore-not-found
```

If the run fails, restore the saved template/replicas **manually** after confirming M4 is no longer EXECUTING/VALIDATING or awaiting approval. Do not race a pending action. Record the failed result honestly; a later healthy measurement cannot rewrite a cached terminal escalation. This checkpoint restores the captured work, resources, probes and version.

```powershell
$taskSavedPayment = Get-Content "$taskCheckpoint/payment-before.json" -Raw | ConvertFrom-Json
$taskRestorePatch = @(
    @{ op = 'replace'; path = '/spec/template'; value = $taskSavedPayment.spec.template },
    @{ op = 'replace'; path = '/spec/replicas'; value = $taskSavedPayment.spec.replicas }
)
$taskRestorePatch | ConvertTo-Json -Depth 60 | Set-Content "$taskCheckpoint/restore-patch.json" -Encoding utf8
Invoke-ResolvedKube patch deployment payment-service --type json --patch-file "$taskCheckpoint/restore-patch.json"
Invoke-ResolvedKube rollout status deployment/payment-service --timeout=300s
Invoke-ResolvedKube delete pod $taskTrafficName --ignore-not-found
```
