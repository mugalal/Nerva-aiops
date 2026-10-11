[CmdletBinding()]
param(
    [string]$Operator = $env:M5_OPERATOR_ID,
    [switch]$NoBuild
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRuntimePath = Join-Path $taskRoot '.review-branches/stack-runtime.json'
$taskCompose = Join-Path $taskRoot 'observability/docker-compose.yml'

function New-LocalSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

if (Test-Path -LiteralPath $taskRuntimePath) {
    $taskRuntime = Get-Content -LiteralPath $taskRuntimePath -Raw | ConvertFrom-Json
} else {
    $taskRuntime = [pscustomobject]@{ NEXUS_DB_PASSWORD = (New-LocalSecret); NEXUS_APPROVAL_TOKEN = (New-LocalSecret); M5_OPERATOR_ID = $null }
}
# Secrets added after the first run are generated once and retained with the others.
foreach ($name in 'NEXUS_INGEST_TOKEN', 'M5_UI_OPERATOR_TOKEN') {
    if (-not ($taskRuntime.PSObject.Properties.Name -contains $name)) {
        $taskRuntime | Add-Member -NotePropertyName $name -NotePropertyValue (New-LocalSecret)
    }
    $fromEnvironment = [Environment]::GetEnvironmentVariable($name)
    if ($fromEnvironment) { $taskRuntime.$name = $fromEnvironment }
}
if ($env:NEXUS_DB_PASSWORD) { $taskRuntime.NEXUS_DB_PASSWORD = $env:NEXUS_DB_PASSWORD }
if ($env:NEXUS_APPROVAL_TOKEN) { $taskRuntime.NEXUS_APPROVAL_TOKEN = $env:NEXUS_APPROVAL_TOKEN }
if ($Operator) { $taskRuntime.M5_OPERATOR_ID = $Operator }
if (-not $taskRuntime.M5_OPERATOR_ID) { $taskRuntime.M5_OPERATOR_ID = [Environment]::UserName }
if ($taskRuntime.NEXUS_DB_PASSWORD -notmatch '^[A-Za-z0-9_-]{16,}$') {
    throw 'NEXUS_DB_PASSWORD must contain at least 16 URL-safe letters, digits, underscores or hyphens.'
}
foreach ($name in 'NEXUS_APPROVAL_TOKEN', 'NEXUS_INGEST_TOKEN', 'M5_UI_OPERATOR_TOKEN') {
    if ([string]$taskRuntime.$name -notmatch '^[A-Za-z0-9_-]{32,}$') { throw "$name must contain at least 32 URL-safe characters." }
}
if ($taskRuntime.NEXUS_INGEST_TOKEN -eq $taskRuntime.NEXUS_APPROVAL_TOKEN) {
    throw 'NEXUS_INGEST_TOKEN must differ from NEXUS_APPROVAL_TOKEN.'
}
$env:NEXUS_DB_PASSWORD = $taskRuntime.NEXUS_DB_PASSWORD
$env:NEXUS_APPROVAL_TOKEN = $taskRuntime.NEXUS_APPROVAL_TOKEN
$env:M5_OPERATOR_ID = $taskRuntime.M5_OPERATOR_ID
$env:NEXUS_INGEST_TOKEN = $taskRuntime.NEXUS_INGEST_TOKEN
$env:M5_UI_OPERATOR_TOKEN = $taskRuntime.M5_UI_OPERATOR_TOKEN
if ($env:GEMINI_API_KEY) {
    $taskRuntime | Add-Member -NotePropertyName GEMINI_API_KEY -NotePropertyValue $env:GEMINI_API_KEY -Force
}
if ($taskRuntime.GEMINI_API_KEY) { $env:GEMINI_API_KEY = $taskRuntime.GEMINI_API_KEY }
$env:M5_LLM_MODEL = if ($env:M5_LLM_MODEL) { $env:M5_LLM_MODEL } elseif ($taskRuntime.M5_LLM_MODEL) { $taskRuntime.M5_LLM_MODEL } else { 'gemini-3.5-flash-lite' }

$taskRuntime | Add-Member `
    -NotePropertyName M5_LLM_MODEL `
    -NotePropertyValue $env:M5_LLM_MODEL `
    -Force

New-Item -ItemType Directory -Path (Split-Path -Parent $taskRuntimePath) -Force | Out-Null
$taskRuntime | ConvertTo-Json | Set-Content -LiteralPath $taskRuntimePath -Encoding utf8

$taskArgs = @('compose', '-f', $taskCompose, '--profile', 'integration', 'up', '--detach', '--wait', '--wait-timeout', '300')
if (-not $NoBuild) { $taskArgs += '--build' }
& docker @taskArgs
if ($LASTEXITCODE -ne 0) { throw 'Stack startup failed. Inspect docker compose logs for the failed service.' }
Write-Host 'The integrated stack is serving. UI: http://127.0.0.1:8005/'
Write-Host "Operator token for dashboard decisions is stored in $taskRuntimePath (M5_UI_OPERATOR_TOKEN); it is not printed."
Write-Host 'Compose uses real providers and an explicit mock actuator. Capture a healthy M1 baseline before diagnosis.'
Write-Host 'Run scripts/check-stack.ps1 -AllowMockActuator after baseline capture for the full readiness gate.'
