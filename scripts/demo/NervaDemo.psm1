# Shared helpers for the two live demo scripts (Windows PowerShell 5.1 compatible).
# Talks to M1-M5 through the Kubernetes API (kubectl get --raw / exec), so it does not
# depend on the browser port-forwards. Credentials are never printed.

$ErrorActionPreference = 'Stop'
$script:Namespace = 'nexus-demo'
$script:Modules = @('telemetry-intelligence', 'anomaly-engine', 'root-cause-analysis',
                    'shared-nexus-api', 'incident-memory', 'finops-engine')
$script:BaselineGenerator = 'payment-traffic'
$script:SpikeGenerator = 'payment-spike-load'

function Write-Step([string]$Text) { Write-Host ""; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "    OK  $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

function Initialize-NervaDemo {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$Scenario)
    $script:Root = $Root
    $script:Kubectl = Join-Path $Root '.review-branches/tools/kubectl.exe'
    $script:Kind = Join-Path $Root '.review-branches/tools/kind.exe'
    $script:Kubeconfig = Join-Path $Root '.review-branches/spike-kubeconfig.yaml'
    foreach ($tool in @($script:Kubectl, $script:Kind)) {
        if (-not (Test-Path -LiteralPath $tool)) { throw "Missing tool: $tool" }
    }
    $script:RunDir = Join-Path $Root (".review-branches/demo-runs/$Scenario-" + [DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $script:RunDir -Force | Out-Null

    Write-Step 'Connecting to Docker and the kind cluster'
    $os = docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or $os -ne 'linux') { throw 'Docker Desktop (Linux engine) is not running.' }
    docker start nexus-control-plane nexus-worker nexus-worker2 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the kind node containers.' }
    $config = & $script:Kind get kubeconfig --name nexus
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the kind kubeconfig.' }
    $config | Set-Content -LiteralPath $script:Kubeconfig -Encoding utf8
    K wait --for=condition=Ready nodes --all --timeout=180s | Out-Null
    foreach ($module in $script:Modules) { K rollout status "deployment/$module" --timeout=300s | Out-Null }
    Write-Ok "cluster ready; evidence folder: $script:RunDir"
}

function K {
    $flags = @('--kubeconfig', $script:Kubeconfig, '--context', 'kind-nexus', '-n', $script:Namespace)
    if ($MyInvocation.ExpectingInput) { $output = $input | & $script:Kubectl @flags @args }
    else { $output = & $script:Kubectl @flags @args }
    if ($LASTEXITCODE -ne 0) { throw "kubectl $($args -join ' ') failed." }
    $output
}

# A JSON array is emitted element by element; callers wrap with @() when they need a list.
function ConvertFrom-JsonText($Lines) {
    $value = ConvertFrom-Json -InputObject (($Lines | Out-String))
    foreach ($item in @($value)) { $item }
}

function Invoke-Api([string]$Service, [string]$Path) {
    # M4 requires a token for reads (NEXUS_REQUIRE_READ_AUTH); probes stay open. A Kubernetes API
    # proxy request cannot carry that header, so M4 reads run inside the M4 pod with its own token.
    if ($Service -like 'shared-nexus-api*' -and $Path -notin @('/ready', '/live', '/health')) {
        $code = "import json, os, urllib.request`n" +
            "token = os.environ.get('NEXUS_READ_TOKEN') or os.environ['NEXUS_INGEST_TOKEN']`n" +
            "request = urllib.request.Request('http://127.0.0.1:8004' + '$Path', headers={'Authorization': 'Bearer ' + token})`n" +
            "print(urllib.request.urlopen(request, timeout=30).read().decode())"
        return ConvertFrom-JsonText ($code | K exec -i deployment/shared-nexus-api '--' python -)
    }
    ConvertFrom-JsonText (K get --raw "/api/v1/namespaces/$script:Namespace/services/${Service}/proxy$Path")
}

function Invoke-Drill {
    $code = Get-Content -LiteralPath (Join-Path $script:Root 'scripts/live-drill.py') -Raw
    ConvertFrom-JsonText ($code | K exec -i deployment/shared-nexus-api '--' python - @args)
}

function Get-Incidents { Invoke-Api 'shared-nexus-api:8004' '/api/incidents/' }

function Start-BrowserForwards {
    Write-Step 'Opening browser port-forwards (one window each, reconnecting automatically)'
    foreach ($forward in @(@{ Service = 'shared-nexus-api'; Ports = '18004:8004' },
                           @{ Service = 'incident-memory'; Ports = '18005:8005' },
                           @{ Service = 'grafana'; Ports = '13000:3000' })) {
        $local = [int]($forward.Ports.Split(':')[0])
        if (Get-NetTCPConnection -LocalPort $local -State Listen -ErrorAction SilentlyContinue) {
            Write-Ok "port $local already forwarded"
            continue
        }
        $loop = "`$Host.UI.RawUI.WindowTitle = 'NERVA forward $($forward.Service) $local'; while (`$true) { " +
                "& '$script:Kubectl' --kubeconfig '$script:Kubeconfig' --context kind-nexus -n $script:Namespace " +
                "port-forward service/$($forward.Service) $($forward.Ports) --address 127.0.0.1; " +
                "Write-Host 'Forward dropped (pod replaced or restarted); reconnecting in 2 s'; Start-Sleep 2 }"
        Start-Process powershell.exe -ArgumentList @('-NoExit', '-NoProfile', '-Command', $loop) | Out-Null
        Write-Ok "forward window opened for $($forward.Service) on 127.0.0.1:$local"
    }
}

function Assert-StackReady {
    Write-Step 'Checking NERVA readiness'
    $ready = Invoke-Api 'shared-nexus-api:8004' '/ready'
    if ($ready.status -ne 'ok' -or $ready.remediation_backend -ne 'kubernetes') {
        throw "M4 is not ready for real Kubernetes remediation (status=$($ready.status), backend=$($ready.remediation_backend))."
    }
    $ui = Invoke-Api 'incident-memory:8005' '/internal/ui/config'
    if ($ui.source -ne 'real' -or -not $ui.approval_enabled) { throw 'The website is not in live mode with approval enabled.' }
    Write-Ok 'M4 real remediation and website approval are ready'
}

function Assert-NoActiveWork {
    $busy = @(Get-Incidents | Where-Object { $_.status -in @('AWAITING_APPROVAL', 'EXECUTING', 'VALIDATING') })
    if ($busy.Count) {
        $busy | Format-Table incident_id, status | Out-Host
        throw 'An incident is still awaiting approval, executing or validating. Let it finish (or reject it on the website) first.'
    }
}

function Get-PaymentDeployment { ConvertFrom-JsonText (K get deployment payment-service -o json) }

function Save-HealthyCheckpoint {
    $checkpoint = Join-Path $script:Root '.review-branches/demo-runs/payment-v1-template.json'
    $deployment = Get-PaymentDeployment
    $container = $deployment.spec.template.spec.containers | Where-Object { $_.name -eq 'payment-service' }
    if ($deployment.spec.template.metadata.labels.version -eq 'v1' -and $container.image -like 'nexus/payment-service:v1*') {
        $deployment.spec.template | ConvertTo-Json -Depth 60 | Set-Content -LiteralPath $checkpoint -Encoding utf8
        Write-Ok "healthy v1 template saved ($($container.image))"
        return
    }
    if (-not (Test-Path -LiteralPath $checkpoint)) {
        throw 'payment-service is not on v1 and no healthy v1 checkpoint exists. Restore v1 manually once, then rerun.'
    }
    Write-Note 'payment-service is not on v1; restoring the saved healthy v1 template'
    $template = Get-Content -LiteralPath $checkpoint -Raw | ConvertFrom-Json
    $patch = Join-Path $script:RunDir 'restore-v1.json'
    ConvertTo-Json -InputObject @(@{ op = 'replace'; path = '/spec/template'; value = $template }) -Depth 60 |
        Set-Content -LiteralPath $patch -Encoding utf8
    K patch deployment payment-service --type json --patch-file $patch | Out-Null
}

function Reset-PaymentService([int]$Replicas) {
    Write-Step "Resetting payment-service to healthy v1 with $Replicas replica(s)"
    Save-HealthyCheckpoint
    K set env deployment/payment-service PAYMENT_MAX_CONCURRENCY=2 PAYMENT_WORK_ITERATIONS=20000 FAULT_LATENCY_MS=0 FAULT_5XX_RATE=0 | Out-Null
    K scale deployment/payment-service "--replicas=$Replicas" | Out-Null
    K rollout status deployment/payment-service --timeout=300s | Out-Null
    $deployment = Get-PaymentDeployment
    if ($deployment.status.readyReplicas -ne $Replicas -or $deployment.spec.template.metadata.labels.version -ne 'v1') {
        throw "payment-service did not settle at $Replicas ready v1 replica(s)."
    }
    Write-Ok "$Replicas ready v1 replica(s)"
    Write-Step 'Restarting M2 so old alert correlation cannot absorb the new fault'
    K rollout restart deployment/anomaly-engine | Out-Null
    K rollout status deployment/anomaly-engine --timeout=300s | Out-Null
    Write-Ok 'M2 restarted'
}

function Start-TrafficGenerator([string]$Name, [int]$Rps, [int]$DurationSeconds) {
    K create configmap payment-traffic-script "--from-file=k8s-traffic.py=$(Join-Path $script:Root 'scripts/k8s-traffic.py')" --dry-run=client -o json |
        K apply -f - | Out-Null
    K delete pod $Name --ignore-not-found --wait=true | Out-Null
    $env = @(
        @{ name = 'TARGET_URL'; value = 'http://payment-service/pay' }, @{ name = 'RPS'; value = "$Rps" },
        @{ name = 'DURATION_SECONDS'; value = "$DurationSeconds" }, @{ name = 'MAX_IN_FLIGHT'; value = '500' },
        @{ name = 'REQUEST_TIMEOUT_SECONDS'; value = '10' }, @{ name = 'REPORT_INTERVAL_SECONDS'; value = '10' })
    $pod = @{
        apiVersion = 'v1'; kind = 'Pod'
        metadata = @{ name = $Name; namespace = $script:Namespace; labels = @{ app = 'payment-traffic'; service_name = 'payment-traffic' } }
        spec = @{
            automountServiceAccountToken = $false; restartPolicy = 'Never'; terminationGracePeriodSeconds = 45
            containers = @(@{ name = 'traffic'; image = 'nexus/payment-service:v1'; imagePullPolicy = 'IfNotPresent'
                command = @('python', '/traffic/k8s-traffic.py'); env = $env
                resources = @{ requests = @{ cpu = '100m'; memory = '64Mi' }; limits = @{ cpu = '1'; memory = '384Mi' } }
                volumeMounts = @(@{ name = 'script'; mountPath = '/traffic'; readOnly = $true }) })
            volumes = @(@{ name = 'script'; configMap = @{ name = 'payment-traffic-script' } })
        }
    }
    $file = Join-Path $script:RunDir "$Name.json"
    $pod | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $file -Encoding utf8
    K apply -f $file | Out-Null
    K wait --for=condition=Ready "pod/$Name" --timeout=120s | Out-Null
    Write-Ok "$Name running at $Rps requests/s"
}

function Stop-TrafficGenerator([string]$Name) { K delete pod $Name --ignore-not-found | Out-Null; Write-Ok "$Name stopped" }

function Measure-HealthyBaseline([int]$Replicas, [int]$WarmupMinutes) {
    Write-Step "Healthy warm-up: $WarmupMinutes minute(s) at 10 requests/s (stay off camera)"
    for ($minute = 1; $minute -le $WarmupMinutes; $minute++) {
        Write-Host "    minute $minute of $WarmupMinutes"
        Start-Sleep -Seconds 60
    }
    Write-Step 'Measuring the healthy baseline (M1)'
    $result = @(Invoke-Drill baseline)
    $baseline = $result[1]
    if ($result[0] -ne 200 -or $baseline.version -ne 'v1' -or $baseline.sample_count -lt 5 -or
        $baseline.replica_count.minimum -ne $Replicas -or $baseline.replica_count.maximum -ne $Replicas -or
        $baseline.request_rate.average -lt 8 -or $baseline.request_rate.average -gt 12 -or
        $baseline.http_5xx_rate.maximum -ne 0) {
        $result | ConvertTo-Json -Depth 12 | Out-Host
        throw 'Healthy baseline failed. Do not inject; let traffic run longer and rerun.'
    }
    $baseline | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $script:RunDir 'baseline.json') -Encoding utf8
    Write-Ok ("baseline: {0:N1} req/s, p95 {1:N2} ms, recovery ceiling {2:N2} ms, {3} samples" -f
        $baseline.request_rate.average, $baseline.latency_p95_ms.average,
        $baseline.thresholds.latency_p95_ms_max, $baseline.sample_count)

    $check = @'
import json, urllib.request
with urllib.request.urlopen("http://telemetry-intelligence:8001/internal/evidence/preview?service=payment-service", timeout=30) as r:
    data = json.load(r)
if data.get("provider_mode") != "real" or data.get("provider_errors") != []:
    raise SystemExit("Evidence providers are not ready: " + str(data.get("provider_errors")))
print("ok")
'@
    $check | K exec -i deployment/shared-nexus-api '--' python - | Out-Null
    Write-Ok 'real telemetry, logs and Kubernetes evidence available'
}

