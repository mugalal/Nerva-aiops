# Run the traffic-spike test yourself on Windows

Use PowerShell and the existing local kind cluster. You run every command, inspect the new incident on the website, and approve scaling yourself. The audit found an older M6 image, an older M1 image and outdated M4 deployment settings. Rebuild these first using the block below; the script preserves existing credentials and database storage. The Kubernetes dashboard is http://127.0.0.1:18005/; port 8005 belongs to the separate Docker stack.

Docker's Linux engine was unavailable at the last read-only check. Open Docker Desktop first and wait for its engine to start. If Windows reports `runtime: cannot allocate memory`, close unused heavy applications before continuing. Do not proceed until the initial checks succeed.

## 0. Rebuild outdated modules once after this pull

Run before starting test traffic or approving an incident. This builds M1, M6 and M4 from your current checkout, imports unique image tags into the existing cluster, applies the repository's current manifests and verifies M6's registry filter. M4 is rebuilt too so applying its manifest cannot switch it to an older cached image. Payment replica counts and traffic are left for you to configure in the steps below.

```powershell
Set-Location 'E:\AIOPS\final project'
.\scripts\rebuild-spike-modules.ps1
```

Wait for 'Rebuild complete'. Then continue with step 1. The script needs Docker Desktop running and the existing nexus cluster containers; it starts those containers if they were stopped. It does not recreate the cluster or run the spike.

## 1. Main PowerShell terminal: reconnect to the existing cluster

```powershell
Set-Location 'E:\AIOPS\final project'
$ErrorActionPreference = 'Stop'
$taskKubectl = Join-Path (Get-Location) '.review-branches/tools/kubectl.exe'
$taskKind = Join-Path (Get-Location) '.review-branches/tools/kind.exe'
$taskKubeconfig = Join-Path (Get-Location) '.review-branches/spike-kubeconfig.yaml'

docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait for its Linux engine.' }
docker start nexus-control-plane nexus-worker nexus-worker2
if ($LASTEXITCODE -ne 0) { throw 'The existing nexus cluster could not start.' }
$taskKubeconfigText = & $taskKind get kubeconfig --name nexus
if ($LASTEXITCODE -ne 0) { throw 'Could not obtain the existing cluster kubeconfig.' }
$taskKubeconfigText | Set-Content -LiteralPath $taskKubeconfig -Encoding utf8

function K {
    $taskArguments = @('--kubeconfig', $taskKubeconfig, '--context', 'kind-nexus', '-n', 'nexus-demo')
    if ($MyInvocation.ExpectingInput) { $input | & $taskKubectl @taskArguments @args }
    else { & $taskKubectl @taskArguments @args }
    if ($LASTEXITCODE -ne 0) { throw 'kubectl failed; inspect its error before continuing.' }
}
function D {
    $taskOutput = Get-Content 'scripts/live-drill.py' -Raw | K exec -i deployment/shared-nexus-api '--' python - @args
    ($taskOutput -join "`n") | ConvertFrom-Json
}
K wait --for=condition=Ready nodes --all --timeout=180s
K get deployments
```

Keep this main terminal open: the variables and helpers are used below. The refreshed kubeconfig handles a possible API port change after restarting Docker. Do not create a replacement cluster or delete database volumes to run this test.

## 2. Website and API: two separate PowerShell terminals

If these URLs already respond, reuse the existing forwards. Otherwise run one command per dedicated terminal and leave both running. An address-in-use message means another forward is already listening; do not repeatedly start duplicates.

API terminal:

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\spike-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/shared-nexus-api 18004:8004 --address 127.0.0.1
```

Website terminal:

```powershell
& 'E:\AIOPS\final project\.review-branches\tools\kubectl.exe' --kubeconfig 'E:\AIOPS\final project\.review-branches\spike-kubeconfig.yaml' --context kind-nexus -n nexus-demo port-forward service/incident-memory 18005:8005 --address 127.0.0.1
```

Open http://127.0.0.1:18005/. Return to the main terminal for all remaining commands. Restart a forward if its pod restarts.

## 3. Save the original state and prepare one healthy replica

The prior rollback test left eight replicas. Reduce to one and add constant processing work BEFORE measuring the baseline; otherwise a spike may fit the existing capacity. Keep these work/resource settings unchanged through scaling and recovery.

```powershell
$taskM4 = 'http://127.0.0.1:18004'
$taskHealth = Invoke-RestMethod "$taskM4/ready"
if ($taskHealth.status -ne 'ok' -or $taskHealth.remediation_backend -ne 'kubernetes') { throw 'Real Kubernetes remediation is not ready.' }
$taskExisting = @(Invoke-RestMethod "$taskM4/api/incidents/")
if ($taskExisting | Where-Object { $_.status -in @('EXECUTING', 'VALIDATING') }) { throw 'Wait for the active recovery to finish.' }
$taskRun = [DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss')
$taskDirectory = ".review-branches/spike-$taskRun"
New-Item -ItemType Directory -Path $taskDirectory | Out-Null
$taskPaymentText = K get deployment payment-service -o json
$taskPaymentBefore = ($taskPaymentText -join "`n") | ConvertFrom-Json
if ($taskPaymentBefore.spec.template.metadata.labels.version -ne 'v1') { throw 'Start with a healthy v1 payment deployment.' }
$taskPaymentBefore | ConvertTo-Json -Depth 60 | Set-Content "$taskDirectory/payment-before.json" -Encoding utf8

