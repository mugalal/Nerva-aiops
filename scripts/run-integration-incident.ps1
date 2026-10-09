param(
    [Parameter(Mandatory = $true)][string]$AnomalyPath,
    [string]$IncidentId = ('INC-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)),
    [string]$M4Url = 'http://127.0.0.1:18004',
    [string]$OutputDirectory = '.review-branches/integration-results',
    [switch]$Approve,
    [ValidateRange(15, 3600)][int]$RecoveryTimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$M4Url = $M4Url.TrimEnd('/')
$incidentRoute = [Uri]::EscapeDataString($IncidentId)
$anomaly = Get-Content -LiteralPath $AnomalyPath -Raw | ConvertFrom-Json
if (-not $anomaly.anomaly_id -or -not $anomaly.service -or -not $anomaly.timestamp) {
    throw 'AnomalyPath must contain an actual AnomalyEvent with ID, service and timestamp.'
}
if ($anomaly.timestamp -is [DateTime] -or $anomaly.timestamp -is [DateTimeOffset]) {
    $eventTime = [DateTimeOffset]$anomaly.timestamp
} else {
    $eventTime = [DateTimeOffset]::Parse([string]$anomaly.timestamp)
}
if ($eventTime -gt [DateTimeOffset]::UtcNow -or $eventTime -lt [DateTimeOffset]::UtcNow.AddMinutes(-5)) {
    throw 'Use a fresh observed anomaly from the last five minutes.'
}
$output = Join-Path $OutputDirectory $IncidentId
New-Item -ItemType Directory -Path $output -Force | Out-Null

function Save-Result([string]$Name, $Value) {
    $Value | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $output $Name) -Encoding utf8
}

function Send-Json([string]$Path, $Body) {
    $parameters = @{ Method = 'Post'; Uri = "$M4Url$Path"; ContentType = 'application/json'; TimeoutSec = 180 }
    if ($null -ne $Body) { $parameters.Body = $Body | ConvertTo-Json -Depth 30 }
    Invoke-RestMethod @parameters
}

$incident = Send-Json '/api/incidents/' @{
    incident_id = $IncidentId
    started_at = $eventTime.ToUniversalTime().ToString('o')
    severity = $anomaly.severity
    affected_services = @($anomaly.service)
    anomaly_ids = @()
}
Save-Result 'incident.json' $incident
$null = Send-Json "/internal/anomalies?incident_id=$incidentRoute" $anomaly
Save-Result 'anomaly.json' $anomaly
$decision = Send-Json "/internal/decisions/build/$incidentRoute" $null
Save-Result 'decision.json' $decision
$proposal = Invoke-RestMethod "$M4Url/api/incidents/$incidentRoute/proposal"
Save-Result 'proposal.json' $proposal
$proposal | Format-List
if ($proposal.recommended_action -eq 'ESCALATE') { throw 'Incident escalated; inspect the saved decision and service evidence.' }
if (-not $Approve) {
    Write-Output "Proposal saved in $output. Approve it through POST /api/incidents/$incidentRoute/approve when reviewed."
    return
}

$action = Send-Json "/api/incidents/$incidentRoute/approve" $null
Save-Result 'action.json' $action
if ($action.status -ne 'SUCCESS') { throw 'Remediation failed; inspect the action result.' }
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($RecoveryTimeoutSeconds)
do {
    $retrySeconds = 15
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri "$M4Url/internal/recovery/validate/$incidentRoute" -TimeoutSec 60
    } catch {
        $failure = $_
        $detail = $null
        try { $detail = ($failure.ErrorDetails.Message | ConvertFrom-Json).detail } catch { }
        if ($failure.Exception.Response -and [int]$failure.Exception.Response.StatusCode -eq 503 -and $detail.retryable -eq $true) {
            $retryHeader = [string]$failure.Exception.Response.Headers['Retry-After']
            $parsedRetry = 0
            if ([int]::TryParse($retryHeader, [ref]$parsedRetry) -and $parsedRetry -gt 0) { $retrySeconds = [Math]::Min(60, $parsedRetry) }
            Write-Output "M1 recovery data is temporarily unavailable: $($detail.message)"
            Start-Sleep -Seconds $retrySeconds
            continue
        }
        throw $failure
    }
    if ([int]$response.StatusCode -eq 202) {
        $retryHeader = [string]$response.Headers['Retry-After']
        $parsedRetry = 0
        if ([int]::TryParse($retryHeader, [ref]$parsedRetry) -and $parsedRetry -gt 0) { $retrySeconds = [Math]::Min(60, $parsedRetry) }
        Write-Output 'M1 is still collecting the post-action recovery window.'
        Start-Sleep -Seconds $retrySeconds
        continue
    }
    $recovery = $response.Content | ConvertFrom-Json
    Save-Result 'recovery-incident.json' $recovery
    if ($recovery.status -ne 'RESOLVED') { throw "Measured recovery did not resolve the incident: $($recovery.status)" }
    Write-Output "Incident $IncidentId RESOLVED after M1 measured recovery. Results: $output"
    return
} while ([DateTimeOffset]::UtcNow -lt $deadline)
throw 'Recovery is still pending. Poll the same recovery endpoint; no additional remediation is required.'
