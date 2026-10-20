<#
.SYNOPSIS
    NERVA / NEXUS Pre-Flight Diagnostic Doctor
    Validates host resources, Docker engine, Kind cluster conditions,
    zombie workloads, and service endpoint connectivity before demos or drills.

.PARAMETER Fix
    Deletes finished pods (phase Failed or Succeeded) in the namespace, gracefully. Pods stuck
    terminating are only reported: force-deleting them can leave two copies of a StatefulSet pod.
    -Fix refuses to run unless the context is kind-nexus.

.PARAMETER AutoForward
    Automatically launches local port-forwards for M4 (18004) and M5 (18005) if closed.

.PARAMETER MinFreeDiskGB
    Minimum expected free disk space on C: drive (default: 15 GB).
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$AutoForward,
    [double]$MinFreeDiskGB = 15.0,
    [string]$Namespace = 'nexus-demo',
    [string]$KubeconfigPath = ''
)

$ErrorActionPreference = 'Continue'
$projectRoot = Split-Path -Parent $PSScriptRoot
$ExpectedContext = 'kind-nexus'
$ExpectedDeployments = @('payment-service', 'checkout-service', 'telemetry-intelligence', 'anomaly-engine',
    'root-cause-analysis', 'shared-nexus-api', 'incident-memory', 'finops-engine', 'prometheus', 'loki', 'grafana')

if (-not $KubeconfigPath) {
    $candidate = Join-Path $projectRoot '.review-branches/spike-kubeconfig.yaml'
    if (Test-Path -LiteralPath $candidate) {
        $KubeconfigPath = $candidate
    } elseif ($env:KUBECONFIG -and (Test-Path -LiteralPath $env:KUBECONFIG)) {
        $KubeconfigPath = $env:KUBECONFIG
    }
}

function Write-CheckHeader([string]$Title) {
    Write-Host "`n=== [ $Title ] ===" -ForegroundColor Cyan
}

function Report-Item([string]$Name, [bool]$Ok, [string]$Details, [bool]$Warning = $false) {
    $statusText = if ($Ok) { "[ PASS ]" } elseif ($Warning) { "[ WARN ]" } else { "[ FAIL ]" }
    $color = if ($Ok) { "Green" } elseif ($Warning) { "Yellow" } else { "Red" }
    Write-Host ("{0,-9} {1,-35} : {2}" -f $statusText, $Name, $Details) -ForegroundColor $color
    return $Ok
}

$allPassed = $true
$warningsFound = $false

# ---------------------------------------------------------
# 1. Host Resources (Disk Free Space)
# ---------------------------------------------------------
Write-CheckHeader "1. Host Operating Environment"
try {
    $driveC = Get-PSDrive C -ErrorAction SilentlyContinue
    if ($driveC) {
        $freeGB = [math]::Round($driveC.Free / 1GB, 2)
        if ($freeGB -ge $MinFreeDiskGB) {
            Report-Item "C: Drive Space" $true "$freeGB GB free (minimum: $MinFreeDiskGB GB)" | Out-Null
        } elseif ($freeGB -ge 5.0) {
            $warningsFound = $true
            Report-Item "C: Drive Space" $false "$freeGB GB free (< $MinFreeDiskGB GB). Run 'docker system prune -a --volumes' or clean temp files before live demo." -Warning $true | Out-Null
        } else {
            $allPassed = $false
            Report-Item "C: Drive Space" $false "CRITICAL: $freeGB GB free (< 5 GB). Kubernetes DiskPressure will evict pods soon!" | Out-Null
        }
    } else {
        Report-Item "C: Drive Space" $false "Unable to query C: drive" | Out-Null
    }
} catch {
    Report-Item "C: Drive Space" $false $_.Exception.Message | Out-Null
}

