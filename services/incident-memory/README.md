# M5 — Incident Memory & Copilot

Implements the M5 **P0** runbook: persistent resolved-incident storage, structured similarity retrieval, operational answers with field citations, and the shared UI shell. Other members' diagnosis, decisions, execution, recovery validation, and FinOps computation are not implemented here.

## Architecture

Open-ended AI chat and follow-up conversation are now available in the Copilot UI. See `docs/M5_AI_CHAT.md` at repository root for model setup, conversation behavior and verification. The deterministic six-question API remains available as an offline fallback.

```text
Shared core / resolved incident aggregator
  └─ POST /internal/memory/store → validated immutable snapshot → database
Operator / shared UI
  ├─ POST /internal/memory/search → type/service filters + overlap ranking
  └─ POST /internal/copilot/query → retrieve records → render grounded answer + citations
Shared UI → M5 configurable HTTP adapter → shared core API → other members
```

`memory` retains the exact eight fields in `contracts/incident_memory.json`. Detailed original upstream records and optional impact/timing fields live in an M5-owned `context` envelope. Frozen `contracts/` and canonical `mocks/` are unchanged. Source is a caller-supplied provenance label, not an authentication or evidence-verification mechanism. Mock and real records have separate namespaces and never mix during retrieval.

## Files and responsibilities

| File | Purpose |
|---|---|
| `app/models.py` | M5 envelope validation and upstream consistency checks |
| `app/storage.py` | SQLite/PostgreSQL persistence, protected facts, optional enrichment, idempotent retry |
| `app/retrieval.py` | Structured filtering, overlap scoring, explanations |
| `app/copilot.py` | Six supported intents, evidence citations, missing-data answers |
| `app/providers.py` | Configurable shared API adapter and approval forwarding |
| `app/main.py` | HTTP endpoints, dependency states, UI hosting |
| `../../ui/` | Overview, Incident Center, FinOps, Copilot; no RCA/decision rules |
| `scripts/seed_demo.py` | Explicit mock-only seed; never runs automatically |
| `tests/` | Behavior tests and optional real PostgreSQL round trip |

## Run locally on Windows

Run from the repository root. PowerShell is the terminal used below. Python's `venv` creates an isolated environment; `pip` installs the service libraries into it. This tests the dependency layer. Successful installation ends without errors; a download error indicates dependency/network trouble rather than a NEXUS failure.

```powershell
python -m venv services\incident-memory\.venv
services\incident-memory\.venv\Scripts\python.exe -m pip install -r services\incident-memory\requirements.txt
```

Change to the service folder so relative database paths resolve consistently. SQLite is a **local development recommendation**, not a replacement of PostgreSQL for shared integration. The `.env.example` documents settings; environment variables must be set explicitly or loaded using Uvicorn's `--env-file` option.

The seed command creates persistent mock records: bad deployment, traffic spike, irrelevant database incident, and a previous similar bad deployment. Expected output is `stored [mock]` (or `already stored [mock]` on a retry). It tests model validation and local database writes. A validation failure indicates a fixture/contract mismatch; a storage error indicates a filesystem/database problem.

```powershell
cd services\incident-memory
$env:M5_UI_MODE = 'mock'
$env:DATABASE_URL = 'sqlite:///./data/memory.db'
.venv\Scripts\python.exe scripts\seed_demo.py
```

