# Traffic-spike commands for the existing local cluster

Run these yourself in PowerShell. Keep the main terminal open; other terminals are only for port-forwarding. Stop on any error. No database deletion is required.

## 1. Rebuild prerequisite

Open Docker Desktop and wait for its Linux engine. If the rebuild has not already printed 'Rebuild complete' and 'finops_registry_check: passed', run:

```powershell
Set-Location 'E:\AIOPS\final project'
.\scripts\rebuild-spike-modules.ps1
```

Skip that block if it already completed successfully. Do not rebuild during a running recovery.

## 2. Main terminal: connect and define helpers

```powershell
Set-Location 'E:\AIOPS\final project'
$ErrorActionPreference = 'Stop'
$taskKubectl = Join-Path (Get-Location) '.review-branches/tools/kubectl.exe'
$taskKind = Join-Path (Get-Location) '.review-branches/tools/kind.exe'
$taskKubeconfig = Join-Path (Get-Location) '.review-branches/spike-kubeconfig.yaml'
docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker Linux engine is not ready.' }
docker start nexus-control-plane nexus-worker nexus-worker2
if ($LASTEXITCODE -ne 0) { throw 'Could not start the existing cluster nodes.' }
$taskConfig = & $taskKind get kubeconfig --name nexus
if ($LASTEXITCODE -ne 0) { throw 'Could not obtain the existing cluster configuration.' }
$taskConfig | Set-Content -LiteralPath $taskKubeconfig -Encoding utf8
function K {
    $taskFlags = @('--kubeconfig', $taskKubeconfig, '--context', 'kind-nexus', '-n', 'nexus-demo')
    if ($MyInvocation.ExpectingInput) { $input | & $taskKubectl @taskFlags @args }
    else { & $taskKubectl @taskFlags @args }
    if ($LASTEXITCODE -ne 0) { throw 'kubectl failed; stop and inspect the error.' }
}
function D {
    $taskOutput = Get-Content 'scripts/live-drill.py' -Raw | K exec -i deployment/shared-nexus-api '--' python - @args
    $taskParsed = ConvertFrom-Json -InputObject ($taskOutput -join "`n")
    foreach ($taskItem in $taskParsed) { $taskItem }
}
function Get-TaskIncidents {
    $taskItems = Invoke-RestMethod "$taskM4/api/incidents/"
    foreach ($taskItem in $taskItems) { $taskItem }
}
K wait --for=condition=Ready nodes --all --timeout=180s
K get deployments
```

## 3. Two dedicated port-forward terminals

Reuse existing working forwards; do not launch duplicates on occupied ports. API terminal:

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\spike-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/shared-nexus-api 18004:8004 --address 127.0.0.1
```

