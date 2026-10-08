# M5 standalone demo

All data in this rehearsal is synthetic and labeled mock. No other member's service or LLM is required.

## Start

From the repository root in PowerShell:

```powershell
$env:M5_DB_PASSWORD = '<local-url-safe-password>'
$env:M5_UI_MODE = 'mock'
docker compose -f services/incident-memory/compose.yml up --build -d
docker compose -f services/incident-memory/compose.yml exec memory python scripts/seed_demo.py
```

Open http://127.0.0.1:8005. If that port already hosts a local preview, set `M5_HOST_PORT=18005` before starting Compose and open port 18005 instead.

## Five-minute walkthrough

1. **Overview:** point out the mock badge, four incidents and resolved counts.
2. **Incident Center / INC-001:** show the timeline, RCA evidence, recommendation, separate action and recovery outcomes, and INC-DEMO-HISTORY as a previous similar incident. Approval stays disabled in mock mode.
3. **Memory:** store the resolved snapshot again; explain `already_stored` means a safe retry. Records persist in PostgreSQL. Conflicting facts cannot overwrite archived evidence.
4. **Copilot:** ask “What is the root cause?” and “Did it work?”. Expand/read citations: each identifies the incident, provenance, exact field and value.
5. **Missing evidence:** select INC-DEMO-TRAFFIC in Incident Center, return to Copilot and ask “What changed before the incident?”. Expect “NEXUS does not have enough recorded evidence.” No deployment event exists for this mock scenario.
6. **FinOps:** explain that estimated options differ from actual measured financial savings. Do not present the fixtures as operational savings.

Explain: retrieval happens before the answer is rendered. P0 filters by service/type and ranks tag/feature overlap. No LLM or embeddings are required; pgvector would add semantic matching as a future bonus.

## Repeatable verification

With the stack running and the same environment settings:

```powershell
services/incident-memory/.venv/Scripts/python.exe services/incident-memory/scripts/verify_docker.py
```

This seeds mock records, runs all tests against PostgreSQL, checks exact record persistence after both service and database restarts, stops PostgreSQL to verify explicit unavailable responses, restarts it, retries the producer's retained request, and checks UI assets, retrieval, citations and missing evidence. It temporarily interrupts this local stack and leaves it running afterwards. A unique synthetic verification record remains in the mock namespace.

The M5 GitHub workflow repeats these Docker checks and the nine-case Copilot evaluation. Team integration with real incidents remains a separate gate.
