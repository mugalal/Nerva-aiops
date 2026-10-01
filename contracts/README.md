# Contracts — FROZEN (Day 1 gate)

Copied verbatim from `01_ARCHITECTURE_CONTRACTS_AND_SHARED_PLATFORM.md` §6.
These files are the team's shared language.

## Rule
- Mocks MUST match these shapes exactly (no extra fields).
- Any change requires team-lead + M2 approval and a simultaneous update to
  `contracts/`, `mocks/`, and M2's Pydantic models.
- M2 owns the Pydantic models generated from these files.

## Files
| File | Source contract |
|---|---|
| `telemetry_snapshot.json` | Telemetry Snapshot |
| `deployment_event.json` | Deployment Event |
| `anomaly_event.json` | Anomaly Event |
| `incident.json` | Incident |
| `rca_result.json` | RCA Result |
| `finops_context.json` | FinOps Context |
| `decision_proposal.json` | Decision Proposal |
| `action_result.json` | Action Result |
| `recovery_result.json` | Recovery Result |
| `incident_memory.json` | Incident Memory |
| `finops_recommendation.json` | FinOps Recommendation |

## Resolved mismatch (Day 1)
`mocks/mock_metrics.json` previously contained `service_health` and `labels`,
which are NOT in the Telemetry Snapshot contract. Decision: **removed from the
mock** (contract wins). If M1 needs those fields later, propose a contract
version bump — do not add them to mocks unilaterally. M2 notified via this file.
