# M5 — Incident Memory, RAG & AIOps Copilot Runbook

**AIOps question:** How does NEXUS learn from previous incidents and expose that operational knowledge to engineers?  
**Timebox:** 14 days

## P0 Ownership

- incident memory schema
- resolved-incident storage
- structured similarity retrieval
- grounded operational Copilot
- RAG architecture
- retrieval/evidence quality
- optional pgvector bonus

## Core Integration

Consumes resolved incident + M2 anomaly features + M3 RCA + M4 action + M1 recovery + M6 cost impact. Produces similar incidents and Copilot answers.

## Secondary / Backup

Backup: M3 for grounding. You are backup for shared UI/Copilot presentation.

---

## Rule

If an upstream dependency is unavailable for >30 minutes, use the canonical mock and keep moving.

Every step below includes an integration target. Your module is not done until another real NEXUS module consumes it.

---

# Shared Platform Contribution — AI-Assisted

You own the minimal NEXUS user-interface shell because your Copilot/memory work is the most user-facing AIOps capability.

Shared UI responsibilities:

- minimal navigation/layout,
- Overview page,
- Incident detail page shell,
- Incident timeline/history,
- Copilot panel,
- reusable evidence/action/recovery display components.

Do **not** build a fancy frontend.

Required views:
```text
Overview
Incident Center
FinOps summary
Copilot
```

The UI consumes shared APIs; it must not contain duplicated RCA/decision logic.

Create:
```text
ui/
  overview/
  incident/
  copilot/
  components/
```

Integration responsibilities:
- M3 RCA evidence is rendered,
- M4 recommendation + approve/reject is rendered,
- M1 recovery state is rendered,
- M6 FinOps summary is rendered,
- M5 memory/similar incident is rendered.

Done when:
- the full incident story can be presented from one simple UI,
- the UI shows real API data and explicit degraded/error states,
- no P0 demo step requires opening raw JSON manually.


### Shared Step A — Generate minimal UI shell
Create only:
```text
Overview
Incident Center
FinOps Summary
Copilot
```
Use mock data first.

**Test:** UI can render canonical mock incident/RCA/decision/recovery/FinOps data.

# Day 1 — Memory Contract

Define memory record:
```text
incident type
service
anomaly features
evidence
root cause
action
action result
recovery result
MTTD
MTTR
cost/SLO impact
tags
```

Build skeleton:
```text
GET  /health
POST /internal/memory/store
POST /internal/memory/search
POST /internal/copilot/query
```

# Day 2 — Mock Memory

Store/query mock incidents.

P0 similarity:
```text
same incident type
same service
overlapping tags/features
```

No embeddings required yet.

# Day 3 — Mock Copilot

Supported P0 questions:
```text
What happened?
What changed before the incident?
What is the root cause?
Have we seen this before?
What action was taken?
Did it work?
```

Answers must cite NEXUS record fields, not general knowledge.

# Day 4 — Retrieval Quality

Create at least:
- one bad-deploy incident
- one traffic-spike incident
- one irrelevant incident

Test that correct similar record ranks first using structured matching.

### Shared Step B — Replace UI mocks progressively
Replace:
```text
mock RCA → M3 real RCA
mock decision → M4 real decision
mock recovery → M1 real recovery
mock FinOps → M6 real FinOps
```
Do not rewrite display logic when providers become real.

# Day 5 — Integrate Real Incident/RCA

Consume M2/M3 real fields through shared core.

# Day 6 — Action/Recovery Context

Add M4 ActionResult + M1 RecoveryResult.

# Day 7 — Flagship Memory

After successful bad-deploy run:
- store real incident
- retrieve it by type/service/tags

# Day 8 — Traffic Memory

Store and retrieve traffic incident separately.

Ensure retrieval does not confuse causes.

# Day 9 — Grounded Copilot

Connect to real NEXUS records.

For every answer include:
```text
incident/source record
evidence used
action/outcome if relevant
```

If data absent:
> NEXUS does not have enough recorded evidence.

# Day 10 — P0 Gate

Shared UI must show:
- one similar incident
- one Copilot answer based on real stored data

# Day 11 — Evaluation

Test:
```text
question
expected source
retrieved source
answer grounded?
unsupported claim?
```

# Day 12 — Hardening

Fallback:
- no similar incident
- DB unavailable
- LLM unavailable
- incomplete incident

Structured retrieval must still work without LLM.

### Shared Step C — Final UI demo path
One screen sequence must support:
```text
healthy overview
→ incident
→ RCA evidence
→ decision/approve
→ execution
→ validation
→ memory
→ FinOps
```

# Day 13 — Rehearsal

Explain:
- incident becomes reusable knowledge
- retrieval before generation
- why structured P0 is safer
- what pgvector would add

# Day 14 — Final Verification

P0 done when:
- resolved incidents stored
- both scenario types retrievable
- Copilot answers real operational questions
- no fabricated incident facts
- shared UI consumes real output

# Bonus

- pgvector embeddings
- semantic similarity
- richer RAG
- incident summarization
- follow-up conversational context
