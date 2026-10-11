# Complete Windows guide: Nerva AIOps

Use PowerShell from E:\AIOPS\final project. This guide was checked against the current startup scripts, Dockerfiles and manifests; it was not verified by a fresh live deployment in this session. Docker engine access was denied in the tool session, and kind was unavailable on its PATH.

Choose ONE runtime:

| Runtime | Included | Execution |
| --- | --- | --- |
| A: Compose | Six modules, UI, payment demo, PostgreSQL, Prometheus, Loki, Alloy, Grafana | Real module/telemetry APIs; mock infrastructure actuator |
| B: kind Kubernetes | Six modules, UI, payment demo, PostgreSQL, Prometheus, Loki, Alloy | Real approved rollback/scaling in isolated nexus-demo |

There is no committed Kubernetes Grafana manifest. The Kubernetes dashboard and Prometheus work through port-forwards. Stop Compose before using Kubernetes on the same host ports.

## 1. Prerequisites

Install/open Docker Desktop with Linux containers. Python 3.11+ and Node are only needed for host-side tests; Docker builds install module dependencies. Kubernetes additionally needs kubectl and kind in PATH. First builds need network access to container registries/package repositories.

Allocate at least the README's 6 GB to Docker Desktop. The three-node kind cluster and scale drill need enough extra available capacity for their Pods; diagnose Pending Pods rather than treating startup as successful.

~~~powershell
Set-Location "E:\AIOPS\final project"
docker version
docker compose version
~~~

docker version must show a responding Server, not only a Client. If PowerShell blocks scripts, allow them for this terminal only:

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
~~~

For Kubernetes:

~~~powershell
kubectl version --client
kind version
~~~

Open a new terminal after installation if PATH has not refreshed. The repository's old check-env.ps1 uses kubectl --short; use the checks above instead.

## 2. Runtime A: Compose

### A1. Start all modules in terminal A

~~~powershell
.\scripts\start-stack.ps1 -Operator mugalal
~~~

The script builds the images, creates/reuses local credentials, starts the integration profile and waits for serving checks. Docker DNS and the Compose configuration connect the modules automatically. M5 serves the UI; there is no separate frontend dev server to start.

The file .review-branches/stack-runtime.json retains runtime credentials. Database/evidence/monitoring data use named volumes. Do not generate a different database password on a rerun.

### A2. Real traffic in a second terminal

~~~powershell
Set-Location "E:\AIOPS\final project"
.\scripts\generate-traffic.ps1 -DurationSeconds 3600 -Concurrency 5
~~~

Leave it running during checks/demos. It prints the request summary at completion; little output during the run is expected. Run it again after the hour if more measurements are needed.

### A3. Baseline and readiness in terminal A

After at least three minutes of healthy traffic:

