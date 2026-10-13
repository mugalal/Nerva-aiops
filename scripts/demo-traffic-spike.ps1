<#
.SYNOPSIS
  Live demo, Scenario B: traffic spike -> NERVA proposes SCALE -> you approve on the website -> measured recovery.

.DESCRIPTION
  Off camera it resets payment-service to one healthy v1 replica, runs 10 requests/s, measures the
  healthy baseline and waits until M2 is quiet. It then pauses so you can start recording. Press Enter
  to add a 150 requests/s spike. It follows the incident, prompts you to approve on the website (the
  operator token is on your clipboard), waits for M1's measured result, saves the evidence and asks
  whether to stop the spike. Nothing is approved or scaled by this script; NERVA and you do that.

.EXAMPLE
  .\scripts\demo-traffic-spike.ps1
.EXAMPLE
  .\scripts\demo-traffic-spike.ps1 -SpikeRps 150 -WarmupMinutes 4 -NoForwards
#>
[CmdletBinding()]
param(
    [int]$SpikeRps = 150,
    [int]$WarmupMinutes = 4,
    [switch]$NoForwards
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
Import-Module (Join-Path $PSScriptRoot 'demo/NervaDemo.psm1') -Force

Initialize-NervaDemo -Root $root -Scenario 'traffic-spike'
if (-not $NoForwards) { Start-BrowserForwards }
Assert-StackReady
Assert-NoActiveWork

# A spike must start from one replica: from 6-8 replicas M6 can only offer a HIGH-risk step and NERVA escalates.
Stop-TrafficGenerator 'payment-spike-load'
Reset-PaymentService -Replicas 1
Write-Step 'Starting healthy baseline traffic'
Start-TrafficGenerator -Name 'payment-traffic' -Rps 10 -DurationSeconds 86400
Measure-HealthyBaseline -Replicas 1 -WarmupMinutes $WarmupMinutes
Wait-M2Quiet
$known = @(@(Get-Incidents) | ForEach-Object { $_.incident_id })
Copy-OperatorToken

Wait-ForRecording "start the $SpikeRps requests/s traffic spike"
Assert-NoActiveWork
Write-Step "Injecting the traffic spike: +$SpikeRps requests/s on one replica"
Start-TrafficGenerator -Name 'payment-spike-load' -Rps $SpikeRps -DurationSeconds 1800
Write-Note 'Expected: detection within about a minute, then a SCALE proposal after the 60 s settle.'

$result = Watch-NewIncident -KnownIds $known -ExpectedCause 'traffic_spike' -ExpectedAction 'SCALE'
K get deployment payment-service | Out-Host

$answer = Read-Host 'Stop the spike load now? (Y/n)'
if ($answer -notmatch '^[nN]') { Stop-TrafficGenerator 'payment-spike-load' }
else { Write-Note 'Spike still running. It stops by itself after 30 minutes, or the next demo run stops it first.' }
Write-Note 'The 10 requests/s baseline generator keeps running for the next demo.'
