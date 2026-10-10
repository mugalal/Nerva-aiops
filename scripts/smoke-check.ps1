param(
    [switch]$M1Only,
    [switch]$SkipGrafana,
    [switch]$RequireKubernetes
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
        $health = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/health" -TimeoutSec 15
        if ($health.status -notin @("ok", "degraded")) {
            throw "Unexpected readiness status: $($health.status)"
        }
        if ($Name -eq "m1-telemetry") {
            if ($health.provider_mode -ne "real") {
                throw "M1 must use real providers for this smoke check"
            }
            foreach ($dependency in @("telemetry_provider", "telemetry_data", "loki", "payment_service")) {
                if ($health.dependencies.$dependency.status -ne "ok") {
                    throw "$dependency is not ready: $($health.dependencies.$dependency.detail)"
                }
            }
            if ($RequireKubernetes -and $health.dependencies.kubernetes.status -ne "ok") {
                throw "Kubernetes evidence is not ready: $($health.dependencies.kubernetes.detail)"
            }
        }
        Write-Host $health.status
    }
    catch {
        Write-Host "failed: $($_.Exception.Message)"
        $script:failed = $true
    }
}

function Test-NexusTelemetry {
    param([string]$BaseUrl)

    Write-Host -NoNewline ("{0,-24} {1} ... " -f "payment snapshot", $BaseUrl)
    try {
        $snapshot = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/internal/telemetry/snapshot?service=payment-service" -TimeoutSec 15
        if ($snapshot.service -ne "payment-service" -or [string]::IsNullOrWhiteSpace($snapshot.version)) {
            throw "Snapshot has no valid payment-service identity"
        }
        $metricNames = @("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu", "memory", "replica_count")
        if (@($snapshot.metrics.PSObject.Properties).Count -ne $metricNames.Count) {
            throw "Snapshot metrics do not match the frozen contract"
        }
        foreach ($metric in $metricNames) {
            if ($snapshot.metrics.PSObject.Properties.Name -notcontains $metric) {
                throw "Snapshot is missing $metric"
            }
            $value = [double]$snapshot.metrics.$metric
            if ($null -eq $snapshot.metrics.$metric -or [double]::IsNaN($value) -or [double]::IsInfinity($value) -or $value -lt 0) {
                throw "Snapshot has an invalid $metric"
            }
        }
        if ($snapshot.metrics.http_5xx_rate -gt 1 -or $snapshot.metrics.replica_count -lt 1 -or $snapshot.metrics.replica_count -ne [Math]::Floor([double]$snapshot.metrics.replica_count)) {
            throw "Snapshot has an invalid error ratio or no running replicas"
        }
        if ($snapshot.metrics.request_rate -le 0 -or $snapshot.metrics.latency_p95_ms -le 0) {
            throw "No recent /pay traffic. Run generate-traffic.ps1 for at least 90 seconds and retry while traffic is running"
        }
        $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]$snapshot.timestamp).TotalSeconds
        $maxAge = [double](Get-EnvironmentValue "M1_STALE_AFTER_SECONDS" "60")
        if ($age -gt $maxAge -or $age -lt -5) {
            throw "Snapshot timestamp is stale or in the future (age=$age seconds)"
        }
        Write-Host ("ok ({0:N2} req/s, {1:N2} ms P95, version {2})" -f $snapshot.metrics.request_rate, $snapshot.metrics.latency_p95_ms, $snapshot.version)
    }
    catch {
        Write-Host "failed: $($_.Exception.Message)"
        $script:failed = $true
    }
}

function Test-NexusPaymentLogs {
    param([string]$BaseUrl)

    Write-Host -NoNewline ("{0,-24} {1} ... " -f "payment logs", $BaseUrl)
    try {
        $endNs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() * [int64]1000000
        $startNs = $endNs - [int64]120000000000
        $query = [Uri]::EscapeDataString('{service_name="payment-service"} | json | route="/pay" | __error__=""')
        $response = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/loki/api/v1/query_range?query=$query&start=$startNs&end=$endNs&limit=20&direction=backward" -TimeoutSec 15
        if ($response.status -ne "success" -or $response.data.resultType -ne "streams") {
            throw "Loki did not return a successful log query"
        }
        $count = 0
        foreach ($stream in $response.data.result) {
            if ($stream.stream.service_name -ne "payment-service") {
                throw "Loki returned logs for another service"
            }
            $count += @($stream.values).Count
        }
        if ($count -eq 0) {
            throw "No /pay logs in the last 2 minutes. Generate payment traffic and check Alloy collection"
        }
        Write-Host "ok ($count recent payment request logs)"
    }
    catch {
        Write-Host "failed: $($_.Exception.Message)"
        $script:failed = $true
    }
}

if (-not $M1Only) {
    Test-NexusHealth "shared-nexus-api" (Get-EnvironmentValue "SHARED_NEXUS_API_BASE_URL" "http://localhost:8000")
}
$m1Url = Get-EnvironmentValue "M1_TELEMETRY_BASE_URL" "http://localhost:8001"
Test-NexusHealth "m1-telemetry" $m1Url
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

$lokiUrl = Get-EnvironmentValue "LOKI_BASE_URL" "http://localhost:3100"
if ($M1Only) {
    Write-Host -NoNewline ("{0,-24} {1} ... " -f "loki", $lokiUrl)
    try {
        Invoke-WebRequest -Uri "$($lokiUrl.TrimEnd('/'))/ready" -TimeoutSec 3 | Out-Null
        Write-Host "ok"
    }
    catch {
        Write-Host "failed"
        $failed = $true
    }

    if (-not $SkipGrafana) {
        $grafanaUrl = Get-EnvironmentValue "GRAFANA_BASE_URL" "http://localhost:3000"
        Write-Host -NoNewline ("{0,-24} {1} ... " -f "grafana", $grafanaUrl)
        try {
            $grafanaHealth = Invoke-RestMethod -Uri "$($grafanaUrl.TrimEnd('/'))/api/health" -TimeoutSec 3
            if ($grafanaHealth.database -ne "ok") {
                throw "Grafana database is not ready"
            }
            Write-Host "ok"
        }
        catch {
            Write-Host "failed: $($_.Exception.Message)"
            $failed = $true
        }
    }
}

Test-NexusTelemetry $m1Url
Test-NexusPaymentLogs $lokiUrl

if ($failed) {
    exit 1
}
exit 0
