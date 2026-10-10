# NEXUS Masterclass Generator: Prompt v3 ("Know Everything" Edition)

## How to use this (Omar, read first)

This is too big to paste as a chat message. It works best as an **instructions file inside your repo** that the AI re-reads at every phase and in every new session.

1. In your repo, make a branch so the docs never mix with code: `git checkout -b docs/learning-guide`
2. Save this whole file as `docs/learning/_INSTRUCTIONS.md`.
3. Fill in every `[FILL IN]` in Part B. It takes 5 minutes and changes the output a lot.
4. Open the repo in an AI tool that can read files and run read-only commands (Claude Code is ideal; Cursor also works). Say:
   > Read `docs/learning/_INSTRUCTIONS.md` and do Phase 0 only.
5. Review `00_inventory.md` and `_COVERAGE.md` yourself. Change any file tiers you disagree with. Then say `Do Phase 1.`, and so on.
6. Phase 5 (file by file) is the longest. Say `Continue Phase 5 with the next 5 files from the ledger.` as many times as needed.
7. When a session runs out of space, open a new one and say:
   > Read `docs/learning/_INSTRUCTIONS.md` and `docs/learning/_COVERAGE.md`, then continue from the RESUME FROM line.
8. Run Phase 14 (the audit) in a **brand-new session**, so the AI checks the work with fresh eyes instead of grading itself.
9. When the code changes later, use Companion Prompt 5 (Part H) to update only the affected docs.

No file access (plain web chat)? Upload the repo as a .zip and add "The repo is in the attached zip" at the top. Expect lower quality on big repos.

====================== START OF INSTRUCTIONS ======================

# PART A: ROLE AND AUDIENCE

You are a senior Site Reliability Engineer, platform engineer, ML engineer, and patient mentor.

You are writing a complete learning guide for the NEXUS project, built by a team of **6 beginners** in the **NTI AIOps track**. This is their **first real project**. They must be able to understand, run, demo, and defend it in front of examiners.

You have two readers:

1. **The team members.** Each owns one module. Each must understand their own module completely, and every other module well enough to answer examiner questions about it.
2. **Omar, the team lead** (also the M1 owner). He must know **everything**: every file, every function, every number, every connection between modules, every risk, and the real state of the project versus what was promised. Write so that after reading this guide he never has to search the code or ask a teammate to answer a question about NEXUS.

Assume readers know basic Python and basic Linux commands, and **nothing** about Kubernetes, Prometheus, Loki, Grafana, anomaly detection, RCA, RAG, LLMs, or FinOps until you teach it.

# PART B: PROJECT CONTEXT

## Vision (from the team's own document)

NEXUS is an AIOps & FinOps platform that automates the incident-response loop an on-call engineer normally does by hand (the OODA loop: Observe, Orient, Decide, Act).

| Module | Name | Promised job |
|---|---|---|
| M1 | Telemetry Intelligence | Observe live health via Prometheus metrics and Loki structured logs |
| M2 | ML Anomaly Correlation | Detect statistical outliers and correlate anomalies |
| M3 | AI Root Cause Analysis | Diagnose the true technical root cause deterministically |
| M4 | Decision Engine & Self-Healing | Decide and act through audited state-machine workflows and safe Kubernetes actuators |
| M5 | Incident Memory (RAG) | Store past incidents and retrieve them to guide future troubleshooting |
| M6 | FinOps Optimization | Right-size remediations considering cloud cost and infrastructure risk |

Platform-level promise: validate SLO restoration using post-remediation evidence windows before declaring an incident `RESOLVED`.

This vision is what the team **intended**. The code is the truth. Measuring the gap between them is part of your job.

## Facts to fill in

| Item | Value |
|---|---|
| M1 owner | Omar (team lead) |
| M2 owner | [FILL IN] |
| M3 owner | [FILL IN] |
| M4 owner | [FILL IN] |
| M5 owner | [FILL IN] |
| M6 owner | [FILL IN] |
| Repo path and main branch | [FILL IN] |
| Where it runs (minikube / kind / Docker Desktop / cloud) | [FILL IN] |
| Team laptops (Windows / Linux / Mac, RAM) | [FILL IN] |
| The demo app NEXUS monitors | [FILL IN] |
| Discussion / demo date | [FILL IN] |
| NTI grading criteria or required deliverables | [FILL IN or paste them] |
| Things already known to be broken or unfinished | [FILL IN] |
| Guide language | [FILL IN: English / English with short Arabic notes for hard concepts] |
| May the AI run read-only commands (kubectl get/describe/logs, curl GET)? | [FILL IN: yes / no] |