# ---------------------------------------------------------
# 2. Docker Linux Engine
# ---------------------------------------------------------
Write-CheckHeader "2. Container Engine (Docker)"
try {
    $dockerInfo = docker info --format '{{.OSType}}|{{.ServerErrors}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $dockerInfo) {
        $osType = ($dockerInfo -split '\|')[0].Trim()
        if ($osType -match 'linux') {
            Report-Item "Docker Engine" $true "Running (OSType: $osType)" | Out-Null
        } else {
            $allPassed = $false
            Report-Item "Docker Engine" $false "Engine is running in $osType mode instead of Linux containers" | Out-Null
        }
    } else {
        $allPassed = $false
        Report-Item "Docker Engine" $false "Docker daemon is not running or not responding to 'docker info'" | Out-Null
    }
} catch {
    $allPassed = $false
    Report-Item "Docker Engine" $false $_.Exception.Message | Out-Null
}

# ---------------------------------------------------------
# 3. Kubernetes / Kind Cluster Conditions
# ---------------------------------------------------------
Write-CheckHeader "3. Kubernetes Cluster Health"
# The repository pins its own kubectl; PATH is only a fallback.
$pinnedKubectl = Join-Path $projectRoot '.review-branches/tools/kubectl.exe'
$kubectlExe = if (Test-Path -LiteralPath $pinnedKubectl) { $pinnedKubectl } else { 'kubectl' }
$kubectlCmd = @($kubectlExe, "--context=$ExpectedContext")
if ($KubeconfigPath) { $kubectlCmd += "--kubeconfig=$KubeconfigPath" }
$contextOk = $false
try {
    $contexts = & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)] | Where-Object { $_ -notlike '--context=*' }) config get-contexts -o name 2>$null
    $contextOk = ($LASTEXITCODE -eq 0) -and (@($contexts) -contains $ExpectedContext)
} catch { $contextOk = $false }
if ($contextOk) {
    Report-Item "Context" $true "Using $ExpectedContext explicitly (never the default context)" | Out-Null
} else {
    $allPassed = $false
    Report-Item "Context" $false "Context $ExpectedContext not found in the kubeconfig; nothing will be changed" | Out-Null
    if ($Fix) { Write-Host "-Fix disabled: the $ExpectedContext context is required." -ForegroundColor Red; $Fix = $false }
}

try {
    $nodesJson = & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)]) get nodes -o json 2>$null
    if ($LASTEXITCODE -eq 0 -and $nodesJson) {
        $nodes = ($nodesJson | ConvertFrom-Json).items
        $cfgDisplay = if ($KubeconfigPath) { $KubeconfigPath } else { 'default kubeconfig' }
        Report-Item "Cluster Reachable" $true "Connected via $cfgDisplay ($($nodes.Count) nodes)" | Out-Null

        foreach ($node in $nodes) {
            $nodeName = $node.metadata.name
            $conds = $node.status.conditions
            $readyCond = $conds | Where-Object { $_.type -eq 'Ready' }
            $diskPressure = $conds | Where-Object { $_.type -eq 'DiskPressure' }
            $memPressure = $conds | Where-Object { $_.type -eq 'MemoryPressure' }

            $isReady = ($readyCond.status -eq 'True')
            $hasDiskPressure = ($diskPressure.status -eq 'True')
            $hasMemPressure = ($memPressure.status -eq 'True')

            if ($isReady -and -not $hasDiskPressure -and -not $hasMemPressure) {
                Report-Item "Node: $nodeName" $true "Ready (Memory/Disk pressure: False)" | Out-Null
            } else {
                $allPassed = $false
                $issues = @()
                if (-not $isReady) { $issues += "Not Ready" }
                if ($hasDiskPressure) { $issues += "DiskPressure" }
                if ($hasMemPressure) { $issues += "MemoryPressure" }
                Report-Item "Node: $nodeName" $false ("Node issue: " + ($issues -join ', ')) | Out-Null
            }
        }
    } else {
        $allPassed = $false
        Report-Item "Cluster Reachable" $false "Failed to query nodes with $($kubectlCmd -join ' ')" | Out-Null
    }
} catch {
    $allPassed = $false
    Report-Item "Cluster Reachable" $false $_.Exception.Message | Out-Null
}

