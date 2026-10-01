# Kickoff — 30 minutes (Day 1, team lead runs it)

Goal: everyone can state their module's question, inputs, and outputs.
If someone can't explain what they produce, they don't understand their contract yet.

## Agenda
| Min | What |
|---|---|
| 0–5 | Lead: repo URL + branching (own branch → PR to `main`), `contracts/` frozen, `mocks/` complete, demo ownership (M1 service / M4 cluster). Env check results. |
| 5–25 | Each member, 3 min, in their own words (no reading the runbook aloud): 1) my question, 2) I consume…, 3) I produce… |
| 25–30 | Confirm Day-3 mocked end-to-end rehearsal owner (captain rota: Day 1 = Lead+M4) and first PRs. |

## Per-member prompts (expected answers)
- **M1:** "What is happening?" Consume: k8s/service signals + action completions. Produce: `telemetry_snapshot.json` + `recovery_result.json`.
- **M2:** "What is abnormal, and what belongs to one incident?" Consume: telemetry (`mock_metrics.json` until M1 real). Produce: `anomaly_event.json` + `incident.json`.
- **M3:** "Why did it happen?" Consume: anomaly + incident + deployment events. Produce: `rca_result.json` (`mock_rca_response.json` shape).
- **M4:** "Safest action, and how to execute it?" Consume: RCA + FinOps context. Produce: `decision_proposal.json` + `action_result.json`; owns cluster + approval routes.
- **M5:** "What do we learn / how do operators ask?" Consume: resolved incidents + action results. Produce: `incident_memory.json` + Copilot answers.
- **M6:** "How to cut waste safely?" Consume: utilization + current config. Produce: `finops_context.json` + `finops_recommendation.json`.

## Exit criteria
- [ ] Each member stated consume/produce without reading verbatim
- [ ] Everyone knows repo URL, branch name convention (`m1/...`, `m2/...`), PR rule
- [ ] M1 + M4 accepted demo ownership; env gaps assigned with names + dates
