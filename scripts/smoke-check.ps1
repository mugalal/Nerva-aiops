param(
    [switch]$M1Only
)

$ErrorActionPreference = "Continue"
$failed = $false

function Get-EnvironmentValue {
    param([string]$Name, [string]$Default)

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Test-NexusHealth {
    param([string]$Name, [string]$BaseUrl)

    Write-Host -NoNewline ("{0,-24} {1} ... " -f $Name, $BaseUrl)
    try {
        $health = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/health" -TimeoutSec 3
        if ($health.status -eq "unavailable") {
            Write-Host "unavailable"
            $script:failed = $true
        }
        else {
            Write-Host $health.status
        }
    }
    catch {
        Write-Host "failed"
        $script:failed = $true
    }
}

if (-not $M1Only) {
    Test-NexusHealth "shared-nexus-api" (Get-EnvironmentValue "SHARED_NEXUS_API_BASE_URL" "http://localhost:8000")
}
Test-NexusHealth "m1-telemetry" (Get-EnvironmentValue "M1_TELEMETRY_BASE_URL" "http://localhost:8001")
if (-not $M1Only) {
    Test-NexusHealth "m2-anomaly" (Get-EnvironmentValue "M2_ANOMALY_BASE_URL" "http://localhost:8002")
    Test-NexusHealth "m3-rca" (Get-EnvironmentValue "M3_RCA_BASE_URL" "http://localhost:8003")
    Test-NexusHealth "m4-decision" (Get-EnvironmentValue "M4_DECISION_BASE_URL" "http://localhost:8004")
    Test-NexusHealth "m5-memory" (Get-EnvironmentValue "M5_MEMORY_BASE_URL" "http://localhost:8005")
    Test-NexusHealth "m6-finops" (Get-EnvironmentValue "M6_FINOPS_BASE_URL" "http://localhost:8006")
}

$prometheusUrl = Get-EnvironmentValue "PROMETHEUS_BASE_URL" "http://localhost:9090"
Write-Host -NoNewline ("{0,-24} {1} ... " -f "prometheus", $prometheusUrl)
try {
    Invoke-WebRequest -Uri "$($prometheusUrl.TrimEnd('/'))/-/ready" -TimeoutSec 3 | Out-Null
    Write-Host "ok"
}
catch {
    Write-Host "failed"
    $failed = $true
}

if ($M1Only) {
    $lokiUrl = Get-EnvironmentValue "LOKI_BASE_URL" "http://localhost:3100"
    Write-Host -NoNewline ("{0,-24} {1} ... " -f "loki", $lokiUrl)
    try {
        Invoke-WebRequest -Uri "$($lokiUrl.TrimEnd('/'))/ready" -TimeoutSec 3 | Out-Null
        Write-Host "ok"
    }
    catch {
        Write-Host "failed"
        $failed = $true
    }

    $grafanaUrl = Get-EnvironmentValue "GRAFANA_BASE_URL" "http://localhost:3000"
    Write-Host -NoNewline ("{0,-24} {1} ... " -f "grafana", $grafanaUrl)
    try {
        Invoke-RestMethod -Uri "$($grafanaUrl.TrimEnd('/'))/api/health" -TimeoutSec 3 | Out-Null
        Write-Host "ok"
    }
    catch {
        Write-Host "failed"
        $failed = $true
    }
}

if ($failed) {
    exit 1
}