# ---------------------------------------------------------
# 4. Workload State & Zombie Pod Detection
# ---------------------------------------------------------
Write-CheckHeader "4. Workload & Zombie Pod Verification ($Namespace)"
$zombiePods = @()
try {
    $podsJson = & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)]) get pods -n $Namespace -o json 2>$null
    if ($LASTEXITCODE -eq 0 -and $podsJson) {
        $pods = ($podsJson | ConvertFrom-Json).items
        $stuckTerminating = @()
        foreach ($pod in $pods) {
            # Finished pods (e.g. completed load generators) are safe to clean; Kubernetes has no "Completed" phase.
            if ($pod.metadata.deletionTimestamp) { $stuckTerminating += $pod.metadata.name }
            elseif ($pod.status.phase -in @('Failed', 'Succeeded', 'Unknown')) { $zombiePods += $pod.metadata.name }
        }

        # Every expected workload must have all its desired replicas ready.
        $deployments = @{}
        $deployJson = & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)]) get deployments -n $Namespace -o json 2>$null
        if ($LASTEXITCODE -eq 0 -and $deployJson) {
            foreach ($d in ($deployJson | ConvertFrom-Json).items) { $deployments[$d.metadata.name] = $d }
        }
        foreach ($name in $ExpectedDeployments) {
            $d = $deployments[$name]
            if (-not $d) {
                $allPassed = $false
                Report-Item "Deployment: $name" $false "missing" | Out-Null
            } elseif ([int]$d.status.readyReplicas -ge [int]$d.spec.replicas -and [int]$d.spec.replicas -gt 0) {
                Report-Item "Deployment: $name" $true "$($d.status.readyReplicas)/$($d.spec.replicas) ready" | Out-Null
            } else {
                $allPassed = $false
                Report-Item "Deployment: $name" $false "$([int]$d.status.readyReplicas)/$($d.spec.replicas) ready" | Out-Null
            }
        }

        if ($stuckTerminating.Count -gt 0) {
            $warningsFound = $true
            Report-Item "Stuck Terminating" $false "$($stuckTerminating -join ', '). Not force-deleted automatically: check the node first (a forced delete can duplicate a StatefulSet pod)." -Warning $true | Out-Null
        }
        if ($zombiePods.Count -gt 0) {
            if ($Fix) {
                Write-Host "Deleting $($zombiePods.Count) finished pod(s) gracefully..." -ForegroundColor Yellow
                foreach ($zombie in $zombiePods) {
                    & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)]) delete pod $zombie -n $Namespace --wait=false 2>$null
                    Write-Host "  deleted $zombie" -ForegroundColor Green
                }
                Report-Item "Finished Pods" $true "Deleted $($zombiePods.Count) finished pod(s)" | Out-Null
            } else {
                $warningsFound = $true
                Report-Item "Finished Pods" $false "$($zombiePods.Count) finished pod(s): $($zombiePods -join ', '). Run with -Fix to delete them." -Warning $true | Out-Null
            }
        } elseif ($stuckTerminating.Count -eq 0) {
            Report-Item "Finished Pods" $true "No finished or stuck pods" | Out-Null
        }

        # M4 must be the ready, elected leader (read through the API server: no port-forward needed).
        $m4 = & $kubectlCmd[0] @($kubectlCmd[1..($kubectlCmd.Length-1)]) get --raw "/api/v1/namespaces/$Namespace/services/shared-nexus-api:8004/proxy/ready" 2>$null
        if ($LASTEXITCODE -eq 0 -and $m4) {
            $ready = $m4 | ConvertFrom-Json
            Report-Item "M4 Leader" $true "status $($ready.status), backend $($ready.remediation_backend), writer generation $($ready.workflow_storage.lease_generation)" | Out-Null
        } else {
            $allPassed = $false
            Report-Item "M4 Leader" $false "/ready is not 200 (not ready, or a standby without a leader)" | Out-Null
        }
    }
} catch {
    Report-Item "Workloads" $false $_.Exception.Message | Out-Null
}

