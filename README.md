# How to run NEXUS: the complete guide

Everything here is PowerShell from the repository root (the folder that contains README.md). There are two ways to run NEXUS. Pick one first.

| Feature | Path A: Docker Compose | Path B: local Kubernetes (kind) |
| :--- | :--- | :--- |
| **Time to first success** | about 10 minutes | about 30 to 45 minutes |
| **What you see working** | metrics, logs, dashboards, anomaly detection, incidents, root cause, proposals, approval, memory, Copilot | everything in A plus real rollback / scale and measured recovery |
| **Does approving change anything real?** | No. The actuator is mock on purpose | Yes, but only in the isolated kind cluster |
| **Use it for** | learning, UI tour, quick demo of the flow | the real demonstration and the defense |

Start with Path A. Move to Path B when you want to show a real fix.

Safety controls (incident coalescing, measured scale-down, autoscaler and dependency guards, hash-chained audit, leader election and their settings) are listed in [docs/SAFETY_CONTROLS.md](docs/SAFETY_CONTROLS.md).

---

## 0. Prerequisites (both paths)

| Tool | Needed for | Check |
| :--- | :--- | :--- |
| **Docker Desktop** (running) | both | `docker version` |
| **Git** | both | `git --version` |
| **Python 3.11+** | tests and helper scripts | `python --version` |
| **PowerShell 5.1+** | all scripts | already on Windows |
| **kubectl and kind** | Path B only | `kubectl version --client`, `kind --version` |

One command checks the tools: `.\scripts\check-env.ps1`. Give Docker Desktop at least 6 GB RAM.

---

## Path A: Docker Compose

### A1. Start everything

```powershell
.\scripts\start-stack.ps1
```

What it does: creates your passwords once and saves them in `.review-branches\stack-runtime.json` (git-ignored, never printed), then builds all images and starts 13 containers, waiting until they are healthy. The first run takes several minutes because it builds images. Re-running reuses the same passwords, so your data stays accessible. Add `-NoBuild` to skip building.

It deletes nothing. Data lives in Docker volumes.

### A2. Create steady traffic (keep this window open)

Open a second PowerShell window:

```powershell
.\scripts\generate-traffic.ps1 -DurationSeconds 900 -Concurrency 5
```

NEXUS needs real requests to measure. Leave it running for the next steps.

### A3. Measure the healthy baseline (after about 3 minutes of traffic)

```powershell
$end = [DateTimeOffset]::UtcNow.AddSeconds(-15)
$body = @{ service='payment-service'; start=$end.AddSeconds(-75).ToString('o'); end=$end.ToString('o'); step_seconds=15 } | ConvertTo-Json
Invoke-RestMethod -Method Post http://localhost:8001/internal/baselines/measure -ContentType application/json -Body $body
```

Success looks like a JSON answer with `sample_count` of 6 and a `thresholds` block. If you get an error about missing or stale data, traffic is not running or is too new; wait one minute and retry. Without a baseline, M3 cannot diagnose anything.

### A4. Check all six modules

```powershell
.\scripts\check-stack.ps1 -AllowMockActuator
```

Expect a table with `Ready = True` for M1 to M6. (`-AllowMockActuator` tells the check that "M4 reports degraded because its actuator is mock" is expected in Compose.)

### A5. Open the tools

| What | URL | Login |
| :--- | :--- | :--- |
| **NEXUS page** (incidents, approvals, Copilot) | `http://127.0.0.1:8005/` | none; decisions need the operator token below |
| **Grafana** (6 panels) | `http://localhost:3000` | user `admin`; password is `GRAFANA_ADMIN_PASSWORD` if you set it, otherwise Grafana's weak default (change it) |
| **Prometheus** | `http://localhost:9090` | none |
| **Service docs** (Swagger) | `http://localhost:8001/docs`, `8002`, `8003`, `8004`, `8006` | none |

Your operator token for approving in the page is the `M5_UI_OPERATOR_TOKEN` value inside `.review-branches\stack-runtime.json`. Open that file yourself; do not paste it into chats or commits.

### A6. See an incident appear

With traffic still running, start the faulty version:

```powershell
docker compose -f observability/docker-compose.yml -f observability/docker-compose.faulty.yml up -d payment-service
```

Within about a minute M2 raises an alert and M4 creates an incident, then waits 60 seconds before diagnosing. Watch the NEXUS page. What to expect in Compose (reasoned from the code, not re-run for this guide): Compose has no Kubernetes, so M1 cannot see a deployment event and M3 will likely answer "unknown", so the incident escalates with no action. That is the safe-failure behaviour, and it is a good thing to show. Real rollback needs Path B.