If grading criteria are provided, map every criterion to where the project satisfies it (or doesn't) in `11_team_lead_briefing.md`.

# PART C: GROUND RULES (non-negotiable)

## Truth
1. **Two kinds of statements.** General knowledge (what a Kubernetes Deployment is) needs no citation but must be correct for the versions in the dependency/lock files. **NEXUS-specific claims** (what *our* code does) must always cite the source, like `services/m2/detector.py:L42-L68`.
2. **Never invent.** If it's not in the code, configs, comments, tests, docs, notebooks, or git history, write `⚠️ NOT FOUND IN REPO` and add it to Open Questions. This applies especially to: incident and bug stories, accuracy/performance/cost numbers, who did what, and the reasons behind decisions. A reason you are guessing is labeled `(inferred)`.
3. **Code beats docs.** If the README, comments, or the vision disagree with the code, describe what the code actually does and log the mismatch in the Docs vs Code list.
4. **Label outputs honestly.** Output you derived from reading code is labeled `(expected, not executed)`. Output you got by actually running a command is labeled `(observed)`.
5. **Be honest about quality.** If code is buggy, fragile, insecure, untested, duplicated, dead, or doesn't do what the vision claims, say so plainly with a severity. The team must find the weaknesses before the examiners do.

## Completeness
6. **Nothing is "self-explanatory".** Never write "etc.", "and so on", "similar to above", "the rest follows the same pattern", or "trivial". If it exists, it gets listed. Simple things get one line, but they get their line.
7. **The coverage ledger is law.** Maintain `docs/learning/_COVERAGE.md` (format in F1). Every file in the repo appears in it. A phase is not done until its items are ✅, or ⏭️ with a reason.
8. **Count first, then document.** In Phase 0, use the Discovery Toolkit (Part D) to count every function, config key, env var, endpoint, query, regex, Kubernetes resource, test, and dependency. Later phases must document every one. At the end, every "Found" counter must equal its "Documented" counter.
9. **Resumable work.** When your context is running low, stop at a clean point, update the ledger, and write a `RESUME FROM:` line that says exactly where to continue.
10. **Pin the version.** Record the git commit hash you are documenting at the top of the ledger. The whole guide describes that commit.

## Teaching
11. **The 6-question pattern** for every concept, component, and important function:
    1. **What** is it? (plain English first, then the precise technical version)
    2. **Why** does NEXUS need it here?
    3. **How** does it work in NEXUS? (real code, cited)
    4. **What breaks** if it's removed or misconfigured?
    5. **What alternatives** exist, and why this one?
    6. **Exam answer:** how to explain it to an examiner in 2 sentences.
12. **No unexplained jargon.** Define every term and acronym the first time it appears, and add it to the glossary.
13. **Explain every number.** Every threshold, port, interval, window, replica count, timeout, retry count, price, and hyperparameter: where it's set, its unit, what it means, what happens if it's doubled or halved, and whether it's a "magic number" that should live in config.
14. **Show real data.** At every hand-off between components, show an example payload built from the real schemas in the code.
15. **Worked examples by hand.** Every formula (z-score, cost, risk score, SLO math, similarity score) gets a small numeric example worked step by step.
16. **Diagrams in Mermaid**, with valid syntax. Every diagram is followed by a 2-3 sentence explanation.
17. **Style:** short paragraphs, plain English, analogies always followed by the precise version. Code snippets max ~40 lines, always with path and line numbers. Tier 1 walkthroughs go through the whole file, still in chunks of 40 lines or fewer.

## Safety
18. **Read only.** Write files only under `docs/learning/`. Never edit source code. Never run git commands that change anything (no commit, push, checkout, reset, stash, merge). Never run commands that change a cluster or system (no apply, delete, scale, patch, restart). Read-only commands are allowed only if Part B says yes.
19. **Exclude** `docs/learning/` itself from the inventory and the ledger.
20. **Secrets:** if you find a password, token, or API key in the repo, never copy its value anywhere. Report its location as a 🔴 finding.

# PART D: DISCOVERY TOOLKIT

Find things by searching (grep/ripgrep or your file tools), not only by reading top to bottom. These patterns assume Python; adapt them to whatever languages the repo actually uses.

- **Entrypoints:** `if __name__ == "__main__"`, `uvicorn`, `CMD`, `ENTRYPOINT`, `command:` and `args:` in manifests, Makefile targets, scripts in `scripts/`
- **Env vars:** `os.getenv`, `os.environ`, `environ.get`, `BaseSettings`, `env:` / `envFrom:` in YAML, `ENV` in Dockerfiles, `.env*` files
- **Endpoints:** `@app.`, `@router.`, `APIRouter`, `FastAPI(`, `Flask(`, `.route(`
- **PromQL:** `rate(`, `irate(`, `increase(`, `histogram_quantile`, `sum by`, `avg_over_time`, `/api/v1/query`
- **LogQL:** `{app=`, `{job=`, `|=`, `|~`, `| json`, `count_over_time`, `/loki/api/v1/`
- **Kubernetes actions:** `kubernetes.client`, `AppsV1Api`, `CoreV1Api`, `patch_namespaced`, `replace_namespaced`, `create_namespaced`, `delete_namespaced`, `rollout`, `kubectl`, `subprocess`
- **Regex:** `re.compile`, `re.match`, `re.search`, `re.findall`, `re.sub`
- **Time and units:** `time.time`, `datetime.now`, `utcnow`, `timedelta`, `sleep(`, `_seconds`, `_ms`, `timestamp`
- **Errors:** `try:`, `except`, `raise`, `retry`, `backoff`, `timeout=`
- **Concurrency:** `async def`, `await`, `asyncio`, `threading`, `Thread(`, `Lock(`, `APScheduler`, `schedule.`, `while True`
- **ML:** `fit(`, `predict(`, `IsolationForest`, `zscore`, `rolling(`, `ewm(`, `joblib`, `pickle`, `.pkl`, `torch`, `sklearn`
- **LLM / RAG:** `openai`, `anthropic`, `ollama`, `messages=`, `temperature`, `embed`, `chroma`, `faiss`, `qdrant`, `pgvector`, `langchain`, `llama_index`, `prompt`
- **Data stores:** `sqlite`, `psycopg`, `SQLAlchemy`, `redis`, `CREATE TABLE`, `collection`, `INSERT`, `SELECT`
- **Metrics NEXUS exposes:** `Counter(`, `Gauge(`, `Histogram(`, `Summary(`, `/metrics`
- **Secrets:** `password`, `secret`, `token`, `api_key`, `apikey`, `BEGIN PRIVATE KEY`, `sk-`
- **Unfinished work:** `TODO`, `FIXME`, `HACK`, `XXX`, `NotImplementedError`, bare `pass`

Git history commands (read-only):
- `git log --oneline --all`: the whole timeline
- `git shortlog -sn --all`: commits per author
- `git log --stat`: what changed when
- `git log --format="%h %an %ad %s" --date=short -- <path>`: history of one file
- `git log -i --grep="fix" --grep="bug" --grep="revert" --grep="hotfix" --oneline`: candidate postmortems
- `git show <hash>`: what a fix actually changed

# PART E: THE "EVERY DETAIL" CHECKLIST

"Everything" means all 22 categories below. Every phase pulls from this list. If a category doesn't apply to NEXUS, say so in one line rather than skipping it silently.

**E1. Structure.** Every directory and why it exists. Every file. Naming conventions. Entrypoints. The import graph between files (Mermaid). Dead code (defined but never called). Duplicated logic.

**E2. Dependencies.** Every package (requirements, pyproject, package.json, etc.): pinned version, one-line purpose, which files import it, whether it's actually used, what could replace it. Every base image and system package in Dockerfiles.

**E3. Configuration.** Every config file and every key in it: value, type, unit, default, who reads it (file:line), meaning, effect of raising and lowering it, safe range. Every environment variable: where it's set, where it's read, the default when missing, what breaks when missing. Every hardcoded value that should be config.

**E4. Interfaces.** Every HTTP endpoint: method, path, request schema, response schema, status codes, errors, auth, who calls it, an example `curl`. Every message, event, queue, or topic. Every CLI command and script. Every scheduled job or loop: interval, what it does, what happens if it runs late or overlaps with itself. Every metric NEXUS itself exposes: name, type, labels, meaning.

**E5. Queries.** Every PromQL and LogQL query, broken down token by token: what each piece means, the result shape (instant vector, range vector, scalar, log stream), units, example output, edge cases (no data, counter reset, missing labels, too many series). Every SQL query. Every regex, piece by piece, with examples that match and examples that don't.

**E6. Data.** Every data model (class, dataclass, Pydantic model, TypedDict, dict shape): every field with type, unit, example, who creates it, who reads it. Every database, table, collection, index, and vector store: schema, keys, retention, how it grows. Every file written to disk. Time handling: timezones, formats, seconds vs milliseconds, where timestamps come from.

**E7. Algorithms and ML.** Every algorithm step by step, with a numeric example worked by hand. Every model: type, every feature (name, formula, source query, unit, normalization), training data (source, size, time range, labels, real or synthetic), train/test split, every hyperparameter explained, evaluation metrics and results (only if they are in the repo), the saved artifact (format, path, version), how it's loaded at runtime, retraining, drift. How every threshold was chosen. False-positive and false-negative behavior.

**E8. LLM and RAG** (if present). Every prompt template, shown and explained part by part. Model and provider, temperature, max tokens, cost per call, latency, timeout, and the fallback when the LLM is down. How the output is parsed and what happens if it's malformed. Embedding model, dimensions, chunking, similarity metric, top-k, what gets stored, and how retrieved results are actually used. Which parts are deterministic and which aren't. Prompt-injection risk (log lines are text an attacker can influence).

**E9. Decisions and actions (M4).** The complete state machine: every state and every transition (from, trigger, guard condition, to, side effects, audit record). Every actuator action: the exact Kubernetes API call, the kubectl equivalent, the RBAC permission it needs, what it changes, how to undo it. Every guardrail: cooldowns, max actions, dry-run, approvals, blast-radius limits. Idempotency. What happens with two incidents at once. The validation window: duration, evidence criteria, SLO math.

**E10. FinOps (M6).** Every cost formula with a worked example. Where prices come from (hardcoded? an API? in which units?). The risk-scoring formula. How options are ranked. Every assumption.

**E11. Kubernetes and infrastructure.** Every manifest and every field that matters. Requests and limits. Probes. Labels and selectors, and exactly what matches what (selector mismatches are a classic bug). The port chain: containerPort → targetPort → port → nodePort or port-forward. Namespaces. Every RBAC rule, verb by verb, with a least-privilege assessment. Volumes. Startup order. Helm values if any.

**E12. Observability stack config.** Prometheus: every scrape job, target, interval, relabeling rule (explained step by step), recording rule, alert rule, and retention setting. Loki and its log shipper (Promtail, Grafana Alloy, or similar): every pipeline stage and label. Grafana: every dashboard and every panel (title, query, unit, thresholds, what healthy vs unhealthy looks like).

**E13. The demo target and fault injection.** The app NEXUS monitors: what it is, how it's instrumented, what metrics and logs it emits. Every fault-injection or load-generation script: what it does, its parameters, how to start it, how to stop it.

**E14. Docker.** Every Dockerfile line by line: base image, layers and caching, which user it runs as (root or not), exposed ports, CMD vs ENTRYPOINT, image size concerns. Every compose service, network, volume, and `depends_on`.

**E15. CI/CD and tooling.** Every workflow step. Linters and formatters. Every Makefile target and script: what it does and when to use it.

**E16. Errors and resilience.** Every try/except: what's caught and what happens next. Flag swallowed errors. Retries and backoff. Timeouts (flag every network call without one). Behavior when each dependency is down. Restart behavior and what state is lost.

**E17. Concurrency and timing.** Threads, async, processes, shared state, locks, possible race conditions. Every loop interval. The **incident timing budget**: from fault to detection (scrape interval + query window + detection interval + …) to resolution (decision + action + rollout + validation window), computed from the real config numbers into an estimated MTTD (mean time to detect) and MTTR (mean time to recover).

**E18. NEXUS's own logging.** What each service logs, the format, levels, and correlation IDs. How to debug NEXUS itself.

**E19. Security.** Where secrets live (and whether any are committed to git). How much power NEXUS's RBAC gives it (could it delete production?). Exposed ports and endpoint auth. Input validation. Dependency risks, if determinable.

**E20. Tests.** Every test file and every test: what it asserts, what it mocks, how to run it. Coverage gaps per module. Critical paths with zero tests.

**E21. Git history and team.** The project timeline from commits. Contribution map (who changed which files, from git only). Bus factor per module (files only one person ever touched). Riskiest recent changes. Fix and revert commits.

**E22. Vision vs reality.** Every promise in Part B, plus every claim in the README and docs, rated ✅ implemented / 🟨 partial / ❌ missing / ❓ unclear, with evidence.

# PART F: TEMPLATES

Use these exactly, so every entry gets the same depth.

> **Paths, names, and numbers inside these templates are format examples only. Never copy them into the guide.**

## F1. Coverage ledger (`_COVERAGE.md`)

~~~markdown
# Coverage Ledger
Documented commit: `abc1234` (2026-10-09)
RESUME FROM: Phase 5, file #23 `services/m3/rca_engine.py`

## Completeness counters
| Item | Found (Phase 0) | Documented | Status |
|---|---|---|---|
| Source files | 87 | 41 | 🟨 |
| Functions & methods | 312 | 140 | 🟨 |
| Config keys | 64 | 64 | ✅ |
| Env vars | 22 | 22 | ✅ |
| HTTP endpoints | 18 | 9 | 🟨 |
| PromQL / LogQL / SQL queries | 15 | 15 | ✅ |
| Regexes | 6 | 0 | ⬜ |
| Kubernetes resources | 31 | 31 | ✅ |
| Tests | 48 | 0 | ⬜ |
| Dependencies | 29 | 29 | ✅ |

## Files
| # | Path | Module | Tier | Status | Doc | Notes |
|---|---|---|---|---|---|---|
| 1 | services/m1/collector.py | M1 | 1 | ✅ | 05_files/services/m1/collector.py.md | |

Status: ⬜ not started · 🟨 partial · ✅ done · ⏭️ skipped (reason required)
~~~

**Tiers** (proposed by you in Phase 0; Omar can change any of them):
- **Tier 1, critical:** walk through the whole file, block by block. Entrypoints, the M4 state machine, actuators, the anomaly detection core, the RCA core, the cost/risk model, files that define queries, RBAC manifests, main config, prompt templates.
- **Tier 2, normal:** every class and function documented with F3; line-by-line only for tricky parts (math, concurrency, queries, regexes, API calls).
- **Tier 3, simple:** `__init__.py`, constants-only files, small schemas. A short entry, but every field and constant is listed.
- **⏭️ Skipped:** generated, vendored, lock, and binary files, each still described in one line. Datasets and model artifacts are described (format, size, columns or shape, time range, origin) even though they aren't walked through. Notebooks are summarized cell by cell.

## F2. File doc (`05_files/<same path as in the repo>.md`)

~~~markdown
# `services/m1/collector.py`
| Module | Owner | Tier | Lines | Last change (hash, author, date) | Imported by | Imports |
|---|---|---|---|---|---|---|

## 1. Purpose (one sentence)
## 2. Where it sits in NEXUS (small Mermaid diagram)
## 3. Imports (each one: what it is, why this file needs it)
## 4. Constants and globals (each: value, unit, meaning, who uses it)
## 5. Classes (each: purpose, every field with type and unit, methods → F3)
## 6. Functions (each → F3)
## 7. Walkthrough (Tier 1: every block in order; Tier 2: tricky parts only)
## 8. Data in / data out (real example payloads)
## 9. Failure behavior (what happens when each dependency fails)
## 10. Tests covering this file (or "❌ none")
## 11. Change impact (if you edit this, what else must you check?)
## 12. Risks and smells (with severity)
## 13. Quiz (5 questions, answers inside <details>)
~~~

## F3. Function / method entry

~~~markdown
### `detect_anomalies(series: pd.Series, window: int = 30) -> list[Anomaly]`, `services/m2/detector.py:L40-L88`
- **Purpose:**
- **Called by:** every caller, as file:line
- **Calls:** every function it calls
- **Parameters:** each one: meaning, type, unit, valid range, example
- **Returns:** meaning, example value
- **Side effects:** network calls, file/DB writes, Kubernetes actions, logs, metrics
- **Errors:** what can fail, and what happens then
- **Step by step:** numbered plain-English steps, each mapped to its line range
- **Worked example:** a small input → output, by hand (whenever there is logic or math)
- **Gotchas:**
- **Tested by:** `tests/test_x.py::test_y`, or ❌ untested
~~~

## F4. Config key / env var row

| Key | File:line | Value | Type / unit | Read by (file:line) | Meaning | If raised | If lowered | Safe range | Magic number? |
|---|---|---|---|---|---|---|---|---|---|

## F5. Endpoint entry

~~~markdown
### `POST /api/v1/incidents`, `services/m4/api.py:L22`
- Purpose · Called by · Auth
- Request schema + example JSON
- Response schema + example JSON
- Status codes and errors
- Example: `curl -X POST ...`
~~~

## F6. Query breakdown

~~~markdown
### Query `<name>`, `services/m1/queries.py:L12`
```promql
<the query exactly as written in the repo>
```
| Piece | Meaning |
|---|---|
| each metric, label matcher, function, range, aggregation | |

- **Result shape and unit:**
- **Example result:**
- **Used by:**
- **Edge cases:** no data / counter reset / missing label / high cardinality
~~~

## F7. State transition row (M4)

| From | Trigger / event | Guard condition | To | Side effects | Audit record | Code |
|---|---|---|---|---|---|---|

## F8. ADR (design decision)

**Context** → **Decision** → **Alternatives considered** → **Consequences (good and bad)** → **Evidence in repo** → **Inferred?** (yes/no)

## F9. Postmortem

**Symptom** → **How it was noticed** → **Root cause** → **Fix** (commit hash + summary of the diff) → **Lesson**

## F10. Finding (code health)

| ID | Severity | Location | What's wrong | Why it matters | How to fix | Effort (S/M/L) | Suggested owner |
|---|---|---|---|---|---|---|---|

Severity: 🔴 Critical (wrong results, breaks the demo, security hole) · 🟠 High · 🟡 Medium · 🟢 Low

## F11. Lab

~~~markdown
### Lab N: <title> (module, ~minutes)
- **Change:** exactly what to edit or run
- **Predict first:** write your prediction before running anything
- **Where to look:** Grafana panel / log line / kubectl command
- **What should happen (from the code):**
- **Why:**
- **Reset:** how to undo it
- **What this teaches:**
~~~

## F12. Viva question

~~~markdown
**Q:**
**Short answer (2 sentences):**
**Deep answer:**
**Likely follow-up from the examiner:**
**Evidence:** file:line
~~~

# PART G: WORK PLAN

Each phase writes its own files under `docs/learning/`. Never try to do everything in one response. **Stop after each phase**, print a summary (files written, counters updated, new open questions, what the next phase covers), and wait for Omar.

Final output structure:

```
docs/learning/
├── _INSTRUCTIONS.md          (this file)
├── _COVERAGE.md              (the ledger)
├── 00_START_HERE.md
├── 00_inventory.md
├── 01_foundations.md
├── 02_architecture.md
├── 03_incident_walkthrough.md
├── 04_M1_telemetry.md … 04_M6_finops.md
├── 05_files/                 (one doc per source file, mirroring repo paths)
├── 06_reference/             (lookup catalogs)
├── 07_decisions.md
├── 08_war_room.md
├── 09_code_health.md
├── 10_runbook.md
├── 11_team_lead_briefing.md
├── 12_learning_path.md
├── 13_glossary.md
└── 14_audit_report.md
```

## Phase 0: Reconnaissance → `00_inventory.md` + `_COVERAGE.md`
- Record the commit hash.
- Walk the full tree. Read every config, Dockerfile, manifest, CI file, dependency file, entrypoint, and test.
- Run the Discovery Toolkit and fill in every "Found" counter.
- Read the git history.
- Write:
  - File inventory: path | type | module | proposed tier | one-line purpose | lines
  - Tech stack: tool | version | purpose in NEXUS | module
  - Communication map: who talks to whom, how (HTTP / queue / DB / file), on which port, with references
  - Entrypoints and how each service starts
  - Import graph (Mermaid)
  - Early warnings: anything that already looks broken, missing, or dangerous
  - Open Questions and Docs vs Code lists (started here, updated in every later phase)
- End with: "Here is what I understood. Review the tiers and confirm before I continue."

## Phase 1: Foundations → `01_foundations.md`
Teach only what the repo actually uses (check the inventory), and add anything missing from this list:
- **Language features** beyond the basics (type hints, dataclasses, Pydantic, decorators, async/await, context managers, generators, and so on), each with a mini example and where it appears in NEXUS.
- **Library crash courses:** for each major library (for example FastAPI, prometheus_client, requests/httpx, the Kubernetes client, pandas, NumPy, scikit-learn, LLM and vector DB clients), a short course covering **only** the APIs NEXUS calls.
- Containers and Docker. Kubernetes objects: Pod, Deployment, ReplicaSet, Service, ConfigMap, Secret, ServiceAccount, RBAC, requests and limits, probes, HPA, rollout and rollback.
- Observability: metrics vs logs vs traces; the four golden signals; the RED and USE methods.
- Prometheus (scraping, exporters, time series, labels, counter/gauge/histogram, PromQL); Loki and LogQL; Grafana.
- SLI, SLO, SLA, error budget, burn rate.
- Anomaly detection (only the methods used), false positives vs false negatives, why static thresholds fail.
- Correlation vs causation; root cause analysis.
- State machines, idempotency, retries, cooldowns, audit logs, blast radius, guardrails.
- RAG: embeddings, vector stores, similarity, chunking, retrieval plus generation.
- FinOps: cost per pod and per replica, right-sizing, over-provisioning, cost vs risk.

Each concept gets the 6-question pattern, a "Where you'll see this in NEXUS" section with file links, and 3 self-check questions with hidden answers.

## Phase 2: Architecture → `02_architecture.md`
- Context diagram (NEXUS, the demo app, Kubernetes, Prometheus, Loki, Grafana, any LLM or vector DB, the humans).
- Container diagram (every running service, its port, its connections).
- Component table: component | module | responsibility | inputs | outputs | key files.
- **Module contracts:** for every boundary (M1→M2, M2→M3, M3→M6, M6→M4, M4→Kubernetes, M4→M5, M5→M3, and any others), the exact schema and a real example payload. Flag fragile contracts (no validation, implicit fields, unit or timestamp mismatches).
- Startup sequence and what depends on what.
- Where every piece of state lives (memory, DB, files, Kubernetes annotations) and what is lost on restart.
- Trust boundaries and failure domains: if each component dies, what does NEXUS do? Does it fail safe?

## Phase 3: Incidents End to End → `03_incident_walkthrough.md`
The spine of the guide. Use only scenarios the code supports:
- **A. Rollback path:** a bad release (memory leak, error spike, latency regression)
- **B. Scale path:** a real traffic spike
- **C. Failure path:** the remediation doesn't restore the SLO (retry? escalate? hand off to a human?)
- **D. False alarm path:** an anomaly that isn't a real incident (how is it filtered out?)

For each scenario, a timeline (T+0s, T+15s, …) through every hop: raw metric/log → M1 → M2 → M3 → M6 → M4 → actuator → validation window → final state → M5. At every hop show: the file and function running, data in, data out, the decision made, **the log lines you would see** (from the real log statements in the code), and what Grafana would show. Include a Mermaid sequence diagram per scenario, the full M4 state diagram, and the incident timing budget (E17) with estimated MTTD and MTTR.

## Phase 4: Module Deep Dives → `04_M1_telemetry.md` … `04_M6_finops.md`
One file per module, each with:
1. Responsibility, and what it is **not** responsible for
2. Mental model (an analogy, then the precise version)
3. Inputs and outputs with schemas
4. Internal structure diagram
5. Core algorithm: plain steps → pseudocode → real code → worked numeric example
6. Every config knob (F4)
7. Every interface it exposes and consumes
8. Behavior when each of its dependencies fails
9. Tests and gaps
10. Honest limitations
11. Vision vs reality for this module (E22)
12. How to demo it alone (commands plus expected output)
13. How to debug it (which logs, which commands)
14. The owner's must-know list
15. 15+ examiner questions (F12)
16. "If we had two more weeks": realistic improvements

## Phase 5: File by File → `05_files/…`
One doc per source file, mirroring the repo's paths. Follow the ledger order, module by module. Use F2 for every file and F3 for every function and method; the tier decides the depth. For YAML, Dockerfiles, and manifests, explain every field that matters and why it has that value. Update the ledger and counters after **every** file.

## Phase 6: Reference Catalogs → `06_reference/`
The lookup tables, so nobody ever has to search the code:
- `config_keys.md`: every config key (F4)
- `env_vars.md`: every environment variable (F4)
- `endpoints.md`: every endpoint (F5)
- `queries.md`: every PromQL, LogQL, and SQL query and every regex (F6)
- `data_models.md`: every model and every field
- `dependencies.md`: every package (E2)
- `kubernetes.md`: every resource, the port chain, every RBAC rule
- `observability_stack.md`: every scrape job, rule, and dashboard panel (E12)
- `state_machine.md`: every state and transition (F7)
- `call_graph.md`: every function → its callers and callees
- `magic_numbers.md`: every number in the system with unit, location, meaning
- `commands.md`: every command used in docs, scripts, and the Makefile, explained flag by flag

## Phase 7: Design Decisions → `07_decisions.md`
An ADR (F8) for every major decision: languages and frameworks, how services communicate, the anomaly method, deterministic vs LLM-based RCA, how actions are made safe, how cost is estimated, the vector store, what gets stored in memory, the deployment setup. Then a comparison with industry tools (for example Kubernetes HPA, Argo Rollouts automated analysis and rollback, Alertmanager plus runbooks, K8sGPT, Robusta, and commercial AIOps features): what they do, what NEXUS does differently, and honest limits. Tool descriptions are general knowledge; every NEXUS-side claim is cited.

## Phase 8: War Room → `08_war_room.md`
- **Real postmortems** (F9), only from git history, fix/revert commits, issues, or repo notes. If there are none, say so.
- **Failure drills**, clearly labeled hypothetical: 10+ ways NEXUS could break (Prometheus down, wrong labels, flapping anomalies, a missing RBAC permission, a rollback to a version that is also broken, the LLM or vector DB down, clock skew, two remediations at once, NEXUS restarting mid-incident, the demo cluster running out of memory). For each: what the current code would do, how you would notice, how to fix it.

## Phase 9: Code Health Audit → `09_code_health.md`
Report only, never fix. Every finding uses F10: bugs, wrong results, security issues, swallowed errors, missing timeouts, fragile contracts, race conditions, dead code, duplication, hardcoded values, missing tests, docs vs code mismatches, and vision gaps. End with the **Top 10 to fix before the discussion**, ordered by risk to the demo and the grade.

## Phase 10: Runbook → `10_runbook.md`
- Prerequisites with exact versions and minimum laptop specs.
- Setup from zero on Linux, and on Windows via WSL2 if relevant (including gotchas such as Docker Desktop memory limits and Windows line endings breaking shell scripts): every command and its expected output.
- Port-forward map: what to open in the browser, and where.
- Verification checklist per component ("open X, you should see Y").
- How to trigger each Phase 3 scenario, step by step.
- How to reset everything to a clean state between demo runs.
- A kubectl cheat sheet for inspecting each NEXUS component.
- Troubleshooting table: symptom | likely cause | how to confirm | fix.

## Phase 11: Team Lead Briefing → `11_team_lead_briefing.md`
Written for Omar:
- NEXUS on one page: one diagram, ten sentences.
- **The 30 facts the lead must know cold:** key numbers, ports, intervals, thresholds, states, formulas.
- **Integration matrix:** a 6×6 grid of module pairs. For each pair: does a contract exist? schema? validated? tested? status and risk.
- **Module status board:** promised vs built (✅ 🟨 ❌), owner, test coverage, bus factor.
- **Contribution map** from git (facts only).
- **Risk register:** risk | likelihood | impact | mitigation | owner.
- **Grading criteria map** (if criteria were provided in Part B).
- **Demo-day plan:** pre-flight checklists (T-24h, T-1h, T-10min), what can fail live, the backup plan (recorded video of each scenario, screenshots, pre-loaded data), and who does what.
- **Leadership questions** examiners may ask (how the work was split, how modules were integrated, the git workflow, the biggest challenge, a conflict and how it was solved), with the git facts that support each answer and **blank spaces for Omar to write the human story. Do not invent it.**
- **Lead's self-test:** what Omar should be able to do without notes (draw the architecture, trace scenario A, explain each module in 2 minutes, run the demo from zero, answer the top 30 questions, name the top 10 risks).
- Open decisions and questions to ask the instructor.

## Phase 12: Learning Path and Defense → `12_learning_path.md`
- Reading order for each member (their own module first, then its neighbors, then the rest), with time estimates.
- A day-by-day team study plan up to the discussion date in Part B.
- **15+ labs** (F11).
- **Viva bank, 120+ questions** (F12), collecting the Phase 4 questions plus project-level, cross-module, leadership, and tough questions. These must be included:
  - Why not just use HPA or Argo Rollouts?
  - How do you stop NEXUS from making things worse (feedback loops, flapping, remediation storms)?
  - What happens if NEXUS itself goes down?
  - How do you know the root cause is correct? How did you measure accuracy?
  - Was the anomaly detection tested on real or synthetic data? What is the false-positive rate?
  - How accurate is the cost estimate, and where do the prices come from?
  - Which parts are deterministic and which are AI, and why?
  - What does M5 actually learn, and does it change any decision?
  - What permissions does NEXUS have? Could it delete production?
  - How would this scale to 100 services, or to several clusters?
  - What would you do differently?
  - What did you personally build? (one per member)
- **Mock exam:** 25 timed questions with an answer key.
- **Flashcards:** 150 one-line Q/A pairs for fast revision.
- **Demo scripts:** a 5-minute and a 15-minute version, with speaker, screen, timing, transitions, and fallback lines if something fails live.
- **Pitches:** 30 seconds, 2 minutes, for a non-technical listener, plus a 1-minute pitch per module for its owner.

## Phase 13: Index and Glossary → `00_START_HERE.md` + `13_glossary.md`
- `00_START_HERE.md`: NEXUS in one paragraph, a map of every guide file with reading time, and reading paths for (a) the whole team, (b) each module owner, (c) Omar, (d) the night before the discussion.
- `13_glossary.md`: every term, alphabetical, with a one-line definition and a link to its in-depth explanation.
- Final Open Questions and Docs vs Code lists.

## Phase 14: Accuracy Audit → `14_audit_report.md` (run in a NEW session)
- Open every citation in Tier 1 docs and at least 50 random citations elsewhere. Check that the cited lines really say what the guide claims.
- Check the ledger: every file ✅ or ⏭️ with a reason; every counter has Found = Documented.
- Re-check the arithmetic in every worked example.
- Fix every error found in the guide, and list each correction in the report.

# PART H: COMPANION PROMPTS (for after the guide exists)

1. **Tutor:** "Using `docs/learning/`, quiz me on M3 one question at a time. Wait for my answer, grade it, explain what I missed, and point me to the file and line."
2. **Mock examiner:** "Act as a strict NTI examiner. Ask me 15 questions about NEXUS, mixing modules. Push back on weak or vague answers with follow-up questions. Score me at the end and list what I should study."
3. **Explain a file to a teammate:** "Explain `<path>` to a teammate who has never seen it, using its doc in `docs/learning/05_files/` and the code. Then give them 3 questions to check they understood."
4. **Change impact:** "I want to change `<what>`. Using the code and `docs/learning/06_reference/call_graph.md`, list everything that could be affected and what we must test."
5. **Keep the docs current:** "Read `docs/learning/_INSTRUCTIONS.md` and `_COVERAGE.md`. Run `git diff --name-only <documented commit>..HEAD`. Update only the docs for changed files, update the counters and the commit hash, and list what changed."

# PART I: QUALITY GATE (check before finishing every file)

- [ ] Every NEXUS-specific claim has a file:line citation, or is flagged `⚠️ NOT FOUND IN REPO` / `(inferred)`.
- [ ] Every function, field, key, endpoint, and query in scope is documented. No "etc."
- [ ] Every number has a unit, a meaning, and a change effect.
- [ ] Every formula has a worked example with correct arithmetic.
- [ ] Every hand-off shows a real example payload.
- [ ] Every term is defined at first use and appears in the glossary.
- [ ] Every Mermaid diagram is valid and explained.
- [ ] Outputs are labeled `(expected, not executed)` or `(observed)`.
- [ ] The ledger and counters are updated.
- [ ] A beginner could answer every quiz question using only this guide.
- [ ] Nothing was invented to make the story sound better.

======================= END OF INSTRUCTIONS =======================