function Wait-M2Quiet([int]$MaxMinutes = 6) {
    Write-Step 'Waiting until M2 has been quiet for over 120 s (prevents joining an old incident)'
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($MaxMinutes)
    while ($true) {
        $status = Invoke-Drill status
        $quiet = $true
        if ($status.m2 -and @($status.m2).Count) {
            $last = Invoke-Drill m2detail $status.m2[0][0]
            if ($last.last_alert_at) {
                $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]$last.last_alert_at).TotalSeconds
                $quiet = $age -gt 120
            }
        }
        if ($quiet) { Write-Ok 'M2 is quiet'; return }
        if ([DateTimeOffset]::UtcNow -gt $deadline) { throw 'M2 kept alerting on healthy traffic. Investigate before injecting.' }
        Write-Host '    recent M2 alert; checking again in 20 s'
        Start-Sleep -Seconds 20
    }
}

function Copy-OperatorToken {
    $encoded = K get secret nexus-runtime -o 'jsonpath={.data.M5_UI_OPERATOR_TOKEN}'
    if (-not $encoded) { throw 'The website operator token is missing from secret nexus-runtime.' }
    Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($encoded -join ''))))
    Write-Ok 'website operator token copied to the clipboard (not shown)'
}

function Wait-ForRecording([string]$Action) {
    Write-Host ""
    Write-Host "READY. Start recording, show the NERVA Overview and the healthy Grafana panels." -ForegroundColor Green
    Write-Host "Website: http://127.0.0.1:18005/   Grafana: http://127.0.0.1:13000/d/nexus-m1-overview/nexus-m1-service-overview"
    [void](Read-Host "Press Enter to $Action")
}

