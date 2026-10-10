# Copilot tools

Gemini can request allowlisted backend tools before answering. The server validates arguments, performs lookups, feeds results back to the model and validates citation IDs. Requests are bounded to three planning rounds, eight tool calls and a two-minute model deadline. No arbitrary URLs, shell commands, Kubernetes commands or model-generated approvals are exposed.

| Example | Tool | Behavior |
|---|---|---|
| Show me all incidents | `list_incidents` | Merges memory and matching shared/mock provider; deduplicates IDs and displays actual rows. |
| Show unresolved/high-severity incidents for payment-service | `list_incidents` | Status/service/severity filters. Unknown status is not treated as unresolved. |
| Show the next page | `list_incidents` | Offset/limit, up to 50 rows; partial coverage and pagination are explicit. |
| Explain INC-DEMO-TRAFFIC | `get_incident` | Retrieves the named ID independently of the UI selection. |
| Find similar traffic incidents | `search_incident_memory` | Structured retrieval in the request's source namespace. |
| Which services are unhealthy? | `get_service_health` | Reads configured live health route; mock mode/missing integration return unavailable. |
| Restart payment-service for INC-001 | `prepare_action_request` | Prepares a review card; does not submit, approve or execute. |

Memory contains only resolved incidents. If shared core is down, archive listings are partial; an empty unresolved result does not establish there are no open incidents. Demo mode contains four resolved synthetic incidents and no live health. Tool outputs preserve provenance.

## System reads and service-only requests

The Copilot can discover access with `get_system_overview` and request `list_services`, `get_telemetry`, `get_deployments`, `get_finops`, `get_logs`, and `get_action_status`. It can combine successive lookups within the existing tool budget. “See everything” means the connected, authorized data APIs; no unrestricted machine or filesystem access is provided.

| Setting | Required private adapter envelope |
|---|---|
| `M5_SERVICES_PATH` | `source=real`, `status`, `services` containing objects with `service` names |
| `M5_TELEMETRY_PATH` | `source=real`, `snapshots` list |
| `M5_DEPLOYMENTS_PATH` | `source=real`, `deployments` list |
| `M5_FINOPS_PATH` | `source=real`, `records` list |
| `M5_LOGS_PATH` | `source=real`, `records` list |
| `M5_ACTION_STATUS_PATH` | Path contains `{request_id}`; response identifies matching `request_id` and `source=real` |

These proposed read envelopes are M5 adapters, not changes to frozen team contracts. Routes are relative to the configured shared API. Metric/deployment/FinOps/log reads send `service` when requested, `limit` (1–25), and optional `cursor`. Returned pagination metadata is preserved; bounded samples do not imply complete coverage. Per-service records must identify the requested service. Timestamps, staleness and total coverage must be exposed by the upstream implementation. Backend credentials are not part of model context.

Mock mode derives a partial service inventory from demo/archive incident records, exposes historical synthetic metrics/deployments/FinOps evidence, and reports logs and M4 status unavailable. The M1 snapshot scaffold is not automatically wired as live telemetry because it serves fixtures.

`prepare_action_request` now accepts an optional incident ID. A service-only request requires a service found in the matching inventory, with `status=ok` for real inventory. It remains a draft until human submission and M4 approval; service-only payload support must be agreed with M4. Actual actions remain RESTART, ROLLBACK, SCALE or ESCALATE requests; executable parameters belong to M4.

Use [the post-merge integration prompt](M5_POST_MERGE_PROMPT.md) after the members' APIs are merged. Before public/team deployment, connect authenticated identity and scope the reads/submission proxy to it; redact sensitive fields in the agreed shared adapter.

## Live health

Set `M5_SERVICE_HEALTH_PATH` to a relative route on `SHARED_NEXUS_API_BASE_URL`. Proposed envelope:

```json
{"status":"ok","source":"real","timestamp":"2026-10-10T10:00:00Z","services":[{"service":"payment-service","status":"unhealthy","metrics":{"latency_p95_ms":820}}]}
```

