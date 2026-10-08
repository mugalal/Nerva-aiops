# M5 verification evidence

Local verification: **2026-10-08**. All fixture data is explicitly mock. No real incident, financial savings, or successful infrastructure operation is claimed.

## API / persistence checks

`python -m unittest discover -s tests -v` from `services/incident-memory`:

- **20 passed, none skipped** inside the Docker image, including the real PostgreSQL round-trip test. The other 19 behavior tests use temporary SQLite databases and FastAPI's HTTP test client.
- Windows local verification also passed 19 behavior tests; PostgreSQL was subsequently verified in Docker.
- Database unavailable and shared API unavailable return explicit failures; live UI does not silently fall back to mocks.
- Action success and recovery are tested separately, including successful action with unsuccessful recovery.
- Cross-incident records and conflicting archived evidence are rejected; identical retries and missing-context enrichment are accepted.
- Live adapter and approval requests use a simulated HTTP transport. This verifies routing and payloads, **not integration with M4 or shared core**.

GitHub workflow `.github/workflows/m5.yml` provisions PostgreSQL, runs the evaluation, builds the Docker image and checks restarts and database outage/retry behavior. GitHub execution status is tracked in the pull request checks.

## Docker / PostgreSQL standalone verification

On 2026-10-08, Docker Desktop ran PostgreSQL 16 and the built M5 image in explicit mock mode on host port 18005 (an existing local preview occupied 8005). `scripts/verify_docker.py` confirmed:

- Exact archived record, including storage timestamp, survives M5 service restart and PostgreSQL restart.
- Stopping PostgreSQL produces `unavailable` health and HTTP 503 for store, search and Copilot.
- Restarting PostgreSQL restores storage without restarting M5; a producer-retained request succeeds, and its identical retry returns `already_stored`.
- Packaged UI/assets and mock overview/detail endpoints return HTTP 200.
- Previous similar incident is retrieved, Copilot returns citations, and absent deployment evidence produces `insufficient_evidence` without citations.

All stored verification records are synthetic and use the mock namespace. See `docs/M5_DEMO.md` for repeatable commands and the presentation walkthrough.

## Retrieval / Copilot evaluation

`python scripts/evaluate.py` recreates a temporary mock dataset and checks expected source, retrieved source, field-citation grounding, unsupported claims, and status. **9/9 cases passed.** Citation grounding checks compare exact recorded field values; they are bounded behavioral checks, not a general proof for arbitrary natural-language questions.

| Question | Expected source | Observed status | Citation check |
|---|---|---|---|
| What happened? | INC-001 | ok | passed |
| What changed before the incident? | INC-001 | ok | passed |
| What is the root cause? | INC-001 | ok | passed |
| Have we seen this before? | INC-DEMO-HISTORY | ok | passed |
| What action was taken? | INC-001 | ok | passed |
| Did it work? | INC-001 | ok | passed |
| Root cause of traffic incident | INC-DEMO-TRAFFIC | ok | passed |
| Change before traffic incident, with no deployment record | none | insufficient_evidence | no fabricated citation |
| Invent financial savings | none | unsupported_question | no fabricated citation |

Ranking tests ensure the correct bad-deployment record ranks first for deployment/5xx criteria, explicit traffic type excludes faulty-deployment causes, and irrelevant service is excluded. No matching source returns an empty result. Current incident is excluded from similar-history searches.

## UI verification

Browser inspection confirmed Overview renders incident counts/list; Incident Center renders timeline, RCA evidence, recommendations, action result, recovery measurements, memory and similar-history state; FinOps distinguishes estimated options from actual financial savings; Copilot shows a recorded answer and field citations. Mock badge is visible and approval is disabled in mock mode. API tests verify the UI entry point, assets, mock overview/detail routes, and disabled mock approval.

## Pending team gates

- Real M2/M3/M4/M1/M6 records and measured impact/timing fields.
- Shared-core route/payload agreement; defaults in the adapter are proposed, not previously frozen team contracts.
- M4 approval protocol with authenticated human identity and required proposal/action IDs.
- Actual resolved bad-deploy and traffic-spike scenarios stored after recovery validation.
- A real shared consumer displaying a previous similar incident and a grounded answer from real stored data.

These are real integration gates. Independent M5 implementation does not establish that the full NEXUS system has passed them. Embeddings, pgvector, LLM generation and richer conversational RAG remain optional runbook bonuses.
