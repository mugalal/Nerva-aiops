# M5: incident memory, grounded Copilot and shared UI

Implements persistent resolved-incident storage, structured similarity retrieval, six operational Copilot questions with exact field citations, and the shared Overview, Incident Center, FinOps and Copilot UI with explicit mock/live modes and dependency failure states.

## Validation

- 20/20 tests passed inside Docker, including PostgreSQL round-trip.
- 9/9 Copilot evaluation cases passed.
- Docker verification confirmed exact record persistence after service/database restarts, HTTP 503 during database outage, successful producer retry after recovery, UI assets, retrieval, citations and missing-evidence behavior.
- Browser rehearsal confirmed incident evidence, disabled mock approval, cited answers, missing-evidence response and FinOps estimate labels.
- [GitHub M5 checks passed](https://github.com/mugalal/Nerva-aiops/actions/runs/37793551276) for implementation commit `dc03d0078746c6fbaf89f12dc9d079ccc8a97e06`.

## Handoff

- Setup: `services/incident-memory/README.md`
- Demo: `docs/M5_DEMO.md`
- Evidence: `docs/M5_EVALUATION.md`
- Team protocol: `docs/M5_INTEGRATION.md`

Real shared-core routes, M4 authenticated approvals and real bad-deploy/traffic scenarios remain team integration gates. All standalone demo data is explicitly mock; frozen contracts and canonical mocks remain unchanged.

Target: `main`; source: `codex/m5-memory-copilot`.
