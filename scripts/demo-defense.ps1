<#
.SYNOPSIS
  One menu for the defense: health check, then any of the three live scenarios.

.DESCRIPTION
  Every scenario is the same script you can run on its own; this only chooses. Approvals always happen on the
  website (the scenario prints when, and copies the operator token to the clipboard). Each scenario resets
  the services afterwards.
    1  Two-service cascade   checkout surge -> payment scaled, checkout blocked by the dependency guard
    2  Payment traffic spike payment-service surge -> SCALE proposal -> measured recovery
    3  Faulty release        payment-service v2 -> ROLLBACK proposal -> measured recovery
    4  Health check          scripts/doctor.ps1 (read-only)
.EXAMPLE
  .\scripts\demo-defense.ps1
.EXAMPLE
  .\scripts\demo-defense.ps1 -Rehearse    # practice: no pauses; approvals go through the website gateway
#>
[CmdletBinding()]
param([switch]$NoForwards, [switch]$Rehearse)   # -Rehearse: practice runs with no pauses
$ErrorActionPreference = 'Stop'
$scripts = $PSScriptRoot
$forward = @(); if ($NoForwards) { $forward += '-NoForwards' }; if ($Rehearse) { $forward += '-Rehearse' }

while ($true) {
    Write-Host ''
    Write-Host 'NERVA defense scenarios' -ForegroundColor Cyan
    Write-Host '  1  Two-service cascade (checkout -> payment, dependency guard)'
    Write-Host '  2  Payment traffic spike (scale out, measured recovery)'
    Write-Host '  3  Faulty release (rollback, measured recovery)'
    Write-Host '  4  Health check (read-only)'
    Write-Host '  Q  Quit'
    switch ((Read-Host 'Choose').Trim().ToUpperInvariant()) {
        '1' { & (Join-Path $scripts 'demo-cascade.ps1') @forward }
        '2' { & (Join-Path $scripts 'demo-traffic-spike.ps1') @forward }
        '3' { & (Join-Path $scripts 'demo-faulty-deployment.ps1') @forward }
        '4' { & (Join-Path $scripts 'doctor.ps1') }
        'Q' { return }
        default { Write-Host 'Choose 1, 2, 3, 4 or Q.' -ForegroundColor Yellow }
    }
}
