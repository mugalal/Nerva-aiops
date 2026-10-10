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
if ($env:NEXUS_DB_PASSWORD) { $taskRuntime.NEXUS_DB_PASSWORD = $env:NEXUS_DB_PASSWORD }
if ($env:NEXUS_APPROVAL_TOKEN) { $taskRuntime.NEXUS_APPROVAL_TOKEN = $env:NEXUS_APPROVAL_TOKEN }
if ($Operator) { $taskRuntime.M5_OPERATOR_ID = $Operator }
if (-not $taskRuntime.M5_OPERATOR_ID) { $taskRuntime.M5_OPERATOR_ID = [Environment]::UserName }
if ($taskRuntime.NEXUS_DB_PASSWORD -notmatch '^[A-Za-z0-9_-]{16,}$') {
    throw 'NEXUS_DB_PASSWORD must contain at least 16 URL-safe letters, digits, underscores or hyphens.'
}
if ($taskRuntime.NEXUS_APPROVAL_TOKEN.Length -lt 32) { throw 'NEXUS_APPROVAL_TOKEN must contain at least 32 characters.' }
New-Item -ItemType Directory -Path (Split-Path -Parent $taskRuntimePath) -Force | Out-Null
$taskRuntime | ConvertTo-Json | Set-Content -LiteralPath $taskRuntimePath -Encoding utf8
$env:NEXUS_DB_PASSWORD = $taskRuntime.NEXUS_DB_PASSWORD
$env:NEXUS_APPROVAL_TOKEN = $taskRuntime.NEXUS_APPROVAL_TOKEN
$env:M5_OPERATOR_ID = $taskRuntime.M5_OPERATOR_ID

$taskArgs = @('compose', '-f', $taskCompose, '--profile', 'integration', 'up', '--detach', '--wait', '--wait-timeout', '300')
if (-not $NoBuild) { $taskArgs += '--build' }
& docker @taskArgs
if ($LASTEXITCODE -ne 0) { throw 'Stack startup failed. Inspect docker compose logs for the failed service.' }
Write-Host 'The integrated stack is serving. UI: http://127.0.0.1:8005/'
Write-Host 'Compose uses real providers and an explicit mock actuator. Capture a healthy M1 baseline before diagnosis.'
Write-Host 'Run scripts/check-stack.ps1 -AllowMockActuator after baseline capture for the full readiness gate.'