# ---------------------------------------------------------
# 5. Service Endpoints & Port-Forward Verification
# ---------------------------------------------------------
Write-CheckHeader "5. Local Endpoint Connectivity"
$endpoints = @(
    @{ Name = "M4 (shared-nexus-api)"; Port = 18004; Path = "/health"; Service = "service/shared-nexus-api 18004:8004" },
    @{ Name = "M5 (incident-memory)";  Port = 18005; Path = "/health"; Service = "service/incident-memory 18005:8005" }
)

foreach ($ep in $endpoints) {
    $url = "http://127.0.0.1:$($ep.Port)$($ep.Path)"
    try {
        $res = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
        $resStatus = if ($res.status) { $res.status } else { 'ok' }
        Report-Item $ep.Name $true "Responding on port $($ep.Port) ($resStatus)" | Out-Null
    } catch {
        if ($AutoForward) {
            Write-Host "Launching port-forward for $($ep.Name)..." -ForegroundColor Yellow
            $pfArgs = @($kubectlCmd[1..($kubectlCmd.Length-1)]) + @('port-forward', '-n', $Namespace, ($ep.Service -split ' ')[0], ($ep.Service -split ' ')[1], '--address', '127.0.0.1')
            Start-Process -FilePath $kubectlCmd[0] -ArgumentList $pfArgs -WindowStyle Hidden
            Start-Sleep -Seconds 2
            try {
                $retry = Invoke-RestMethod -Uri $url -TimeoutSec 3 -ErrorAction Stop
                Report-Item $ep.Name $true "Connected on port $($ep.Port) via auto-forward" | Out-Null
            } catch {
                Report-Item $ep.Name $false "Failed to connect to port $($ep.Port). Run: kubectl $($pfArgs -join ' ')" | Out-Null
            }
        } else {
            Report-Item $ep.Name $false "Port $($ep.Port) closed. Start forward with: kubectl --kubeconfig=$KubeconfigPath port-forward -n $Namespace $($ep.Service) --address 127.0.0.1" -Warning $true | Out-Null
            $warningsFound = $true
        }
    }
}

# ---------------------------------------------------------
# 6. Runtime Configuration & Token Consistency
# ---------------------------------------------------------
Write-CheckHeader "6. Security & Credentials Check"
$runtimeJson = Join-Path $projectRoot '.review-branches/stack-runtime.json'
if (Test-Path -LiteralPath $runtimeJson) {
    try {
        $rt = Get-Content -LiteralPath $runtimeJson -Raw | ConvertFrom-Json
        $hasUiToken = -not [string]::IsNullOrWhiteSpace($rt.M5_UI_OPERATOR_TOKEN)
        $hasApprToken = -not [string]::IsNullOrWhiteSpace($rt.NEXUS_APPROVAL_TOKEN)
        if ($hasUiToken -and $hasApprToken) {
            Report-Item "Runtime Tokens" $true "Valid operator tokens present in .review-branches/stack-runtime.json" | Out-Null
        } else {
            Report-Item "Runtime Tokens" $false "One or more runtime tokens are missing or empty" | Out-Null
            $allPassed = $false
        }
    } catch {
        Report-Item "Runtime Tokens" $false $_.Exception.Message | Out-Null
    }
} else {
    Report-Item "Runtime Tokens" $false "Missing .review-branches/stack-runtime.json" -Warning $true | Out-Null
}

Write-Host "`n-----------------------------------------------------------"
if ($allPassed -and -not $warningsFound) {
    Write-Host "Doctor status: ALL SYSTEMS HEALTHY. Stack is ready for live drills." -ForegroundColor Green
} elseif ($allPassed) {
    Write-Host "Doctor status: PASSED WITH WARNINGS. Review items marked [ WARN ]." -ForegroundColor Yellow
} else {
    Write-Host "Doctor status: ISSUES DETECTED. Resolve items marked [ FAIL ] before live drills." -ForegroundColor Red
}
Write-Host "-----------------------------------------------------------`n"
if (-not $allPassed) { exit 1 }
exit 0