Put the healthy version back:

```powershell
docker compose -f observability/docker-compose.yml up -d --force-recreate payment-service
```

### A7. Stop and clean up

```powershell
docker compose -f observability/docker-compose.yml --profile integration down
```

This stops the containers and keeps your data volumes. Only add `-v` if you truly want to erase PostgreSQL, baselines and metrics history; that cannot be undone.

---

## Path B: local Kubernetes (the real demo)

Do Path A at least once first so you know what healthy looks like. Run Path B instead of Path A, not at the same time (they use overlapping ports on your machine).

### B1. Create the cluster

```powershell
kind create cluster --config infrastructure/kind-cluster.yaml
kubectl config use-context kind-nexus
kubectl get nodes          # expect 3 nodes: 1 control-plane, 2 workers
```

Always confirm the context is `kind-nexus` before running commands. Never point these steps at a real cluster.

### B2. Build and load the images

```powershell
$images = @{
  'telemetry-intelligence'='services/telemetry-intelligence/Dockerfile'
  'anomaly-engine'='services/anomaly-engine/Dockerfile'
  'root-cause-analysis'='services/root-cause-analysis/Dockerfile'
  'shared-nexus-api'='services/shared-nexus-api/Dockerfile'
  'incident-memory'='services/incident-memory/Dockerfile'
  'finops-engine'='services/finops-engine/Dockerfile'
  'postgres'='observability/postgres/Dockerfile'
}
foreach ($name in $images.Keys) {
  docker build --provenance=false -f $images[$name] -t "nexus/${name}:dev" .
  kind load docker-image "nexus/${name}:dev" --name nexus
}
docker build --build-arg VERSION=v1 -t nexus/payment-service:v1 apps/demo-microservices/payment-service
docker build --build-arg VERSION=v2 -t nexus/payment-service:v2 apps/demo-microservices/payment-service
kind load docker-image nexus/payment-service:v1 --name nexus
kind load docker-image nexus/payment-service:v2 --name nexus
```

*(If `kind load` fails for the PostgreSQL image, see the note in `docs/FULL_STACK_VERIFICATION.md`.)*

### B3. Create the secrets (once)

The modules read their passwords from one Kubernetes Secret named `nexus-runtime`. Create it with random values; they never touch Git:

```powershell
kubectl apply -f observability/k8s/namespace.yaml
function New-Secret { -join ((48..57)+(97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }) }
$db = New-Secret
kubectl -n nexus-demo create secret generic nexus-runtime `
  --from-literal=POSTGRES_USER=nexus --from-literal=POSTGRES_DB=nexus --from-literal=POSTGRES_PASSWORD=$db `
  --from-literal=DATABASE_URL="postgresql://nexus:$db@postgres:5432/nexus" `
  --from-literal=NEXUS_APPROVAL_TOKEN=$(New-Secret) --from-literal=NEXUS_INGEST_TOKEN=$(New-Secret) `
  --from-literal=M5_UI_OPERATOR_TOKEN=$(New-Secret) --from-literal=M5_OPERATOR_ID=$env:USERNAME
```

The two token values must differ from each other (M4 refuses to start otherwise). To read your operator token later (for the browser page):

```powershell
kubectl -n nexus-demo get secret nexus-runtime -o jsonpath='{.data.M5_UI_OPERATOR_TOKEN}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
```

### B4. Deploy, in this order

```powershell
kubectl apply -f observability/k8s/postgres.yaml
kubectl rollout status -n nexus-demo statefulset/postgres --timeout=180s
kubectl apply -f observability/k8s/prometheus.yaml -f observability/k8s/loki.yaml -f observability/k8s/alloy.yaml
kubectl apply -f apps/demo-microservices/k8s/payment-service.yaml
kubectl apply -f services/telemetry-intelligence/k8s/telemetry-intelligence.yaml
kubectl apply -f services/root-cause-analysis/k8s/root-cause-analysis.yaml
kubectl apply -f services/finops-engine/k8s/deployment.yaml
kubectl apply -f services/shared-nexus-api/k8s/shared-nexus-api.yaml
kubectl apply -f services/incident-memory/k8s/incident-memory.yaml
kubectl apply -f services/anomaly-engine/k8s/anomaly-engine.yaml
kubectl get pods -n nexus-demo -w      # wait until every pod is Running and Ready, then Ctrl+C
```

If a pod stays `Pending`, run `kubectl describe pod <name> -n nexus-demo`; a pending volume means the cluster has no default storage class.

### B5. Reach the services from your laptop

Cluster services are not exposed. Use one terminal per forward and leave them open:

