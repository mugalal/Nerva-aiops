[CmdletBinding()]
param(
    [string]$M1Url = 'http://127.0.0.1:8001',
    [string]$M2Url = 'http://127.0.0.1:8002',
    [string]$M3Url = 'http://127.0.0.1:8003',
    [string]$M4Url = 'http://127.0.0.1:8004',
    [string]$M5Url = 'http://127.0.0.1:8005',
    [string]$M6Url = 'http://127.0.0.1:8006',
    [switch]$AllowMockActuator
)
$ErrorActionPreference = 'Stop'
$taskServices = [ordered]@{ M1 = $M1Url; M2 = $M2Url; M3 = $M3Url; M4 = $M4Url; M5 = $M5Url; M6 = $M6Url }
$taskRows = @()
foreach ($taskModule in $taskServices.Keys) {
    $taskUrl = $taskServices[$taskModule].TrimEnd('/')
    try {
        $taskHealth = Invoke-RestMethod -Uri "$taskUrl/health" -TimeoutSec 20
        $taskAccept = $taskHealth.status -eq 'ok'
        if ($taskModule -eq 'M4' -and $AllowMockActuator -and $taskHealth.remediation_backend -eq 'mock') {
            $taskModes = @($taskHealth.provider_modes.PSObject.Properties | ForEach-Object { $_.Value })
            $taskAccept = $taskHealth.status -eq 'degraded' -and $taskHealth.workflow_storage.durable -eq $true -and
                -not $taskHealth.workflow_storage.error -and -not ($taskModes | Where-Object { $_ -ne 'real' })
        }
        if ($taskModule -in @('M4', 'M5', 'M6')) {
            $null = Invoke-RestMethod -Uri "$taskUrl/ready" -TimeoutSec 20
        }
        if ($taskModule -eq 'M2' -and ($taskHealth.dependencies.m1 -eq 'disabled' -or $taskHealth.dependencies.shared_api -eq 'disabled')) {
            $taskAccept = $false
        }
        $taskRows += [pscustomobject]@{ Module = $taskModule; Status = $taskHealth.status; Ready = [bool]$taskAccept; URL = $taskUrl; Reason = $taskHealth.reason }
    } catch {
        $taskRows += [pscustomobject]@{ Module = $taskModule; Status = 'unavailable'; Ready = $false; URL = $taskUrl; Reason = 'Health or readiness request failed' }
    }
}
$taskRows | Format-Table -AutoSize
if ($taskRows | Where-Object { -not $_.Ready }) {
    throw 'Readiness gate failed. Check each service /health response and collect the required measured baseline; do not substitute mock data.'
}
Write-Host 'All six modules passed the configured readiness checks. Fault recovery still needs its separate scenario validation.'
