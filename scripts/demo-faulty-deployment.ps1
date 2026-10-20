<#
.SYNOPSIS
  Live demo, Scenario A: faulty v2 release -> NERVA proposes ROLLBACK -> you approve on the website -> measured recovery.

.DESCRIPTION
  Off camera it resets payment-service to healthy v1 (default 3 replicas), runs 10 requests/s,
  measures the healthy baseline and waits until M2 is quiet. It then pauses so you can start
  recording. Press Enter to release a faulty v2 (+600 ms latency, 14% errors). Every run gets a
  unique release id, so M1 sees a new ReplicaSet and a fresh deployment event (re-applying the
  same release would be diagnosed "unknown"). It follows the incident, prompts you to approve on
  the website (token on your clipboard) and saves M1's measured result. The rollback itself is
  done by NERVA after your approval, never by this script.

.EXAMPLE
  .\scripts\demo-faulty-deployment.ps1
.EXAMPLE
  .\scripts\demo-faulty-deployment.ps1 -Replicas 8
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 10)][int]$Replicas = 3,
    [int]$WarmupMinutes = 4,
    [switch]$NoForwards,
    [switch]$Rehearse   # practice run: no pauses; approves through the website gateway as a click would
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
Import-Module (Join-Path $PSScriptRoot 'demo/NervaDemo.psm1') -Force

Initialize-NervaDemo -Root $root -Scenario 'faulty-deployment'
foreach ($node in @('nexus-worker', 'nexus-worker2')) {
    $images = docker exec $node crictl images
    if (-not ($images | Select-String -SimpleMatch 'nexus/payment-service' | Select-String -Pattern '\sv2\s')) {
        throw "The faulty image nexus/payment-service:v2 is not loaded on $node. Load it with kind load docker-image first."
    }
}
if (-not $NoForwards) { Start-BrowserForwards }
Assert-StackReady
Assert-NoActiveWork

Stop-TrafficGenerator 'payment-spike-load'
Reset-PaymentService -Replicas $Replicas
Write-Step 'Starting healthy baseline traffic'
Start-TrafficGenerator -Name 'payment-traffic' -Rps 10 -DurationSeconds 86400
Measure-HealthyBaseline -Replicas $Replicas -WarmupMinutes $WarmupMinutes
Wait-M2Quiet
$known = @(@(Get-Incidents) | ForEach-Object { $_.incident_id })
if (-not $Rehearse) { Copy-OperatorToken }

if (-not $Rehearse) { Wait-ForRecording 'release the faulty v2 version' }
Assert-NoActiveWork
$run = [DateTimeOffset]::UtcNow.ToString('yyyyMMdd-HHmmss')
Write-Step "Releasing faulty v2 (release id demo-faulty-$run)"
# Only the release fields change; resources, probes and processing settings stay as in healthy v1.
$patch = @{ spec = @{ template = @{
    metadata = @{ labels = @{ version = 'v2' }; annotations = @{
        'nexus.io/previous-version' = 'v1'; 'nexus.io/version' = 'v2'
        'nexus.io/commit-sha' = "demo-faulty-$run"; 'nexus.io/pipeline-id' = "demo-faulty-$run" } }
    spec = @{ containers = @(@{ name = 'payment-service'; image = 'nexus/payment-service:v2'; env = @(
        @{ name = 'VERSION'; value = 'v2' }, @{ name = 'FAULT_LATENCY_MS'; value = '600' },
        @{ name = 'FAULT_5XX_RATE'; value = '0.14' }) }) }
} } }
$patchFile = Join-Path (Get-Location) ".review-branches/demo-runs/fault-patch-$run.json"
$patch | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $patchFile -Encoding utf8
K patch deployment payment-service --type strategic --patch-file $patchFile | Out-Null
K rollout status deployment/payment-service --timeout=300s | Out-Null
Write-Ok 'faulty v2 is live'
Write-Note 'Expected: detection within about a minute, then a ROLLBACK v2 -> v1 proposal after the 60 s settle.'

$approve = if ($Rehearse) { { param($id, $context) Approve-ThroughWebsite $id } } else { $null }
$result = Watch-NewIncident -KnownIds $known -ExpectedCause 'faulty_deployment' -ExpectedAction 'ROLLBACK' -OnAwaitingApproval $approve
$deployment = Get-PaymentDeployment
Write-Host ("payment-service now: version {0}, {1}/{2} ready" -f $deployment.spec.template.metadata.labels.version,
    $deployment.status.readyReplicas, $deployment.spec.replicas)
if (-not $Rehearse) { [void](Read-Host 'Show the result on camera, then press Enter to reset for the next run') }
Assert-NoActiveWork
Reset-PaymentService -Replicas $Replicas
Write-Host ''
Write-Host "DONE. Back to healthy v1 with $Replicas replica(s) and the 10 requests/s baseline running. Ready for the next run." -ForegroundColor Green