~~~powershell
$taskBaselineEnd = [DateTimeOffset]::UtcNow.AddSeconds(-15)
$taskBaselineBody = @{
    service = "payment-service"
    start = $taskBaselineEnd.AddSeconds(-75).ToString("o")
    end = $taskBaselineEnd.ToString("o")
    step_seconds = 15
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8001/internal/baselines/measure" -ContentType "application/json" -Body $taskBaselineBody
.\scripts\check-stack.ps1 -AllowMockActuator
~~~

Require baseline samples/thresholds and Ready=True for all six modules. Missing/stale data means healthy traffic/collection is not ready yet; keep traffic running and retry. Container health is not a replacement for this gate.

### A4. Application addresses and credentials

| Component | Address |
| --- | --- |
| Payment | http://127.0.0.1:8000/health |
| M1 telemetry | http://127.0.0.1:8001/docs |
| M2 anomalies | http://127.0.0.1:8002/docs |
| M3 RCA | http://127.0.0.1:8003/docs |
| M4 decisions | http://127.0.0.1:8004/docs |
| M5 UI, incident memory, Copilot | http://127.0.0.1:8005/ |
| M6 FinOps | http://127.0.0.1:8006/docs |
| Grafana | http://127.0.0.1:3000/ |
| Prometheus | http://127.0.0.1:9090/ |
| Alloy | http://127.0.0.1:12345/ |

Grafana defaults to admin/admin for a new volume unless GRAFANA_ADMIN_PASSWORD was set before startup. Existing volumes retain their configured login.

~~~powershell
notepad ".\.review-branches\stack-runtime.json"
~~~

Use M5_UI_OPERATOR_TOKEN when the dashboard requests an operator decision token. It differs from the backend approval/ingest tokens.

### A5. Check connected data

~~~powershell
Invoke-RestMethod "http://127.0.0.1:8001/internal/telemetry/snapshot?service=payment-service" | ConvertTo-Json -Depth 12
Invoke-RestMethod "http://127.0.0.1:8002/health" | ConvertTo-Json -Depth 12
Invoke-RestMethod "http://127.0.0.1:8004/api/incidents" | ConvertTo-Json -Depth 12
~~~

A healthy app can have no incidents. M2 polls M1 and hands off correlated alerts to M4. M4 consumes M3 diagnosis, M6 options and M1 evidence/recovery; M5 reads M4 and archives terminal outcomes. FinOps uses Kubernetes resource evidence or the explicit Compose sizing assumptions.

### A6. Optional generated Copilot

Recorded-answer mode requires no external key. Generated chat needs a matching provider key, available model and quota. For unchanged Compose, set the Google provider before startup or recreate after setting it:

~~~powershell
$env:GEMINI_API_KEY = Read-Host "Gemini API key"
$env:M5_LLM_MODEL = Read-Host "Model ID available to your Gemini account"
.\scripts\start-stack.ps1 -Operator mugalal -NoBuild
~~~

Choose a model ID available to your account. The startup script saves the Gemini key and model in the ignored .review-branches/stack-runtime.json file and restores them in a new shell. Explicit environment values replace the saved settings.

The current Compose manifest forwards GEMINI_API_KEY, M5_LLM_API_KEY and M5_LLM_MODEL, but NOT M5_LLM_BASE_URL. Using another backend requires a Compose override carrying its base URL, despite the broader M5 documentation examples.

### A7. Optional fault

With traffic still running, use terminal A:

~~~powershell
docker compose -f observability/docker-compose.yml -f observability/docker-compose.faulty.yml --profile integration up -d --build payment-service
~~~

Watch the UI. Compose lacks Kubernetes deployment evidence, so diagnosis may return unknown and safely escalate. This is not proof of a real rollback or measured infrastructure recovery.

Restore healthy payment:

~~~powershell
docker compose -f observability/docker-compose.yml --profile integration up -d --force-recreate payment-service
~~~

### A8. Inspection, restart and stopping

Use terminal A, where startup exported required Compose environment variables:

~~~powershell
docker compose -f observability/docker-compose.yml --profile integration ps
docker compose -f observability/docker-compose.yml --profile integration logs --tail 100
~~~

For a FRESH terminal used only for logs/status/down, first load saved settings without printing credentials:

~~~powershell
$taskRuntime = Get-Content ".\.review-branches\stack-runtime.json" -Raw | ConvertFrom-Json
foreach ($taskName in @("NEXUS_DB_PASSWORD", "NEXUS_APPROVAL_TOKEN", "NEXUS_INGEST_TOKEN", "M5_UI_OPERATOR_TOKEN", "M5_OPERATOR_ID")) {
    [Environment]::SetEnvironmentVariable($taskName, [string]$taskRuntime.$taskName, "Process")
}
~~~

Otherwise Compose interpolation can fail even for logs/down.

Follow one module's logs and press Ctrl+C when finished:

~~~powershell
docker compose -f observability/docker-compose.yml --profile integration logs -f shared-nexus-api
~~~

Module service names: telemetry-intelligence, anomaly-engine, root-cause-analysis, shared-nexus-api, incident-memory, finops-engine.

Start again without rebuilding:

~~~powershell
.\scripts\start-stack.ps1 -Operator mugalal -NoBuild
~~~

After source changes, omit -NoBuild. To stop, Ctrl+C the traffic terminal, then:

~~~powershell
docker compose -f observability/docker-compose.yml --profile integration down
~~~

This retains volumes. Do not add -v when you want to retain incident/baseline data.

## 3. Runtime B: real Kubernetes actions

Use an isolated local kind cluster. Stop Compose with A8 and close other listeners using the local ports first. Keep one main PowerShell terminal for all setup/scenario commands.

### B1. Pinned context and cluster

Define a wrapper that pins cluster/namespace, forwards piped manifest input, and stops on errors:

~~~powershell
Set-Location "E:\AIOPS\final project"
$ErrorActionPreference = "Stop"
function Invoke-NervaKube {
    if ($MyInvocation.ExpectingInput) {
        $input | & kubectl --context kind-nexus --namespace nexus-demo @args
    } else {
        & kubectl --context kind-nexus --namespace nexus-demo @args
    }
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed; inspect the error before continuing." }
}
$taskClusters = @(kind get clusters)
if ($LASTEXITCODE -ne 0) { throw "kind could not list clusters." }
if ($taskClusters -notcontains "nexus") {
    kind create cluster --config infrastructure/kind-cluster.yaml
    if ($LASTEXITCODE -ne 0) { throw "Cluster creation failed." }
}
kubectl --context kind-nexus get nodes
if ($LASTEXITCODE -ne 0) { throw "Cannot access kind-nexus." }
Invoke-NervaKube apply -f observability/k8s/namespace.yaml
~~~

Expect three Ready nodes. Reusing the cluster preserves its secrets/data.

### B2. Build/import all images

~~~powershell
$taskImages = [ordered]@{
    "postgres" = "observability/postgres/Dockerfile"
    "telemetry-intelligence" = "services/telemetry-intelligence/Dockerfile"
    "anomaly-engine" = "services/anomaly-engine/Dockerfile"
    "root-cause-analysis" = "services/root-cause-analysis/Dockerfile"
    "shared-nexus-api" = "services/shared-nexus-api/Dockerfile"
    "incident-memory" = "services/incident-memory/Dockerfile"
    "finops-engine" = "services/finops-engine/Dockerfile"
}
foreach ($taskName in $taskImages.Keys) {
    docker build --provenance=false --platform linux/amd64 -f $taskImages[$taskName] -t "nexus/$($taskName):dev" .
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $taskName" }
    kind load docker-image "nexus/$($taskName):dev" --name nexus
    if ($LASTEXITCODE -ne 0) { throw "Image import failed: $taskName" }
}
foreach ($taskVersion in @("v1", "v2")) {
    docker build --provenance=false --platform linux/amd64 --build-arg "VERSION=$taskVersion" -t "nexus/payment-service:$taskVersion" apps/demo-microservices/payment-service
    if ($LASTEXITCODE -ne 0) { throw "Payment build failed." }
    kind load docker-image "nexus/payment-service:$taskVersion" --name nexus
    if ($LASTEXITCODE -ne 0) { throw "Payment image import failed." }
}
~~~

Use the repository's distinct, single-platform PostgreSQL image to avoid the documented kind import problem. Do not delete its PVC to fix an image issue.

### B3. Credentials, once

Reuse an existing runtime secret, adding only missing ingest/dashboard tokens while preserving its database and approval credentials:

~~~powershell
function New-NervaSecret {
    $taskBytes = New-Object byte[] 32
    $taskRng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskRng.GetBytes($taskBytes) } finally { $taskRng.Dispose() }
    ([BitConverter]::ToString($taskBytes)).Replace("-", "").ToLowerInvariant()
}
$taskExistingSecret = Invoke-NervaKube get secret nexus-runtime -o name --ignore-not-found
if (-not $taskExistingSecret) {
    $taskDbPassword = New-NervaSecret
    $taskSecret = @{
        apiVersion = "v1"
        kind = "Secret"
        metadata = @{ name = "nexus-runtime"; namespace = "nexus-demo" }
        type = "Opaque"
        stringData = @{
            POSTGRES_USER = "nexus"
            POSTGRES_DB = "nexus"
            POSTGRES_PASSWORD = $taskDbPassword
            DATABASE_URL = "postgresql://nexus:$taskDbPassword@postgres:5432/nexus"
            NEXUS_APPROVAL_TOKEN = (New-NervaSecret)
            NEXUS_INGEST_TOKEN = (New-NervaSecret)
            M5_UI_OPERATOR_TOKEN = (New-NervaSecret)
            M5_OPERATOR_ID = "mugalal"
        }
    }
    $taskSecret | ConvertTo-Json -Depth 8 | Invoke-NervaKube create -f -
} else {
    # Older clusters may predate the separate ingest and dashboard tokens.
    $taskSecret = Invoke-NervaKube get secret nexus-runtime -o json | ConvertFrom-Json
    $taskSecretChanged = $false
    foreach ($taskName in @("NEXUS_INGEST_TOKEN", "M5_UI_OPERATOR_TOKEN")) {
        if (-not $taskSecret.data.$taskName) {
            $taskSecret.data | Add-Member -NotePropertyName $taskName -NotePropertyValue ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((New-NervaSecret)))) -Force
            $taskSecretChanged = $true
        }
    }
    if ($taskSecretChanged) {
        $taskSecret | ConvertTo-Json -Depth 30 | Invoke-NervaKube replace -f -
    }
}
~~~