Website terminal:

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\spike-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/incident-memory 18005:8005 --address 127.0.0.1
```

Open http://127.0.0.1:18005/. All remaining commands go in the main terminal.

## 4. Prepare one healthy v1 replica and one baseline generator

These commands remove only the two named demo traffic Pods. Other running traffic is a reason to stop and inspect before continuing. Existing incident history is retained.

```powershell
$taskM4 = 'http://127.0.0.1:18004'
$taskHealth = Invoke-RestMethod "$taskM4/ready"
if ($taskHealth.status -ne 'ok' -or $taskHealth.remediation_backend -ne 'kubernetes') { throw 'Real Kubernetes remediation is not ready.' }
$taskExisting = @(Get-TaskIncidents)
if ($taskExisting | Where-Object { $_.status -in @('EXECUTING', 'VALIDATING') }) { throw 'Wait for the existing action/recovery to finish.' }
$taskRun = [DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss')
$taskDirectory = ".review-branches/manual-spike-$taskRun"
New-Item -ItemType Directory -Path $taskDirectory | Out-Null
$taskBeforeText = K get deployment payment-service -o json
$taskBefore = ($taskBeforeText -join "`n") | ConvertFrom-Json
if ($taskBefore.spec.template.metadata.labels.version -ne 'v1') { throw 'Start with a healthy v1 deployment.' }
$taskBefore | ConvertTo-Json -Depth 60 | Set-Content "$taskDirectory/payment-before.json" -Encoding utf8
K delete pod payment-spike-load payment-traffic --ignore-not-found
$taskTrafficText = K get pods -l app=payment-traffic -o json
$taskTraffic = ($taskTrafficText -join "`n") | ConvertFrom-Json
if ($taskTraffic.items | Where-Object { $_.status.phase -in @('Running', 'Pending') }) { throw 'Other traffic Pods are active; inspect them before continuing.' }
K scale deployment/payment-service --replicas=1
K set env deployment/payment-service PAYMENT_WORK_ITERATIONS=20000 FAULT_LATENCY_MS=0 FAULT_5XX_RATE=0
K rollout status deployment/payment-service --timeout=300s
K rollout restart deployment/anomaly-engine
K rollout status deployment/anomaly-engine --timeout=300s
K create configmap payment-traffic-script --from-file=k8s-traffic.py=scripts/k8s-traffic.py --dry-run=client -o json | K apply -f -
K apply -f infrastructure/k8s/traffic-demo.yaml
K wait --for=condition=Ready pod/payment-traffic --timeout=120s
K get deployment payment-service
```

M2 is restarted here to clear old in-memory correlations before the fresh test, including any left over from the prior database reset. Expect 1/1 v1 payment replicas. Do not change work, resources or version during the test.

## 5. Warm up and measure a fresh baseline

```powershell
$taskBaselineVerified = $false
for ($taskMinute = 1; $taskMinute -le 4; $taskMinute++) {
    Write-Host "Healthy traffic: minute $taskMinute of 4"
    Start-Sleep -Seconds 60
}
K logs payment-traffic --tail=2
$taskBaseline = @(D baseline)
if ($taskBaseline[0] -ne 200 -or $taskBaseline[1].version -ne 'v1' -or $taskBaseline[1].replica_count.minimum -ne 1 -or $taskBaseline[1].replica_count.maximum -ne 1) {
    $taskBaseline | ConvertTo-Json -Depth 12
    throw 'Healthy baseline failed; do not start the spike.'
}
$taskBaseline[1] | ConvertTo-Json -Depth 20 | Set-Content "$taskDirectory/baseline.json" -Encoding utf8
$taskBaseline[1] | ConvertTo-Json -Depth 10
$taskEvidenceCheck = @'
import json, urllib.request
with urllib.request.urlopen('http://telemetry-intelligence:8001/internal/evidence/preview?service=payment-service', timeout=30) as response:
    data = json.load(response)
if data.get('provider_mode') != 'real' or data.get('provider_errors') != []:
    raise SystemExit('Evidence providers are not ready: ' + str(data.get('provider_errors')))
if data['telemetry']['version'] != 'v1' or data['telemetry']['metrics']['replica_count'] != 1:
    raise SystemExit('Expected one healthy v1 replica.')
print(json.dumps({'evidence_provider_errors': [], 'version': 'v1', 'replicas': 1}))
'@
$taskEvidenceCheck | K exec -i deployment/shared-nexus-api '--' python -
$taskStatus = D status
if ($taskStatus.m2.Count) {
    $taskLast = D m2detail $taskStatus.m2[0][0]
    if (([DateTimeOffset]::UtcNow - [DateTimeOffset]$taskLast.last_alert_at).TotalSeconds -le 120) { throw 'Wait for more than 120 seconds without an M2 alert, then rerun the status check.' }
}
$taskKnownIds = @((Get-TaskIncidents).incident_id)
$taskBaselineVerified = $true
```

Expect a successful baseline near 10 RPS, zero errors and one replica. Do not overwrite the baseline after starting the spike.

## 6. Copy the current UI token and start the spike

```powershell
if (-not (Get-Variable taskBaselineVerified -ValueOnly -ErrorAction SilentlyContinue)) { throw 'Complete every baseline check successfully before starting the spike.' }
$taskHealth = Invoke-RestMethod "$taskM4/ready"
if ($taskHealth.status -ne 'ok' -or $taskHealth.remediation_backend -ne 'kubernetes') { throw 'Restore the API port-forward and verify Kubernetes remediation before starting the spike.' }
$taskEncoded = K get secret nexus-runtime -o jsonpath='{.data.M5_UI_OPERATOR_TOKEN}'
if (-not $taskEncoded) { throw 'The UI operator token is missing.' }
$taskOperatorToken = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($taskEncoded -join '')))
Set-Clipboard -Value $taskOperatorToken
K apply -f infrastructure/k8s/spike-load.yaml
K wait --for=condition=Ready pod/payment-spike-load --timeout=120s
K logs payment-spike-load --tail=2
```

The extra generator targets 150 RPS, making the configured total 160 RPS. Offered/completed RPS depends on capacity. Keep both traffic Pods running through recovery.

## 7. Review and approve on the website yourself

On http://127.0.0.1:18005/, refresh and select the new incident. A few minutes of detection/diagnosis can be normal. To list only new incident IDs:

```powershell
Get-TaskIncidents | Where-Object { $_.incident_id -notin $taskKnownIds } | Format-Table incident_id, status, severity
```

Require AWAITING_APPROVAL, root cause traffic_spike, action SCALE on payment-service, real evidence and an unexpired proposal. Paste your clipboard into the Operator token box, click Approve and confirm. The replica target must exceed one; the first step permits at most eight, but eight is not guaranteed. Do not manually scale to force success.

## 8. Verify the measured outcome

Wait at least about 90 seconds after action completion. Watch EXECUTING → VALIDATING → RESOLVED in the UI. Enter the new ID and run this context check repeatedly:

```powershell
$taskIncidentId = Read-Host 'Enter the NEW spike incident ID'
$taskContextUrl = "$taskM4/api/incidents/$([Uri]::EscapeDataString($taskIncidentId))/context"
$taskResult = Invoke-RestMethod $taskContextUrl
K get deployment payment-service
[pscustomobject]@{
    status = $taskResult.incident.status
    cause = $taskResult.rca.root_cause
    action = $taskResult.action_result.action
    action_status = $taskResult.action_result.status
    recovered = $taskResult.recovery_result.recovered
    slo_restored = $taskResult.recovery_result.slo_restored
    before = $taskResult.recovery_result.before
    after = $taskResult.recovery_result.after
} | Format-List
```

Success means RESOLVED, SCALE/SUCCESS, more than one Ready v1 replica, recovered=true and slo_restored=true under continued load. An ESCALATED result is a failed test; inspect the recorded reason.

## 9. Cleanup after a terminal outcome

```powershell
$taskResult = Invoke-RestMethod $taskContextUrl
if ($taskResult.incident.status -notin @('RESOLVED', 'ESCALATED')) { throw 'Recovery is still active; do not stop the spike yet.' }
$taskActive = @(Get-TaskIncidents) | Where-Object { $_.status -in @('EXECUTING', 'VALIDATING') }
if ($taskActive) { throw 'Another recovery is active; wait before cleanup.' }
$taskResult | ConvertTo-Json -Depth 50 | Set-Content "$taskDirectory/final-context.json" -Encoding utf8
K delete pod payment-spike-load payment-traffic --ignore-not-found
$taskSaved = Get-Content "$taskDirectory/payment-before.json" -Raw | ConvertFrom-Json
$taskRestore = @(
    @{ op = 'replace'; path = '/spec/template'; value = $taskSaved.spec.template },
    @{ op = 'replace'; path = '/spec/replicas'; value = $taskSaved.spec.replicas }
)
$taskRestore | ConvertTo-Json -Depth 60 | Set-Content "$taskDirectory/restore.json" -Encoding utf8
K patch deployment payment-service --type json --patch-file "$taskDirectory/restore.json"
K rollout status deployment/payment-service --timeout=300s
```

This stops the 24-hour/autorestarting baseline as well as the spike and restores your captured settings. It does not delete incident history.

## Optional Kubernetes Grafana

If the cluster's Grafana service is installed, use another dedicated terminal. Port 13000 avoids the Compose Grafana port:

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\spike-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/grafana 13000:3000 --address 127.0.0.1
```

Open http://127.0.0.1:13000/d/nexus-m1-overview/nexus-m1-service-overview and select Last 5 minutes. Use the measured curves, not promised latency/CPU numbers. Grafana is optional; the NERVA incident/recovery panels contain the test outcome.
