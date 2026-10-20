<#
.SYNOPSIS
  Live demo, Scenario C: two services. A surge on checkout-service overloads payment-service, which it calls.

.DESCRIPTION
  Off camera it resets both services to one replica, runs 5 requests/s to payment-service plus 5 checkouts/s
  (each checkout calls payment, so payment still sees its usual 10/s), measures both healthy baselines and
  waits until M2 is quiet. Press Enter to add 60 checkouts/s. Expected:
    * two incidents, one per service, detected independently
    * payment-service: SCALE proposal -> you approve on the website -> M1 measures recovery
    * checkout-service: ESCALATED by the dependency guard ("blocked by payment-service"), not scaled
  Nothing is approved by this script unless -Rehearse is given (rehearsal only: it approves through the
  website gateway with the website's own operator token, exactly as a click would).

.EXAMPLE
  .\scripts\demo-cascade.ps1
.EXAMPLE
  .\scripts\demo-cascade.ps1 -Rehearse -NoForwards
#>
[CmdletBinding()]
param(
    [int]$CheckoutSurgeRps = 60,
    [int]$WarmupMinutes = 4,
    [switch]$Rehearse,
    [switch]$NoForwards
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
Import-Module (Join-Path $PSScriptRoot 'demo/NervaDemo.psm1') -Force

Initialize-NervaDemo -Root $root -Scenario 'two-service-cascade'
if (-not $NoForwards) { Start-BrowserForwards }
Assert-StackReady
Assert-NoActiveWork

Stop-TrafficGenerator 'checkout-spike-load'
Reset-PaymentService -Replicas 1
Reset-CheckoutService -Replicas 1
Write-Step 'Healthy traffic: 5/s straight to payment + 5 checkouts/s (each calls payment: payment sees 10/s)'
Start-TrafficGenerator -Name 'payment-traffic' -Rps 5 -DurationSeconds 86400
Start-TrafficGenerator -Name 'checkout-traffic' -Rps 5 -DurationSeconds 86400 -Target 'http://checkout-service/checkout'
Measure-HealthyBaseline -Replicas 1 -WarmupMinutes $WarmupMinutes
Measure-ServiceBaseline 'checkout-service'
Wait-M2Quiet
$known = @(@(Get-Incidents) | ForEach-Object { $_.incident_id })
if (-not $Rehearse) { Copy-OperatorToken; Wait-ForRecording "start the $CheckoutSurgeRps checkouts/s surge" }

Assert-NoActiveWork
Write-Step "Injecting a surge on checkout-service only: +$CheckoutSurgeRps checkouts/s (each one calls payment-service)"
Start-TrafficGenerator -Name 'checkout-spike-load' -Rps $CheckoutSurgeRps -DurationSeconds 1800 -Target 'http://checkout-service/checkout'
Write-Note 'Expected: two incidents; payment-service gets a SCALE proposal, checkout-service is escalated by the dependency guard.'

$approve = $null
if ($Rehearse) {
    $approve = {
        param($id, $context)
        if ($context.decision.recommended_action -ne 'SCALE') { return }
        $code = Get-Content -Raw (Join-Path $root 'scripts/live-drill.py')
        $out = ($code | K exec -i deployment/incident-memory '--' python - ui-approve $id | Out-String) | ConvertFrom-Json
        Write-Host ("        rehearsal approval through the website gateway: without token HTTP {0}, with token HTTP {1}" -f $out.without_operator_token[0], $out.approved[0])
    }
}
$null = Watch-NewIncidents -KnownIds $known -Expected 2 -OnAwaitingApproval $approve
K get deployment payment-service checkout-service | Out-Host

if (-not $Rehearse) { [void](Read-Host 'Show the result on camera, then press Enter to reset for the next run') }
Stop-TrafficGenerator 'checkout-spike-load'
Stop-TrafficGenerator 'checkout-traffic'
Assert-NoActiveWork
Reset-PaymentService -Replicas 1
Reset-CheckoutService -Replicas 1
Start-TrafficGenerator -Name 'payment-traffic' -Rps 10 -DurationSeconds 86400
Write-Host ''
Write-Host 'DONE. Both services back to 1 replica; the standard 10 requests/s payment traffic is running.' -ForegroundColor Green