Uvicorn hosts the FastAPI application. `--host 127.0.0.1` makes this a local preview; `--port 8005` follows the team's M5 port convention. Expected output says the application started and is listening. A port-in-use error means another server already occupies 8005; a missing module means dependencies or the working folder are incorrect.

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8005
```

Open [UI](http://127.0.0.1:8005/) and [interactive API documentation](http://127.0.0.1:8005/docs). Mock mode is labeled throughout. The UI has no actual approval/execution capability in mock mode. Health is `degraded` while the UI uses mocks; memory endpoints remain functional. Health is also degraded if the live shared API is unavailable and unavailable if the database fails.

## PostgreSQL / Docker

Docker Compose runs PostgreSQL and M5 together. It tests packaging, database connectivity, and HTTP serving. First set a local database password containing URL-safe characters (or supply a percent-encoded connection string independently). This password is local development configuration; do not commit it.

```powershell
$env:M5_DB_PASSWORD = '<choose-a-local-url-safe-password>'
docker compose -f services/incident-memory/compose.yml up --build -d
```

Run this from repository root. Expected output shows PostgreSQL healthy and M5 started. Docker daemon errors mean Docker Desktop is not running; connection errors mean database configuration or networking needs inspection. Live UI may be degraded until the shared API is running. The named PostgreSQL volume persists records across container restarts. No demo seed is performed automatically.

For explicit mock previews with Docker, set `M5_UI_MODE=mock` before starting Compose. The following executes the seed inside the running M5 container; expected output is four mock records:

```powershell
docker compose -f services/incident-memory/compose.yml exec memory python scripts/seed_demo.py
```

## API examples

See `docs/M5_INTEGRATION.md` at repository root for the complete handoff protocol. `/docs` exports the exact envelope schema and `/openapi.json` provides machine-readable schemas.

Minimal resolved store (without optional context):

```json
{
  "source": "real",
  "resolved": true,
  "memory": {
    "incident_id": "INC-123", "incident_type": "faulty_deployment",
    "service": "payment-service", "root_cause": "faulty_deployment",
    "action": "ROLLBACK", "action_success": true, "recovered": true,
    "tags": ["deployment", "latency", "5xx"]
  },
  "context": {}
}
```

`resolved=true` is an explicit assertion by the producer. When the incident record is supplied, its status must be `RESOLVED`. The boolean fields are required by the frozen contract; do not use `false` to mean unknown. Do not store before the producer has enough evidence to supply these fields. Failed recovery can be stored if the incident was explicitly closed/resolved by the incident owner; closure and successful recovery are distinct facts.

Incomplete **optional** context is accepted: known memory facts remain queryable, but unsupported questions return `NEXUS does not have enough recorded evidence.` Supplied upstream records must match frozen top-level fields/types; matching incident IDs, affected service, action outcome, and recovery outcome are checked. In particular, M1's current scaffold `status=unknown` recovery is not a canonical verified RecoveryResult and must not be archived as one. MTTD/MTTR are optional producer measurements; M5 never guesses them from action duration or recovery duration.

Search:

```json
{"source":"mock","service":"payment-service","incident_type":"faulty_deployment","tags":["deployment"],"features":["latency_p95_ms"],"exclude_incident_id":"INC-001","limit":5}
```

Specified type and service are hard filters. Score is 4 for matching type + 3 for matching service + tag Jaccard overlap + feature-name Jaccard overlap. Scores are ranking values, **not probabilities or ML confidence**. Equal scores sort by incident ID. Numeric feature distance/embeddings are not implemented in P0. Search requires at least one criterion; it returns an empty list for no matches, never irrelevant filler.

Copilot:

```json
{"source":"mock","incident_id":"INC-001","question":"Did it work?"}
```

Or provide `search` criteria instead of an incident ID to retrieve the top matching record before answering. A missing explicit incident ID never falls back to an unrelated incident. Every factual answer carries source ID, provenance, field path, and recorded value. No LLM or API key is required: this P0 uses deterministic intent routing and grounded answer rendering. Unsupported/free-form general questions are reported as unsupported. Embeddings, pgvector, richer LLM generation, and conversational context remain **runbook bonuses**.

## Test / evaluate

Python's built-in unittest runner executes API behavior tests. These test persistence, conflicting writes, similarity ranking, all six questions and their exact citations, mock separation, incomplete context, dependency failures, and approval routing. Expected result is `OK`, with PostgreSQL explicitly skipped unless `M5_TEST_DATABASE_URL` is set. Failures identify the behavior that regressed rather than proving the entire project is broken.

From the service folder:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Set `M5_TEST_DATABASE_URL` to a **disposable PostgreSQL database** to run the real database round-trip test. It creates its own test record and deletes only that record afterwards. GitHub CI supplies PostgreSQL and runs it automatically. See `docs/M5_EVALUATION.md` for test evidence and remaining real integration gates.

## Current limitations

This is a local/team P0 module, not a production access-control system. Internal endpoints trust the shared core; only expose them on the team's private network. Approval forwarding is disabled until its route is configured. M4 must enforce authenticated human identity, authorization, proposal identity, audit logging, and execution guardrails; a shared service token alone does not prove a human approval. Production deployment needs identity propagation/access control before exposing the UI or internal endpoints externally.

The UI adapter paths are **proposed M5 integration defaults**, since a shared-core HTTP contract is not present in this repository. Configure paths and adapt `app/providers.py` if the team's payload differs. Other modules are still independently owned. Real flagship incidents, measured MTTD/MTTR, and a real shared consumer must be demonstrated at integration before the full runbook is considered complete. PostgreSQL/Docker standalone verification is documented in `docs/M5_EVALUATION.md`; repeatable demo and outage/restart checks are in `docs/M5_DEMO.md`.