This adapter protocol is not a frozen team contract. Agree/adapt it with shared core. The current M1 scaffold serves fixtures and must not be presented as live health. Backend credentials are never sent to the model.

## M4 handoff

`M5_ACTION_REQUEST_PATH` must create a **pending approval request**, never approve or execute. Leave it unset until agreed with M4. Payload: incident ID, service, allowlisted action, reason, source, `approval_required=true` and unique `request_id`. Request categories: RESTART, ROLLBACK, SCALE, ESCALATE. M4 owns parameters and executable proposals.

The model prepares a ten-minute draft. A human reviews every field and chooses **Accept** or **Decline** in chat. Accept submits the pending request to M4 for approval; it does not bypass M4 or confirm execution. Decline marks the draft cancelled on the server, sends no upstream request, and permanently blocks its submission. Repeated decline is idempotent. Already submitted, uncertain, or expired drafts cannot be cancelled through this local draft endpoint. The separate submission endpoint requires explicit confirmation, rejects cross-origin browser calls, rechecks real/live mode and integration, and prevents repeat submissions. Timeout means uncertain submission: inspect M4 before retrying. Mock acceptance is disabled; mock drafts can still be declined.

Every model tool has an explicit read/proposal effect classification. Read tools run without a confirmation prompt. The model exposes only proposal preparation for mutations; Accept and Decline are separate human UI endpoints, not model tools. Unclassified tools and unsupported add/delete/access changes are rejected. Any future system-changing capability must use the same review gate with its exact parameters. Typed/model-generated consent is not treated as the human button decision.

M4 must enforce authenticated identity, authorization, idempotency, audit and approval before execution. Identity integration does not exist in this local scaffold; keep the submission proxy private. Request submission is never reported as successful restart/recovery.

## Verification

On 2026-10-10, 48 local tests passed with optional PostgreSQL skipped. Twenty-one tool tests cover listing/filtering/pagination, source isolation, named lookup, partial live API coverage, health provenance, argument allowlists, draft target validation, explicit confirmation/cross-origin checks, single submission and a simulated model/tool/citation loop. They also verify that unavailable health cannot be inferred from resolved incidents and that retrieved rows survive model quota failures. New system tests verify capability discovery, historical mock reads, live adapter provenance/target/bounds/cursor handling, service-only restart drafts, read-only action status matching, and multi-step incident/FinOps retrieval. A chat regression test verifies that “what can I do to solve the incident” still returns the selected incident's cited historical resolution during a quota failure, and that missing records produce no invented resolution. The existing nine-case recorded-answer evaluation passed.

The expanded system-tool browser test received Gemini HTTP 429 before retrieval. A separate disposable preview using a simulated model verified the actual backend tools and UI: partial demo service inventory, a service-only RESTART draft, disabled M4 submission in mock mode, and two citations. That preview made no external model calls and no infrastructure actions. It is UI evidence, not a successful live Gemini or M4 execution test.

Confirmation verification: local tests additionally cover server-side idempotent decline, blocked submission after decline, cross-origin decision rejection, expired/in-flight decision rejection, automatic reads with no drafts, and rejection of an unclassified mutation. The synthetic browser preview displayed Accept and Decline, then clicking Decline displayed “Declined. Nothing was sent to M4 or executed.” and disabled both choices. Accept remained disabled because the preview is mock mode; successful confirmed submission is covered by simulated M4 API tests, not a real restart.

With explicit permission to send only synthetic demo data to Google, live Gemini calls successfully listed all four demo incidents, returned no unresolved demo incidents and explained INC-DEMO-TRAFFIC using retrieved evidence. A health lookup correctly returned unavailable in mock mode; a backend guard prevents generated health claims based on resolved incidents. A restart request produced a review-only draft with submission disabled. Later Google HTTP 429 responses kept retrieved rows and the draft visible with an explicit quota warning. No real operational records were sent and no infrastructure action was executed. Live health and M4 submission still require agreed shared-core routes; these checks do not establish those integrations or PostgreSQL/Docker verification for the new chat changes.

Protocol: [Google function-calling compatibility](https://ai.google.dev/gemini-api/docs/openai#function-calling).
