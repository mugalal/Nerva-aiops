# NEXUS AIOps & FinOps Platform

This repository contains the integrated NEXUS project for the NTI AIOps track.

M1 establishes the shared runtime base:

- runtime conventions in `docs/runtime-conventions.md`
- shared config helpers in `shared/config`
- shared JSON logging in `shared/logging`
- telemetry mock fixture in `mocks/mock_metrics.json`
- M1 telemetry service skeleton in `services/telemetry-intelligence`
- runtime smoke check in `scripts/smoke-check.sh`
- local observability stack in `observability/docker-compose.yml`

## Run M1 Telemetry Service

```bash
cd services/telemetry-intelligence
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Run Observability Stack

```bash
cd observability
docker compose up -d
```

Grafana: `http://localhost:3000`

Prometheus: `http://localhost:9090`

Loki: `http://localhost:3100`

## Smoke Check

```bash
bash scripts/smoke-check.sh
```

The smoke check expects all module services to expose `GET /health`. Early in the project, failures for modules that are not built yet are expected and useful.

## M5 Incident Memory & Copilot

The M5 service and shared UI are implemented in `services/incident-memory/` and `ui/`.
See [setup and behavior](services/incident-memory/README.md), [team integration protocol](docs/M5_INTEGRATION.md), and [verification / pending gates](docs/M5_EVALUATION.md).
M5 uses port 8005; PostgreSQL is supported for integration, with SQLite for local development.
Mock previews are explicitly labeled. The shared frozen contracts are unchanged.

