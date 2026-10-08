# M5 integration handoff

## What is implemented

M5 is an independent FastAPI service on port **8005**. It stores resolved incidents, retrieves similar incidents, answers six operational questions with citations, and hosts the shared UI. It does not perform RCA, select remediation, execute Kubernetes commands, validate recovery, or calculate FinOps values. The shared frozen contracts and mocks remain unchanged.

## Producer → M5 memory

The shared-core/incident-lifecycle owner should POST a resolved snapshot to:

```text
POST http://m5-memory:8005/internal/memory/store
Content-Type: application/json
```

Use the actual deployed service hostname; `m5-memory` above is illustrative, not a new team convention. Local URL is `http://localhost:8005`.

Envelope:

| Field | Required | Producer responsibility |
|---|---|---|
| `memory` | yes | Exact `contracts/incident_memory.json` fields; true known outcome booleans |
| `source` | yes | `real` for operational evidence; `mock` for fixtures/synthetic demo records |
| `resolved` | yes | Explicit `true`; lifecycle owner decides closure |
| `context.incident` | optional | M2/shared core Incident, status `RESOLVED` |
| `context.anomaly` | optional | M2 AnomalyEvent, belonging to this incident and service |
| `context.rca` | optional | M3 RCAResult, matching incident and root cause |
| `context.decision` | optional | M4 DecisionProposal |
| `context.action_result` | optional | M4 terminal ActionResult, matching action/outcome |
| `context.recovery_result` | optional | M1 canonical RecoveryResult, matching outcome |
| `context.finops_context` | optional | M6 FinOpsContext, same service |
| `context.deployment_event` | optional | DeploymentEvent, same service; needed for the 'what changed' answer |
| `context.mttd_seconds`, `context.mttr_seconds` | optional | Nonnegative measured timings from experiment/lifecycle owner |
| `context.cost_impact`, `context.slo_impact` | optional | Producer-owned objects; financial estimates must be labeled as estimates |

Detailed context preserves original provider records for field citations. Supplied canonical records must retain their frozen top-level fields/types. Unknown outcome must never be converted to `false` just to satisfy the frozen memory contract; wait for an explicit known outcome. Optional context may be omitted when unavailable.

Success response:

```json
{"status":"stored","record":{"memory":{},"context":{},"source":"real","resolved":true,"stored_at":"UTC timestamp"}}
```

The empty objects here illustrate placement only; real responses include the stored values. Identical retries return `already_stored`. Later delivery of a previously missing optional context item returns `enriched`. Existing non-null context items and all canonical memory fields are protected: changed facts return **409**, never silently overwrite history. Omitted/null context on retry never erases stored evidence. Concurrent enrichment can return 409 and should be retried. Validation failures return **422**, unavailable storage **503**. Retry temporary 503 failures with bounded backoff; investigate permanent 422/409 mismatches. M5 cannot durably queue upstream events while its database is down; the producer must retain/retry them.

## M5 → consumers

### Similar incidents

```text
POST /internal/memory/search
```

```json
{"source":"real","incident_type":"faulty_deployment","service":"payment-service","tags":["deployment","latency"],"features":["latency_p95_ms"],"exclude_incident_id":"INC-CURRENT","limit":5}
```

Returns `status`, `source`, `results` and `total_matches`. Each result contains `record`, numeric `score`, and `reasons`. Explicit type/service filter out other causes/services. Rank scores are not confidence values. No match returns `results: []`. The shared UI consumes this endpoint; other consumers may use it for operational context, never as permission to execute a previous action automatically.

### Stored record

```text
GET /internal/memory/{incident_id}?source=real
```

Returns `{"record": ...}` or 404. Default source is real.

### Copilot

```text
POST /internal/copilot/query
```

```json
{"source":"real","incident_id":"INC-123","question":"Did it work?"}
```

Returns `status`, `source`, `mode`, `answer`, `citations`, `similar_incidents`, `supported_questions`. Citations identify `incident_id`, `source`, `field`, and exact recorded `value`. Supported questions: what happened, what changed before, root cause, previous similar incidents, action taken, and whether it worked.

Without `incident_id`, pass `search` using the search envelope to retrieve a top matching source before answer rendering. No arbitrary default incident is chosen. Status can be `ok`, `insufficient_evidence`, `no_similar_incident`, or `unsupported_question`. No LLM dependency exists in P0; outage of an optional future LLM cannot stop structured retrieval.

## Shared-core → UI adapter

**These HTTP routes are M5 adapter defaults, not frozen or verified team contracts.** The repository has frozen record examples but no shared-core route specification. The shared-core owner can expose this read protocol or adapt `services/incident-memory/app/providers.py` to their existing protocol:

| Setting | Default | Expected payload |
|---|---|---|
| `SHARED_NEXUS_API_BASE_URL` | `http://localhost:8000` | Base URL |
| `M5_INCIDENTS_PATH` | `/api/incidents` | `{ "status": "ok", "incidents": [Incident, ...] }` |
| `M5_INCIDENT_DETAIL_PATH` | `/api/incidents/{incident_id}` | Bundle below |
| `M5_APPROVAL_PATH` | unset | POST `{ "decision": "approve" or "reject" }`; agreed M4 response |
| `M5_SHARED_API_TOKEN` | unset | Optional trusted backend Bearer token, never exposed to the browser |
| `M5_UI_MODE` | `live` | `mock` must be explicitly selected |

Detail bundle fields: `incident`, `anomaly`, `rca`, `decision`, `action_result`, `recovery_result`, `finops_context`, `deployment_event`, `memory`, optional timings and impacts. Their inner shapes are canonical records. The UI renders absent data as not recorded; it must not infer evidence or fabricate recovery. The shared-core owner supplies the memory projection. The UI can archive it only once incident status is `RESOLVED`.

Approval buttons are disabled in mock mode, when a route is not configured, without a decision, or once an action result exists. M4 remains the authority on approval state, identity, guardrails, execution, and idempotency. Before real integration, verify the M4 route/body includes the required proposal/action identifiers and authenticated human identity; adapt the proxy if the agreed contract needs more than the default decision payload. M5 does not execute infrastructure actions.

Live provider failures return explicit 503/degraded responses and **never substitute mock data**. The UI has one selected incident across Incident Center, FinOps, and Copilot; records are fetched again on Refresh.

## Integration acceptance sequence

1. Start PostgreSQL and M5; verify `/health` storage readiness.
2. Shared core exposes/configures list and detail routes; live UI renders real RCA/decision/action/recovery/FinOps fields.
3. Verify M4 human approval protocol and authentication, then enable its route. Complete a real bad-deployment scenario through the other owners.
4. Lifecycle owner posts the closed incident snapshot with real provenance, action result, and validated recovery.
5. Search it by type/service/tags; ask the supported questions and verify each citation against upstream records.
6. Complete the real traffic-spike scenario; store/retrieve it without matching a faulty-deployment cause.
7. Demonstrate a genuinely previous similar operational incident in the UI and a grounded Copilot answer.
8. Interrupt DB/shared API connectivity; verify explicit unavailable/degraded output, no fabricated data and successful producer retry after recovery.

Until these gates pass, M5 is implemented/tested independently; the complete NEXUS real integration gate remains pending. Do not claim synthetic fixtures, test transport responses, or an estimated financial figure as real operational evidence.