Output should show the resource name, not credentials. Keep secrets out of Git.

### B4. Dependencies and stable demo workload

~~~powershell
Invoke-NervaKube apply -f observability/k8s/postgres.yaml
Invoke-NervaKube rollout status statefulset/postgres --timeout=300s
Invoke-NervaKube apply -f observability/k8s/prometheus.yaml -f observability/k8s/loki.yaml -f observability/k8s/alloy.yaml
Invoke-NervaKube rollout status deployment/prometheus --timeout=300s
Invoke-NervaKube rollout status deployment/loki --timeout=300s
Invoke-NervaKube rollout status daemonset/alloy --timeout=300s
Invoke-NervaKube apply -f apps/demo-microservices/k8s/payment-service.yaml
Invoke-NervaKube scale deployment/payment-service --replicas=1
Invoke-NervaKube set env deployment/payment-service PAYMENT_WORK_ITERATIONS=20000
Invoke-NervaKube rollout status deployment/payment-service --timeout=300s
~~~

One replica plus constant processing work supports a meaningful capacity drill. Set these BEFORE baseline capture and keep work/resources unchanged through the scenario.

Nada's commit 44264ef changed the committed traffic Pod default to 10 RPS. Use that healthy load for the baseline:

~~~powershell
New-Item -ItemType Directory -Path ".review-branches" -Force | Out-Null
Invoke-NervaKube create configmap payment-traffic-script --from-file=k8s-traffic.py=scripts/k8s-traffic.py --dry-run=client -o json | Invoke-NervaKube apply -f -
$taskTrafficSource = Get-Content "infrastructure/k8s/traffic-demo.yaml" -Raw
$taskHealthyTraffic = $taskTrafficSource
if ($taskHealthyTraffic -notmatch '(?m)^\s*- name: RPS\r?\n\s*value: "10"') { throw "Traffic manifest changed; inspect its RPS first." }
$taskHealthyTraffic | Set-Content ".review-branches/traffic-baseline.yaml" -Encoding utf8
Invoke-NervaKube apply -f .review-branches/traffic-baseline.yaml
Invoke-NervaKube wait --for=condition=Ready pod/payment-traffic --timeout=120s
~~~

