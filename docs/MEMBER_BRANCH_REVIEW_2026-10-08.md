# Member branch review — 8 October 2026

Reviewed freshly fetched remote branches against main and the frozen contracts, kickoff ownership, and runtime conventions. No member source was modified or merged. Review snapshots and isolated dependencies are under `.review-branches/`.

| Member | Commit | Tests | Verdict |
| --- | --- | --- | --- |
| M2 | 4e401ac | 117 passed | Baseline implementation works under tests; module incomplete |
| M3 | f9a3553 | 6 passed | Mock RCA works; real integration incomplete |
| M4 | 30ee942 | 54 passed | Foundation works under tests; public workflow and integration blocked |
| M5 | 83e0a08 | No implementation suite | Same commit as main; no member implementation published on this branch |
| M6 | 412e08f | 23 passed, 1 skipped | Substantive core implementation; integration and live persistence unverified |
| integration/m3-m4 | 469114c | 48 passed, 6 failed | Partial wiring; not ready for acceptance |

Tests used bundled Python 3.12 with isolated current dependencies. Plugin autoload and pytest cache were disabled to avoid runner shutdown hangs. M3's shared provider package was made importable through PYTHONPATH. Tests are unit/API/mock checks, not live Kubernetes/Jenkins/PostgreSQL or full-system validation. M6's database test skipped because DATABASE_URL was not configured.

## M2 — anomaly and correlation

The detector, feature extraction, shared contract models, synthetic evaluation, and tuning are implemented and tested. The README candidly identifies the detector as a provisional synthetic-data baseline.

- **Incomplete incident responsibility:** `services/anomaly-engine/app/main.py` exposes only health and single-snapshot evaluation. No incident correlation, incident creation, event storage/retrieval, or M1 polling/window consumption is implemented. Defining an Incident model does not produce incidents.
- **M3 route mismatch:** M3 requests `GET /internal/anomalies?incident_id=...`; M2 exposes `POST /internal/anomalies/evaluate` only.
- **Readiness requirements:** health omits version and environment. Real M1 calibration and detection evaluation remain outstanding; no trained model is present, although a validated baseline may be acceptable if agreed.

Acceptance: add correlation and incident handoff, agree event retrieval, connect real M1 telemetry, and validate both demo scenarios with measured detector results.

## M3 — root cause analysis

Deterministic scoring and frozen RCA response work for supplied fixtures. HTTP providers include timeout/error handling, but the running service does not select them.

- **High: real mode is not wired.** `app/main.py` creates `RCAService`; its constructor in `app/service.py` unconditionally creates mock providers. `create_http_providers()` is never selected by this path.
- **High: requested incident identity is ignored.** Mock providers read the same fixtures for every ID. Reproduced `POST /internal/rca/analyze` with `INC-NOT-EXIST`: HTTP 200 returned `incident_id: INC-001`, faulty deployment, confidence 0.8. This must not be accepted as evidence for the requested incident.
- **Provider contract mismatch:** incident provider defaults to M1 port 8001 at `/internal/incidents`, whereas incidents belong to the shared API. M4 exposes `/api/incidents/{incident_id}`, not this route. M2 anomaly retrieval is also absent.
- Health always reports ok and omits environment, without proving evidence availability.

Acceptance: explicit provider mode, real provider wiring, matching routes and incident IDs, missing-data tests, and actual incident evidence from both scenarios.

## M4 — decision and remediation

Decision rules, guardrails, state machine, mock execution, Kubernetes commands, and Jenkins client exist. Tests use REMEDIATION_BACKEND=mock; passing them does not demonstrate cluster remediation.

- **High: approval then execution fails.** `/api/incidents/{id}/approve` moves to EXECUTING without executing. `/internal/remediation/execute` then attempts EXECUTING -> EXECUTING, which is forbidden. Reproduced approval 200 followed by execution 400. Unify approval/execution or persist authorization separately.
- **High: real downstream providers are unimplemented.** RCA, FinOps, and recovery providers raise NotImplementedError in real mode. Mock recovery always returns success. Actual M1 validation requires recovered/slo_restored and action timing; this branch reads a mock success flag.
- **Contract mismatch:** DecisionResult exposes action/reason/confidence/approval_required/scale_option instead of the frozen DecisionProposal fields. RemediationResult lacks ActionResult's action_id, incident_id, status, started_at, and completed_at. Map internal models to public frozen contracts.
- **Failure handling:** the remediation API changes status before execution, catches ValueError without applying FAILED_REMEDIATION/ESCALATED, and bypasses orchestration audit handling. Missing kubectl or request exceptions can leave the incident EXECUTING.
- Incidents and audit records are process-local lists; restart loses them. No Jenkins job definition or live execution proof is supplied on this branch.

Acceptance: repair public workflow, connect actual providers, return frozen contracts, persist incident/action/audit state, handle execution failures, and prove recovery after real actions.

## M5 — incident memory and RAG

Remote branch still equals main at 83e0a08. The incident_memory contract and mock are scaffold files, not an implemented memory/RAG service. Work elsewhere or not pushed cannot be assessed.

Acceptance: publish memory ingestion/storage/retrieval and Copilot implementation with source-backed answers and tests.

## M6 — FinOps

Sizing uses peaks, headroom, resource floors, minimum window length, and conservative outcomes. Cost assumptions and temporary scaling options are explicit. Database schema, generic repository, and experiment export helpers exist.

- **Integration gap:** endpoints accept caller-supplied usage/configuration; no real M1 telemetry/config adapter exists. M1 CPU/memory are ratios relative to limits; M6 explicitly requires percentages relative to requests. Multiplying by 100 alone is insufficient when requests differ from limits.
- **Route mismatch with M4:** M4 expects `/internal/finops/context`; M6 implements POST `/internal/finops/scale-options` with a request body.
- **Evidence gap:** sample_count is optional. A nominal long window with sample_count omitted passes the sample-count safety gate, despite the stated minimum of ten observations.
- **Persistence unverified:** live DB test skipped. Persistence errors are logged and swallowed, and recommendation responses do not disclose that storage failed. Generic DB helpers do not mean the other modules are actually persisting their workflows.

Acceptance: agree route/body, implement request/limit unit conversion and real evidence collection, require sufficient observation count, verify PostgreSQL roundtrip, and define how failed persistence is surfaced.

## Separate integration/m3-m4 branch

This branch adds a real HTTP M4-to-M3 call and a decision-build route, and changes approval to execute the stored proposal. These are useful fixes but do not make the system complete.

- Six existing tests fail: five involve DIAGNOSED -> CORRELATING after build_decision starts the lifecycle again; one approval test now lacks a stored proposal. Some tests need adaptation to the changed entry point, and lifecycle preconditions need an explicit contract.
- **Scaling approval loses replicas:** build_decision stores scale_option, but approve_incident calls execute_approved_action without replicas. SCALE consequently raises 'Replicas must be specified', then escalates.
- M3 still uses mock evidence; M4 FinOps and recovery real modes are still unimplemented. M4-to-M3 HTTP wiring alone is not real evidence integration.

## Overall acceptance decision

None of the other members' branches can currently be signed off as a complete, integrated module. M2/M3/M4/M6 have useful implemented work; M5 has no implementation on its published branch. Resolve the concrete blockers above, then run one real bad-deployment flow and one real traffic-spike flow across telemetry, detection, incident correlation, RCA, approved action, recovery, memory, and cost evidence.