function Watch-NewIncident {
    param([string[]]$KnownIds, [string]$ExpectedCause, [string]$ExpectedAction, [int]$TimeoutMinutes = 20)
    Write-Step "Watching for the new incident (expected: $ExpectedCause -> $ExpectedAction). Ctrl+C stops watching only."
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $incidentId = $null; $lastStatus = $null; $announced = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if (-not $incidentId) {
            $new = @(Get-Incidents) | Where-Object { $_.incident_id -notin $KnownIds -and 'payment-service' -in $_.affected_services } |
                Sort-Object started_at -Descending | Select-Object -First 1
            if ($new) { $incidentId = $new.incident_id; Write-Ok "new incident $incidentId" }
        }
        if ($incidentId) {
            $context = Invoke-Api 'shared-nexus-api:8004' "/api/incidents/$incidentId/context"
            $status = $context.incident.status
            if ($status -ne $lastStatus) {
                Write-Host ("    {0}  {1}" -f [DateTime]::Now.ToString('HH:mm:ss'), $status)
                $lastStatus = $status
            }
            if ($status -eq 'AWAITING_APPROVAL' -and -not $announced) {
                $announced = $true
                Write-Host ("    cause: {0} ({1})   proposal: {2} {3}   risk: {4}" -f $context.rca.root_cause, $context.rca.confidence,
                    $context.decision.recommended_action, ($context.decision.parameters | ConvertTo-Json -Compress), $context.decision.risk)
                if ($context.rca.root_cause -ne $ExpectedCause -or $context.decision.recommended_action -ne $ExpectedAction) {
                    Write-Note "This is not the expected $ExpectedCause -> $ExpectedAction proposal. Review it before approving, or Reject it."
                }
                Write-Host '    >>> Open this incident on the website, paste the token (Ctrl+V) and click Approve within 5 minutes.' -ForegroundColor Magenta
            }
            if ($status -in @('RESOLVED', 'ESCALATED')) {
                $context | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $script:RunDir 'final-context.json') -Encoding utf8
                $recovery = $context.recovery_result
                Write-Step "Finished: $incidentId is $status"
                [pscustomobject]@{
                    cause = $context.rca.root_cause; action = $context.action_result.action
                    action_status = $context.action_result.status; recovered = $recovery.recovered
                    slo_restored = $recovery.slo_restored; p95_before_ms = $recovery.before.latency_p95_ms
                    p95_after_ms = $recovery.after.latency_p95_ms; recovery_seconds = $recovery.recovery_time_seconds
                } | Format-List | Out-Host
                Write-Ok "evidence saved in $script:RunDir"
                return $context
            }
        }
        Start-Sleep -Seconds 10
    }
    Write-Note "Stopped watching after $TimeoutMinutes minutes. Check the website; nothing was changed."
}

Export-ModuleMember -Function Initialize-NervaDemo, Start-BrowserForwards, K, Invoke-Api, Get-Incidents, Assert-StackReady, Assert-NoActiveWork,
    Get-PaymentDeployment, Reset-PaymentService, Start-TrafficGenerator, Stop-TrafficGenerator, Measure-HealthyBaseline,
    Wait-M2Quiet, Copy-OperatorToken, Wait-ForRecording, Watch-NewIncident, Write-Step, Write-Ok, Write-Note