A completed traffic Pod is not restarted by reapplying it. For a new run, inspect it, delete only that completed demo Pod, then apply the local baseline file again.

### B5. All six modules

~~~powershell
Invoke-NervaKube apply -f services/telemetry-intelligence/k8s/telemetry-intelligence.yaml
Invoke-NervaKube apply -f services/root-cause-analysis/k8s/root-cause-analysis.yaml
Invoke-NervaKube apply -f services/finops-engine/k8s/deployment.yaml
Invoke-NervaKube apply -f services/shared-nexus-api/k8s/shared-nexus-api.yaml
Invoke-NervaKube apply -f services/incident-memory/k8s/incident-memory.yaml
Invoke-NervaKube apply -f services/anomaly-engine/k8s/anomaly-engine.yaml
foreach ($taskModule in @("telemetry-intelligence", "root-cause-analysis", "finops-engine", "shared-nexus-api", "incident-memory", "anomaly-engine")) {
    Invoke-NervaKube rollout status "deployment/$taskModule" --timeout=300s
}
Invoke-NervaKube get pods
Invoke-NervaKube get services
~~~

The manifests configure HTTP providers, DB connections, tokens, storage and Kubernetes RBAC. M4's actuator is real here.

On later source updates, reloading a :dev image does not replace running Pods. Rebuild/import, intentionally restart updated module Deployments, wait for rollouts and recheck readiness. Do not restart during an approval/execution.