K scale deployment/payment-service --replicas=1
K set env deployment/payment-service PAYMENT_WORK_ITERATIONS=20000 FAULT_LATENCY_MS=0 FAULT_5XX_RATE=0
K rollout status deployment/payment-service --timeout=300s
K get deployment payment-service
```

Expect 1/1 Ready and v1. This is a capacity test; do not deploy v2 or change its image/version during the spike.

## 4. Start healthy 10 RPS traffic and measure the baseline

These generators run inside Kubernetes and reach Service DNS, allowing load to reach all replicas after scaling. Each run uses new pod names; applying a completed Pod does not restart it.

```powershell
K create configmap payment-traffic-script --from-file=k8s-traffic.py=scripts/k8s-traffic.py --dry-run=client -o json | K apply -f -
function New-TrafficPod {
    param([string]$Name, [int]$Rps, [string]$Path)
    $taskManifest = Get-Content 'infrastructure/k8s/traffic-demo.yaml' -Raw
    $taskManifest = [regex]::Replace($taskManifest, '(?m)^  name: payment-traffic\r?$', "  name: $Name")
    $taskManifest = [regex]::Replace($taskManifest, '(?m)(^\s*- name: RPS\r?\n\s*value: )"[^"]+"', [System.Text.RegularExpressions.MatchEvaluator]{ param($taskMatch) $taskMatch.Groups[1].Value + '"' + $Rps + '"' })
    $taskManifest | Set-Content -LiteralPath $Path -Encoding utf8
    K apply -f $Path
    K wait --for=condition=Ready "pod/$Name" --timeout=120s
}
$taskBaselinePod = "payment-spike-base-$taskRun"
$taskSpikePod = "payment-spike-load-$taskRun"
New-TrafficPod -Name $taskBaselinePod -Rps 10 -Path "$taskDirectory/baseline-traffic.yaml"
for ($taskMinute = 1; $taskMinute -le 4; $taskMinute++) {
    Write-Host "Healthy warmup minute $taskMinute of 4..."
    Start-Sleep -Seconds 60
}
K logs $taskBaselinePod --tail=2
$taskBaseline = @(D baseline)
if ($taskBaseline[0] -ne 200 -or $taskBaseline[1].version -ne 'v1') { $taskBaseline | ConvertTo-Json -Depth 10; throw 'Healthy baseline failed; do not start the spike.' }
$taskBaseline[1] | ConvertTo-Json -Depth 20 | Set-Content "$taskDirectory/baseline.json" -Encoding utf8
$taskBaseline[1] | ConvertTo-Json -Depth 10
```

Expect about 10 RPS, zero errors, one replica and a successful measured baseline. Do not remeasure the baseline after starting the spike.

Verify evidence and quiet correlation before continuing:

```powershell
$taskSnapshot = @(D snapshot)
if ($taskSnapshot[0] -ne 200 -or $taskSnapshot[1].version -ne 'v1' -or $taskSnapshot[1].metrics.replica_count -ne 1 -or $taskSnapshot[1].metrics.http_5xx_rate -gt 0.01) { throw 'Healthy telemetry gate failed.' }
$taskStatus = D status
if ($taskStatus.m2.Count) {
    $taskLast = D m2detail $taskStatus.m2[0][0]
    if (([DateTimeOffset]::UtcNow - [DateTimeOffset]$taskLast.last_alert_at).TotalSeconds -le 120) { throw 'Wait for more than 120 seconds without an M2 alert, then rerun this check.' }
}
$taskKnownIds = @((Invoke-RestMethod "$taskM4/api/incidents/").incident_id)
```

The quiet period prevents the new spike joining an older correlated incident. If alerts persist during healthy traffic, inspect them rather than repeatedly injecting a spike.

## 5. Copy the website operator token

```powershell
$taskTokenEncoded = K get secret nexus-runtime -o jsonpath='{.data.M5_UI_OPERATOR_TOKEN}'
if (-not $taskTokenEncoded) { throw 'Dashboard operator token is not configured.' }
$taskOperatorToken = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($taskTokenEncoded -join '')))
Set-Clipboard -Value $taskOperatorToken
```

The token is on your clipboard, not printed. Paste it into the website's Operator token field when reviewing the new proposal. This is the dashboard operator token, not the Gemini key.

## 6. Start the spike yourself

```powershell
New-TrafficPod -Name $taskSpikePod -Rps 150 -Path "$taskDirectory/spike-traffic.yaml"
K logs $taskSpikePod --tail=2
```

The new generator adds 150 RPS to the existing 10 RPS. Configured total is approximately 160 RPS; offered/completed rates can differ under overload. Leave BOTH generators running through action and recovery. Load generation alone does not prove an incident exists or recovery succeeded.

## 7. Inspect and approve through the website

Open the Incidents view at http://127.0.0.1:18005/. Look for a NEW payment-service incident, using its recent timestamp/ID. Detection and diagnosis can take a few minutes. To list new incidents in the main terminal:

```powershell
Invoke-RestMethod "$taskM4/api/incidents/" | Where-Object { $_.incident_id -notin $taskKnownIds } | Format-Table incident_id, status, severity
```

On that incident, check:

- Status reaches AWAITING_APPROVAL.
- '2 · Diagnose: root cause and evidence' says traffic_spike.
- '3 · Decide: recommended action' says SCALE on payment-service, with a replica target greater than one and an unexpired proposal.
- Evidence provenance is real. FinOps shows the scale options/risk/cost.

Paste the copied token into Operator token, click Approve, and confirm the website's dialog. Approve within the five-minute proposal lifetime; if it expired, refresh and review a fresh proposal before approving. The exact replica count depends on observed CPU and the scale policy; do not assume a fixed count. The first step from one replica is limited to at most eight.

Do not manually scale the deployment to make the test pass. NERVA owns the test's scaling action. If diagnosis is unknown or the incident is ESCALATED, inspect its recorded reason and treat the run as unsuccessful.

## 8. Watch execution and measured recovery

Use the incident's '4 · Act: execution' and '5 · Recover: recovery validation' panels. Expected path: AWAITING_APPROVAL → EXECUTING → VALIDATING → RESOLVED. Wait at least about 90 seconds after action completion for the one-minute lookback and healthy hold; the configured recovery deadline is six minutes.

```powershell
K get deployment payment-service
$taskIncidentId = Read-Host 'Enter the NEW spike incident ID shown on the website'
$taskResult = Invoke-RestMethod "$taskM4/api/incidents/$taskIncidentId/context"
[pscustomobject]@{
    status = $taskResult.incident.status
    cause = $taskResult.rca.root_cause
    action = $taskResult.action_result.action
    action_status = $taskResult.action_result.status
    recovered = $taskResult.recovery_result.recovered
    slo_restored = $taskResult.recovery_result.slo_restored
    after = $taskResult.recovery_result.after
} | Format-List
```

Repeat the context check while watching the site. Success requires RESOLVED, action SCALE/SUCCESS, more than one Ready v1 replica, recovered=true, slo_restored=true, and continued traffic. A successful action alone is not a resolved incident. Save evidence after it becomes terminal:

```powershell
$taskResult = Invoke-RestMethod "$taskM4/api/incidents/$taskIncidentId/context"
if ($taskResult.incident.status -notin @('RESOLVED', 'ESCALATED')) { throw 'Recovery is still active; wait before cleanup.' }
$taskResult | ConvertTo-Json -Depth 50 | Set-Content "$taskDirectory/final-context.json" -Encoding utf8
```

If 150 RPS is absorbed without degraded latency, it is not a demonstrated capacity incident. Finish that run, stop its traffic, restore the checkpoint, and measure a new healthy baseline before a separate run with a different load. If scaling succeeds but the sustained load remains beyond the measured SLO, record the failed recovery instead of dropping traffic early to force RESOLVED.

## 9. Stop your traffic and restore the original demo settings

Run only after the selected incident is terminal and no other payment recovery is EXECUTING or VALIDATING. This cleanup is manual and does not rewrite the incident outcome.

```powershell
$taskActive = @(Invoke-RestMethod "$taskM4/api/incidents/") | Where-Object { $_.status -in @('EXECUTING', 'VALIDATING') }
if ($taskActive) { throw 'Another recovery is active; wait before restoring settings.' }
K delete pod $taskSpikePod $taskBaselinePod --ignore-not-found
$taskSaved = Get-Content "$taskDirectory/payment-before.json" -Raw | ConvertFrom-Json
$taskRestore = @(
    @{ op = 'replace'; path = '/spec/template'; value = $taskSaved.spec.template },
    @{ op = 'replace'; path = '/spec/replicas'; value = $taskSaved.spec.replicas }
)
$taskRestore | ConvertTo-Json -Depth 60 | Set-Content "$taskDirectory/restore.json" -Encoding utf8
K patch deployment payment-service --type json --patch-file "$taskDirectory/restore.json"
K rollout status deployment/payment-service --timeout=300s
```

You can leave the API/website forwards running. Ctrl+C in a dedicated forward terminal stops that connection only. For another test, start at the beginning with a new run name and fresh baseline.
