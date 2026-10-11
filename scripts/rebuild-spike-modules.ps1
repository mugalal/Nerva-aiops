[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskKubectl = Join-Path $taskRoot '.review-branches/tools/kubectl.exe'
$taskKind = Join-Path $taskRoot '.review-branches/tools/kind.exe'
$taskKubeconfig = Join-Path $taskRoot '.review-branches/spike-kubeconfig.yaml'
$taskTag = 'spike-refresh-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss')

function Invoke-RebuildKube {
    $taskArguments = @('--kubeconfig', $taskKubeconfig, '--context', 'kind-nexus', '-n', 'nexus-demo')
    if ($MyInvocation.ExpectingInput) { $input | & $taskKubectl @taskArguments @args }
    else { & $taskKubectl @taskArguments @args }
    if ($LASTEXITCODE -ne 0) { throw 'kubectl failed; rebuild stopped.' }
}

function Assert-NoActiveRecovery {
    $taskProbe = @'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8004/api/incidents/', timeout=20) as response:
    incidents = json.load(response)
if any(item['status'] in ('EXECUTING', 'VALIDATING') for item in incidents):
    raise SystemExit('An incident is executing or validating. Wait before rebuilding.')
'@
    # Quote the separator: PowerShell functions consume an unquoted --.
    $taskProbe | Invoke-RebuildKube exec -i deployment/shared-nexus-api '--' python -
}

function Set-RebuildManifestImage {
    param($Manifest, [string]$Module, [string]$Image)
    $taskItems = if ($Manifest.kind -eq 'List') { @($Manifest.items) } else { @($Manifest) }
    $taskDeployments = @($taskItems | Where-Object { $_.kind -eq 'Deployment' -and $_.metadata.name -eq $Module })
    if ($taskDeployments.Count -ne 1) { throw "Expected one deployment in the $Module manifest." }
    $taskDeployment = $taskDeployments[0]
    foreach ($taskContainer in $taskDeployment.spec.template.spec.containers) {
        if ($taskContainer.name -eq $Module) { $taskContainer.image = $Image }
    }
    foreach ($taskContainer in @($taskDeployment.spec.template.spec.initContainers)) {
        if ($taskContainer -and $taskContainer.image -like "nexus/${Module}:*") { $taskContainer.image = $Image }
    }
    return $Manifest
}

Push-Location $taskRoot
try {
    foreach ($taskTool in @($taskKubectl, $taskKind)) {
        if (-not (Test-Path -LiteralPath $taskTool)) { throw "Required tool is missing: $taskTool" }
    }
    docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0) { throw 'Open Docker Desktop and wait for the Linux engine, then retry.' }
    docker start nexus-control-plane nexus-worker nexus-worker2
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the existing nexus cluster nodes.' }
    $taskConfigText = & $taskKind get kubeconfig --name nexus
    if ($LASTEXITCODE -ne 0) { throw 'Could not obtain the existing nexus cluster kubeconfig.' }
    $taskConfigText | Set-Content -LiteralPath $taskKubeconfig -Encoding utf8
    Invoke-RebuildKube wait --for=condition=Ready nodes --all --timeout=180s
    Invoke-RebuildKube rollout status deployment/shared-nexus-api --timeout=180s
    Assert-NoActiveRecovery

    # Verify field names only; secret values are never displayed or changed.
    $taskSecretText = Invoke-RebuildKube get secret nexus-runtime -o json
    $taskSecret = ($taskSecretText -join "`n") | ConvertFrom-Json
    foreach ($taskName in @('DATABASE_URL', 'NEXUS_APPROVAL_TOKEN', 'NEXUS_INGEST_TOKEN')) {
        if (-not $taskSecret.data.$taskName) { throw "Existing nexus-runtime secret is missing $taskName; rebuild stopped." }
    }
    $taskSecret = $null
    $taskSecretText = $null

    $taskModules = [ordered]@{
        'telemetry-intelligence' = 'services/telemetry-intelligence/k8s/telemetry-intelligence.yaml'
        'finops-engine' = 'services/finops-engine/k8s/deployment.yaml'
        'shared-nexus-api' = 'services/shared-nexus-api/k8s/shared-nexus-api.yaml'
    }
    $taskManifests = [ordered]@{}
    foreach ($taskModule in $taskModules.Keys) {
        $taskImage = "nexus/${taskModule}:$taskTag"
        Write-Host "Building and importing $taskImage"
        docker build --provenance=false --platform linux/amd64 -f "services/$taskModule/Dockerfile" -t $taskImage .
        if ($LASTEXITCODE -ne 0) { throw "Image build failed: $taskModule" }
        & $taskKind load docker-image $taskImage --name nexus
        if ($LASTEXITCODE -ne 0) { throw "Image import failed: $taskModule" }

        # Convert repository manifests locally and set fresh tags before applying.
        $taskManifestText = Invoke-RebuildKube create --dry-run=client --validate=false -f $taskModules[$taskModule] -o json
        $taskRaw = ($taskManifestText -join "`n").Trim()
        $taskArrayJson = if ($taskRaw.StartsWith('[')) { $taskRaw } else { '[' + ($taskRaw -replace '(?m)^\}\r?\n\{', '},{') + ']' }
        $taskManifest = $taskArrayJson | ConvertFrom-Json
        $taskManifest = Set-RebuildManifestImage -Manifest $taskManifest -Module $taskModule -Image $taskImage
        $taskList = [ordered]@{ apiVersion = 'v1'; kind = 'List'; items = @($taskManifest) }
        $taskManifests[$taskModule] = $taskList | ConvertTo-Json -Depth 80
    }

    Assert-NoActiveRecovery
    foreach ($taskModule in $taskModules.Keys) {
        Write-Host "Applying current configuration and fresh image for $taskModule"
        $taskManifests[$taskModule] | Invoke-RebuildKube apply -f -
        Invoke-RebuildKube rollout status "deployment/$taskModule" --timeout=300s
    }

    $taskCheckM6 = @'
import json, sys
sys.path.insert(0, '/app')
from app.models import ScaleOptionsRequest
from app.scale_options import build_scale_options
request = ScaleOptionsRequest(service='payment-service', current_replicas=1,
    cpu_request_m=100, memory_request_mb=128, observed_cpu_pct=100, scale_duration_minutes=30)
result = build_scale_options(request, observed_cpu_pct=500)
options = [(option.replicas, option.risk) for option in result.temporary_scale_options]
assert (8, 'MEDIUM') in options and all(count <= 8 for count, _ in options), options
print(json.dumps({'finops_registry_check': 'passed', 'simulated_options': options}))
'@
    $taskCheckM6 | Invoke-RebuildKube exec -i deployment/finops-engine '--' python -
    Invoke-RebuildKube get deployments telemetry-intelligence finops-engine shared-nexus-api -o wide
    Write-Host 'Rebuild complete. Current manifests and freshly built images are deployed.'
    Write-Host 'Restart the API port-forward if it exited during the M4 rollout.'
    Write-Host 'Continue from step 1 of docs/TEST_TRAFFIC_SPIKE_SELF_SERVICE_WINDOWS.md; skip step 0 because this rebuild is complete.'
} finally {
    Pop-Location
}