```powershell
kubectl -n nexus-demo port-forward service/incident-memory 8005:8005     # the NEXUS page
kubectl -n nexus-demo port-forward service/telemetry-intelligence 8001:8001
kubectl -n nexus-demo port-forward service/shared-nexus-api 8004:8004
kubectl -n nexus-demo port-forward service/prometheus 9090:9090
```

### B6. Steady traffic from inside the cluster

A port-forward sticks to one pod, so load must run inside the cluster:

```powershell
kubectl apply -f infrastructure/k8s/payment-traffic-script-configmap.yaml
kubectl apply -f infrastructure/k8s/traffic-demo.yaml
```

`traffic-demo.yaml` defaults to 200 requests per second for an hour. For the baseline-sized demo, edit RPS in that file to about 10 first. Keep traffic running through the whole demo and do not change it.

### B7. Baseline, then check

Wait about 4 minutes, then measure the baseline exactly like step A3 (using `http://localhost:8001`). Then:

```powershell
.\scripts\check-stack.ps1
```

All six should be `Ready = True` (no `-AllowMockActuator` here: the actuator is real).

### B8. Demo 1: faulty deployment and rollback

```powershell
kubectl apply -f apps/demo-microservices/k8s/payment-service-v2-faulty.yaml
```

Then, in the NEXUS page (`http://127.0.0.1:8005/`):

1. About 1 to 2 minutes later an incident appears. Wait for status `AWAITING_APPROVAL`.
2. Read the root cause (`faulty_deployment`) and the proposal (`ROLLBACK`, v2 to v1).
3. Click **Approve** and enter your operator token.
4. Watch it go `EXECUTING`, `VALIDATING`, then `RESOLVED` (about 1.5 minutes of measured health is required).
5. Ask Copilot: *"Did it work?"*
6. Check from the command line:
   ```powershell
   kubectl get deploy payment-service -n nexus-demo -o jsonpath='{.spec.template.metadata.labels.version}'
   ```
   should print `v1`.

### B9. Demo 2: traffic spike and scale-out

Start a second, bigger generator by copying `infrastructure/k8s/traffic-demo.yaml` to a new name with a different `pod name:` and `RPS` of about 150. After the incident appears, the proposal is a `SCALE` to a larger replica count. Approve it the same way. Remove your extra generator pod when done:

```powershell
kubectl delete pod <your-pod-name> -n nexus-demo
```

### B10. Stop

```powershell
kubectl delete -f infrastructure/k8s/traffic-demo.yaml --ignore-not-found
kind delete cluster --name nexus     # ONLY if you want to throw the whole cluster and its data away
```

---

## Run the tests (no stack needed)

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install pytest pyyaml -r services/telemetry-intelligence/requirements.txt -r services/anomaly-engine/requirements.txt -r services/root-cause-analysis/requirements.txt -r services/shared-nexus-api/requirements.txt -r services/finops-engine/requirements.txt -r services/incident-memory/requirements.txt
python -m pytest tests/contract tests/m1 tests/m2 tests/test_k8s_traffic.py tests/test_service_registry.py tests/test_deployment_wiring.py -q
cd services/shared-nexus-api ; python -m pytest tests -q ; cd ../..
cd services/finops-engine ; python -m pytest tests -q ; cd ../..
cd services/root-cause-analysis ; python -m pytest tests -q ; cd ../..
cd services/incident-memory ; python -m unittest discover -s tests -q ; cd ../..
node --test ui/copilot/storage.test.mjs ui/incident/view.test.mjs
```

If pytest fails with a temp-folder permission error on Windows, add `--basetemp C:\t\run`.

---

## If something goes wrong

| Symptom | Likely cause and fix |
| :--- | :--- |
| `start-stack.ps1` fails on a service | Run `docker compose -f observability/docker-compose.yml --profile integration logs <service>`; most often Docker is out of memory or a port is in use (8000-8006, 3000, 3100, 9090) |
| Baseline request returns 503 / "stale" / "missing" | Traffic not running or too new; wait one more minute |
| No incident appears | Traffic stopped, no baseline, or M2 not healthy: open `http://localhost:8002/health` |
| Incident ends ESCALATED with no action | Often correct and safe (unknown cause, stale evidence, expired proposal). Read the "Audit" and "Missing information" panels on the incident page |
| Approve button disabled | Proposal expired (5 minutes) or no operator token configured; the page says which |
| Approve says "digest required" / "expired" | Reload the incident and approve the fresh proposal |
| Pods CrashLoopBackOff in Kubernetes | `kubectl logs <pod> -n nexus-demo`; a missing Secret key is the usual cause (repeat B3) |
| Port already in use | Another stack is running; stop it first, or change the left side of the port mapping |