### B6. Forward ALL six modules

Each command needs its OWN PowerShell terminal. Leave all six running for check-stack.ps1 and host API access:

~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/telemetry-intelligence 8001:8001
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/anomaly-engine 8002:8002
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/root-cause-analysis 8003:8003
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/shared-nexus-api 8004:8004
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/incident-memory 8005:8005
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/finops-engine 8006:8006
~~~

Optional monitoring forwards, also separate terminals:

~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/prometheus 9090:9090
~~~
~~~powershell
kubectl --context kind-nexus -n nexus-demo port-forward service/loki 3100:3100
~~~

Open http://127.0.0.1:8005/. Module addresses match A4. Forwards can end on Pod restart; restart them. Do not use payment port-forward traffic to demonstrate scale: it sends requests to one Pod. The in-cluster generator uses Service DNS.

### B7. Baseline/readiness and decision token

After at least four minutes of healthy traffic, run the baseline block in A3, then WITHOUT the mock exception:

~~~powershell
.\scripts\check-stack.ps1
Invoke-RestMethod "http://127.0.0.1:8004/health" | ConvertTo-Json -Depth 12
~~~

Require six Ready=True results, real providers and remediation_backend=kubernetes.

Copy the operator token locally without displaying it:

~~~powershell
$taskSecretData = Invoke-NervaKube get secret nexus-runtime -o json | ConvertFrom-Json
$taskOperatorToken = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($taskSecretData.data.M5_UI_OPERATOR_TOKEN))
Set-Clipboard -Value $taskOperatorToken
~~~

Paste it into the dashboard decision prompt.

### B8. Actual rollback scenario

Capture the healthy template/replica count before injecting the fault:

~~~powershell
$taskPaymentBefore = Invoke-NervaKube get deployment payment-service -o json | ConvertFrom-Json
$taskPaymentBefore | ConvertTo-Json -Depth 50 | Set-Content ".review-branches/payment-before-fault.json" -Encoding utf8
$taskFaultPatch = @{
    spec = @{
        template = @{
            metadata = @{
                labels = @{ version = "v2" }
                annotations = @{
                    "nexus.io/previous-version" = "v1"
                    "nexus.io/version" = "v2"
                    "nexus.io/commit-sha" = "local-demo-v2"
                    "nexus.io/pipeline-id" = "local-rollback-demo"
                }
            }
            spec = @{
                containers = @(@{
                    name = "payment-service"
                    image = "nexus/payment-service:v2"
                    env = @(
                        @{ name = "VERSION"; value = "v2" },
                        @{ name = "FAULT_LATENCY_MS"; value = "600" },
                        @{ name = "FAULT_5XX_RATE"; value = "0.14" }
                    )
                })
            }
        }
    }
}
$taskFaultPatch | ConvertTo-Json -Depth 15 | Set-Content ".review-branches/payment-fault-patch.json" -Encoding utf8
Invoke-NervaKube patch deployment payment-service --type strategic --patch-file .review-branches/payment-fault-patch.json
Invoke-NervaKube rollout status deployment/payment-service --timeout=300s
~~~

This patches only fault fields, preserving replica count, processing work, resources and probes. Provenance lives on the release template and explicitly identifies a local demonstration, not a real external CD pipeline.

Keep healthy traffic running. Wait for AWAITING_APPROVAL in the UI, inspect faulty_deployment and ROLLBACK, then approve. Success requires actual v1 rollback AND measured recovery/RESOLVED. Inspect incident history and ask Copilot about that incident's stored outcome:

~~~powershell
Invoke-NervaKube get deployment payment-service -o jsonpath='{.spec.template.metadata.labels.version}'
Invoke-RestMethod "http://127.0.0.1:8004/api/incidents" | ConvertTo-Json -Depth 12
~~~

Do not manually undo and claim M4 performed it. Unknown/failed/escalated outcomes must stay factual.

If a failed demo needs MANUAL restoration of the captured healthy template:

~~~powershell
$taskSavedPayment = Get-Content ".review-branches/payment-before-fault.json" -Raw | ConvertFrom-Json
$taskRestorePatch = @(
    @{ op = "replace"; path = "/spec/template"; value = $taskSavedPayment.spec.template },
    @{ op = "replace"; path = "/spec/replicas"; value = $taskSavedPayment.spec.replicas }
)
$taskRestorePatch | ConvertTo-Json -Depth 50 | Set-Content ".review-branches/payment-restore-patch.json" -Encoding utf8
Invoke-NervaKube patch deployment payment-service --type json --patch-file .review-branches/payment-restore-patch.json
Invoke-NervaKube rollout status deployment/payment-service --timeout=300s
~~~

### B9. Actual scale scenario

Start after v1 is healthy, the earlier incident is terminal, and baseline traffic is still active. Measure a new healthy baseline if work/resources/replicas changed. Never replace it after overload starts.

~~~powershell
$taskSpikeTraffic = [regex]::Replace($taskTrafficSource, '(?m)^  name: payment-traffic\r?$', '  name: payment-traffic-spike')
$taskSpikeTraffic = [regex]::Replace($taskSpikeTraffic, '(?m)(^\s*- name: RPS\r?\n\s*value: )"[^"]+"', [System.Text.RegularExpressions.MatchEvaluator]{ param($taskMatch) $taskMatch.Groups[1].Value + '"150"' })
$taskSpikeTraffic | Set-Content ".review-branches/traffic-spike.yaml" -Encoding utf8
Invoke-NervaKube apply -f .review-branches/traffic-spike.yaml
Invoke-NervaKube wait --for=condition=Ready pod/payment-traffic-spike --timeout=120s
~~~

This adds a second generator targeting 150 RPS. Review the traffic_spike/SCALE proposal in the UI and approve. Keep both generators unchanged through action AND measured recovery.

The registry allows 1–10 replicas and a maximum scale step of 7, so 1 cannot jump directly to 10. The actual recommendation/outcome depends on observed load and available capacity.

~~~powershell
Invoke-NervaKube get deployment payment-service
Invoke-NervaKube logs payment-traffic-spike --tail=10
Invoke-RestMethod "http://127.0.0.1:8004/api/incidents" | ConvertTo-Json -Depth 12
~~~

Configured RPS is not guaranteed achieved RPS under overload. Judge recovery from M1's pinned SLO/post-action windows, not the generator's whole-run latency percentile.

After recording the outcome, stop the extra generator:

~~~powershell
Invoke-NervaKube delete pod payment-traffic-spike --ignore-not-found
~~~

### B10. Optional generated Copilot on Kubernetes

The committed M5 Kubernetes manifest does not configure an external model. Recorded-answer mode works without one. Add a separate provider secret if needed:

~~~powershell
$taskCopilotSecret = @{
    apiVersion = "v1"
    kind = "Secret"
    metadata = @{ name = "nerva-copilot"; namespace = "nexus-demo" }
    type = "Opaque"
    stringData = @{
        GEMINI_API_KEY = (Read-Host "Gemini API key")
        M5_LLM_MODEL = (Read-Host "Model ID available to your Gemini account")
    }
}
$taskCopilotSecret | ConvertTo-Json -Depth 8 | Invoke-NervaKube apply -f -
Invoke-NervaKube set env deployment/incident-memory --from=secret/nerva-copilot
Invoke-NervaKube rollout status deployment/incident-memory --timeout=300s
~~~

Restart its host forward if needed. Provider 429/quota/model errors are not successful generated-answer tests.

### B11. PowerShell in-cluster helper

Do not paste Bash's input redirection form (< scripts/live-drill.py) into PowerShell. Stream the helper instead:

~~~powershell
Get-Content scripts/live-drill.py -Raw | Invoke-NervaKube exec -i deployment/shared-nexus-api '--' python - status
~~~

This prints current incident/dispatch status. Other helper commands are documented in scripts/live-drill.py; some mutate workflows and are not required for normal UI operation.

### B12. Logs and stopping

~~~powershell
Invoke-NervaKube get pods
Invoke-NervaKube get pvc
Invoke-NervaKube get events --sort-by=.lastTimestamp
Invoke-NervaKube logs deployment/shared-nexus-api --tail=100
Invoke-NervaKube logs deployment/anomaly-engine --tail=100
~~~

Use describe pod with its actual name for Pending/CrashLoopBackOff. Use logs --previous for a previously crashed container.

Stop demo load:

~~~powershell
Invoke-NervaKube delete pod payment-traffic payment-traffic-spike --ignore-not-found
~~~

Close forwards with Ctrl+C. This closes host access but leaves cluster services and the database running. Later restart forwards/traffic and recheck freshness/readiness. Stopping Docker Desktop stops the Docker runtime. Deleting the kind cluster deletes its local cluster data and is not required for normal shutdown.

## 4. Optional verification tests

Tests are additional verification, not startup prerequisites. With Python 3.11+ installed:

~~~powershell
python -m venv .venv
$taskPython = (Resolve-Path ".venv/Scripts/python.exe").Path
& $taskPython -m pip install pytest pyyaml -r services/telemetry-intelligence/requirements.txt -r services/anomaly-engine/requirements.txt -r services/root-cause-analysis/requirements.txt -r services/shared-nexus-api/requirements.txt -r services/finops-engine/requirements.txt -r services/incident-memory/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $taskPython -m pytest tests/contract tests/m1 tests/m2 tests/test_k8s_traffic.py tests/test_service_registry.py tests/test_deployment_wiring.py -q
if ($LASTEXITCODE -ne 0) { throw "Root tests failed." }
foreach ($taskService in @("root-cause-analysis", "shared-nexus-api", "finops-engine")) {
    Push-Location "services/$taskService"
    try { & $taskPython -m pytest tests -q } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Tests failed: $taskService" }
}
Push-Location services/incident-memory
try { & $taskPython -m unittest discover -s tests -q } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "M5 tests failed." }
node --test ui/copilot/storage.test.mjs ui/incident/view.test.mjs
if ($LASTEXITCODE -ne 0) { throw "UI tests failed." }
& $taskPython services/incident-memory/scripts/evaluate.py
~~~

PostgreSQL suites need their documented disposable-database settings or some tests skip. See docs/RUNBOOK.md, docs/FULL_STACK_VERIFICATION.md and tests/integration/README.md for database/integration tests. Do not use the retained demo database as a disposable test database. Local passing tests do not prove a remote GitHub CI run.

## 5. Common failures

| Symptom | Next check |
| --- | --- |
| Docker Client but no Server | Docker Desktop running, Linux engine/context, daemon access |
| Compose missing variable in logs/down | Load saved settings in that shell using A8 |
| Missing/stale baseline | Keep healthy payment traffic active; allow multiple scrapes, retry |
| M2 silent | /health polling/handoff and dispatch failures |
| M3 unknown/stale evidence | Incident context, release provenance and evidence age |
| M4 lease/storage not ready | PostgreSQL and M4 /health; avoid two writers on one DB |
| Only some Kubernetes modules reachable | Start all six B6 forwards |
| ImagePullBackOff | Exact loaded tag and node image presence; rebuild/import |
| Pod Pending | describe pod, node capacity, PVC/default storage class |
| Approval refused | Operator token, proposal digest/expiry and AWAITING_APPROVAL state |
| Action succeeded but incident escalated | M1 measured recovery; execution success is not SLO recovery |
| Empty UI | Shared API connectivity and whether incidents actually occurred |
| Generated chat offline | Provider key/model/quota and M5 logs; recorded-answer mode is separate |

Readiness plus live telemetry verifies configured integration. A real remediation demonstration additionally requires actual Kubernetes execution, measured recovery, and corresponding incident memory/Copilot records. Retain failed/mock outcomes honestly.

