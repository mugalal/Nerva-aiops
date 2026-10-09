# anomaly-engine (M2)

Scores telemetry from M1 and returns an `AnomalyEvent` (the frozen contract
in `/contracts/anomaly_event.json`).

```
GET  /health
POST /internal/anomalies/evaluate               TelemetrySnapshot -> AnomalyEvent
GET  /internal/anomalies?incident_id=...        ONE anomaly for an incident (the route M3 calls)
GET  /internal/anomalies/recent                 recent alerts, newest first
GET  /internal/correlation/incidents            incident candidates (the frozen Incident contract)
GET  /internal/correlation/incidents/{id}       one candidate plus the evidence behind it
```
(`/internal/anomalies/*` and `/internal/correlation/*` are M2's per the architecture
document. The shared core's `/api/incidents/*` is not.)

## Run

```bash
cd services/anomaly-engine
python -m pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8002
```

## Test (from the repo root)

```bash
python -m pytest tests -v
```

## What is here

| File | Purpose |
|---|---|
| `app/features.py` | Raw telemetry to M2's 8-feature vector; `to_contract()` narrows it to the 4 features the contract carries |
| `app/baseline.py` | The threshold baseline (**not ML**) and its settings object, `BaselineConfig` |
| `app/pipeline.py` | One reading in, one `AnomalyEvent` out; alerts are correlated and their evidence saved |
| `app/correlation.py` | Consecutive alerts for a service become **one** incident candidate, with the signals involved |
| `app/evidence.py` | Append-only log of every alert with all 8 features and per-feature distances |
| `app/handoff.py` | Hands an incident and its anomalies to the shared API, in the team's documented two-call sequence |
| `app/poller.py` | Pulls live snapshots from M1; skips stale, repeated, missing and malformed data |
| `app/reference.py` | The frozen reference file: each service's normal plus the alert line |
| `app/model.py` | The two detectors that learn each service's normal: Isolation Forest, and a plain z-score ruler (**not ML**) |
| `app/main.py` | The two endpoints |
| `app/evaluation/scenarios.py` | Generates synthetic healthy / bad-deployment / traffic-spike runs |
| `app/evaluation/metrics.py` | Scores a detector against those runs (precision, recall, F1, false alarms, detection delay) |
| `app/evaluation/tune.py` | Searches threshold combinations |
| `app/evaluation/run.py` | Command line that ties the three together |
| `app/evaluation/compare.py` | Day 4: the four-way comparison (baseline, ruler, forest, pooled forest) |
| `app/realdata/m1_client.py` | Day 3: reads M1's history (stdlib only; errors are raised, never replaced by made-up data) |
| `app/realdata/drill.py` | Day 3: makes real faults on M1's demo service and records exactly when each begins |
| `app/realdata/capture.py` | Day 3: the capture file (M1's readings plus the answer key) and its conversion to `Run`s |
| `app/realdata/report.py` | Day 3: how the detectors do on real readings |
| `app/realdata/train.py` | Builds the reference from captures and chooses the alert line from the data |
| `app/realdata/push.py` | `handoff` command: gives the shared API an incident from the running M2, by hand |
| `app/realdata/final.py` | `evaluate` (results table) and `live-check` (running M2 against the drill) |

The shared models live in `shared/contracts/` (`from shared.contracts import ...`).

## Day 2: evaluating and tuning the baseline

```bash
cd services/anomaly-engine
python -m app.evaluation.run                      # placeholder vs the current config
python -m app.evaluation.run --tune               # re-tune, then compare
python -m app.evaluation.run --csv results.csv    # also write the Day-11 table
```

No extra installs needed. Everything is seeded, so a run is repeatable. With
`--tune` it takes a minute or two.

### The data

Four services with different "normal" (search-service is slow even when
healthy, so one fixed latency line can't suit all of them), three scenarios:

| Scenario | What changes | What doesn't |
|---|---|---|
| healthy | nothing, apart from rare one-off latency blips | |
| bad_deployment | latency up 1.6-6x, 5xx up 2-18 points, version v1 to v2 | request rate |
| traffic_spike | request rate up 2-4.5x, CPU and latency up with it | version, errors (barely) |

### How the config was chosen

`--tune` tries 6000 threshold combinations on one dataset (seed 1000) and
keeps the one with the best F1 among those that raise false alarms on no more
than 0.2% of normal snapshots. It is then scored on a different dataset
(seed 2000) that the search never saw.

Held-out results (120 runs, 7200 snapshots):

| | placeholder (Day 1) | tuned, absolute rules only | tuned, with change rules (**current**) |
|---|---|---|---|
| precision | 0.867 | 0.983 | 0.955 |
| recall | 0.650 | 0.713 | 0.800 |
| F1 | 0.743 | 0.826 | 0.871 |
| recall, bad_deployment | 95% | 100% | 100% |
| recall, traffic_spike | 35% | 42.5% | 60% |
| false alarms, healthy runs | 20% | 2.5% | 7.5% |
| false alarms, normal snapshots | 0.6% | 0.1% | 0.1% |
| mean detection delay | 13 s | 25 s | 4 s |

### How far to trust this

* **The data is synthetic.** It is my assumption of what such telemetry looks
  like. Held-out results show the tuning generalises across draws from the
  same generator, not to real traffic. Several tuned thresholds sit near the
  edges of the faults the generator invents (the 5xx rule at 0.02 is the
  smallest error jump it ever produces). **Re-tune on M1's real telemetry on
  Day 3.**
* **Small samples.** Each scenario has 40 runs, so a measured spike recall of
  60% could plausibly be anywhere from about 45% to 75%. The change rules
  helped in the same direction on both datasets (45% to 72.5% on the tuning
  set, 42.5% to 60% held-out), so they probably help, but treat the size of the
  gain as an estimate, not a fact.
* **The remaining weakness is traffic spikes on services with headroom.** All
  16 held-out misses are spikes on payment, checkout and auth: CPU and latency
  rise but stay under any line that is safe on healthy data. A fixed threshold
  can't see "3x normal traffic". A per-service reference ("compared with this
  service's own recent normal") is the natural next step, and it is also what
  the Day-4 model will need.
* **Severity is just a label.** Whether something is an alert is decided by
  `alert_score`; `low` / `medium` / `high` is how much of the rule weight is
  breached. Don't build on severity yet.

## Day 4: does a model beat the baseline?

```bash
python -m pip install scikit-learn numpy
cd services/anomaly-engine
python -m app.evaluation.compare                  # standard-size faults
python -m app.evaluation.compare --severity 0.3   # faults at 30% strength
```

Four detectors, judged by the same code on the same data, with the same
false-alarm budget (0.2% of normal readings):

| Detector | What it is |
|---|---|
| baseline | The Day-2 threshold lines (the same lines for every service) |
| z-score per-service | A plain ruler: how many "usual wobbles" is this reading from *this service's* healthy average? **Not ML** |
| forest per-service | Isolation Forest, one model per service, trained on healthy readings only |
| forest pooled | Isolation Forest, one model for all services |

Data roles: the model trains on healthy readings only (seed 1500); its alert
line is chosen on the tuning set the baseline was tuned on (seed 1000); every
number below is from the held-out set (seed 2000), which no choice ever used.
When faults change size the baseline is re-tuned too, so it gets the same
chance.

### Held-out results

Standard faults:

| | baseline | z-score per-service | forest per-service | forest pooled |
|---|---|---|---|---|
| precision | 0.955 | 0.988 | 0.988 | 0.960 |
| recall | 0.800 | 1.000 | 1.000 | 0.900 |
| **F1** | 0.871 | **0.994** | **0.994** | 0.929 |
| spikes caught | 60% | 100% | 100% | 97.5% |
| normal readings falsely flagged | 6 of 4610 | 2 | 5 | 5 |
| mean detection delay | 4 s | 10 s | 12 s | 21 s |

Faults at 30% strength:

| | baseline (re-tuned) | z-score per-service | forest per-service | forest pooled |
|---|---|---|---|---|
| precision | 0.932 | 0.975 | 0.985 | 0.919 |
| recall | 0.512 | 0.963 | 0.825 | 0.425 |
| **F1** | 0.661 | **0.969** | 0.898 | 0.581 |
| spikes caught | 20% | 92.5% | 77.5% | 32.5% |
| normal readings falsely flagged | 7 of 4610 | 8 | 6 | 5 |
| mean detection delay | 46 s | 25 s | 91 s | 45 s |

### What to conclude, and what not to

* **Knowing each service's own normal is what helps.** Both per-service
  detectors beat the global threshold lines, and the pooled forest (same
  model, no per-service reference) is barely better than the baseline.
* **The forest did not beat the plain ruler.** It tied at standard fault size
  and lost at 30%. The runbook says "select empirically" (Day 5) and to
  explain "why the selected model won" (Day 13); it only requires the ML model
  to be *evaluated*. Read that way, a simple detector can be selected if it
  wins, which makes the ruler the leading candidate on this evidence. That is
  an interpretation, not a quoted rule: confirm with the team lead that
  shipping a non-ML detector is acceptable.
* **This data favours the ruler.** The generator makes each metric wobble
  independently, in a bell curve, around a fixed average. That is exactly the
  situation a "how many wobbles away" ruler is built for. Real telemetry has
  daily cycles, correlated metrics and heavy tails, which is where a forest
  could pull ahead. Nothing here tests that. **Day 3's real data decides.**
* **The standard faults are very easy.** The ruler's alert line sat at about
  10.5 wobbles from normal; almost every standard fault is further out than
  that, which is why three detectors look almost perfect. The 30% contest is
  the one that separates them.
* **The forest used default settings and was not tuned.** A tuned forest,
  or one that ignores features with no signal (memory here), may close the gap.
* **Sample size.** 80 faulty runs per contest. 77 caught versus 66 is a real
  gap; a difference of one or two runs is not.
* **Reproducibility.** The baseline and ruler numbers repeat exactly. The
  forest's can shift slightly between scikit-learn versions.
* **Still open for a new service.** Both per-service detectors need healthy
  history before they can score it. The baseline needs none.

## Day 3: real readings from M1

Everything above ran on synthetic data. This is how to get the real thing.

**What M1 gives us.** `GET /internal/telemetry/window?service=...&start=...&end=...&step_seconds=15`
returns the six frozen metrics per step. No Kubernetes is needed: M1's own
README starts the demo `payment-service`, Prometheus and M1's API with
`docker compose`. There is one demo service, so real data can't test the
"each service has its own normal" idea, only the ruler against the forest on
one service.

**What the drill does.** Real data only judges a detector if we know when each
fault really began, so the drill creates the faults and writes the times down:

| Stretch | What happens | How the start time is found |
|---|---|---|
| warm-up | steady traffic, readings ignored | |
| healthy | steady traffic | |
| bad deployment | swap `payment-service` to faulty v2 (+600 ms, 14% errors), then back | polls the demo's `/version`; the first `v2` answer is the exact start |
| recovery | steady traffic while M1's 1-minute windows clear | |
| traffic spike | 6x the workers sending traffic | the moment the load is raised |

If the drill is stopped part-way through the bad deployment, it puts
`payment-service` back to healthy v1 before exiting.

### Run it

```bash
# 1. A second folder that shows M1's version of the repo (your branch is untouched)
git worktree add ~/Nerva-m1 origin/m1/prometheus-integration

# 2. Start M1's stack (the first time it downloads several images)
cd ~/Nerva-m1
docker compose -f observability/docker-compose.yml up -d --build

# 3. Back in M2: check the plumbing first (about 10 minutes, results not meaningful)
cd ~/Nerva-aiops/services/anomaly-engine
python -m app.realdata drill --stack-dir ~/Nerva-m1 --quick --out quick.json

# 4. The real drill (about 30 minutes). It first builds the faulty version, so the
#    network is needed BEFORE the timeline starts, never in the middle of it.
python -m app.realdata drill --stack-dir ~/Nerva-m1 --out capture1.json

# 5. What happened?
python -m app.realdata report capture1.json
```

### Before you start, and if it goes wrong

* **Keep the laptop plugged in and stop it sleeping** (Settings, System, Power:
  sleep when plugged in = Never; and closing the lid = Do nothing). If it sleeps,
  the demo and Prometheus are paused with it and the readings get a hole in
  them. The drill notices the clock jump, stops, and puts v1 back, because data
  with a gap looks fine but isn't.
* **"could not build the faulty version ... lookup auth.docker.io ... i/o
  timeout"** is a DNS problem inside WSL, common after a sleep. Quit Docker
  Desktop, run `wsl --shutdown` in PowerShell, start Docker Desktop, wait for
  "Engine running", reopen Ubuntu, and check `getent hosts auth.docker.io`
  prints an address. Then start the drill again.
* **Ctrl+C is always safe.** If the faulty version is live, the drill puts v1
  back before it exits.

If M1 is down when the drill ends, the answer key is already saved. Retry just
the download with `python -m app.realdata fetch capture1.json`. Run the drill
more than once and pass every capture to `report`: each detector is then
trained on more normal readings, and the runbook's Day 7 asks for three
bad-deployment runs anyway.

### Results on real readings (three drills)

Three 27-minute drills against M1's demo `payment-service` (`capture1`, `capture2`,
`capture3`). Each detector is trained on the normal readings of every *other* run
(180 readings) and scored on the run being judged. "Caught" is the share of fault
readings scoring above the highest normal reading of the same run, i.e. what an
alert line with zero false alarms would catch.

| run | threshold alarm | ruler | forest |
|---|---|---|---|
| capture1, bad deployment | 95% | **100%** | 100% |
| capture1, traffic spike | 6% | **100%** | 100% |
| capture2, bad deployment | 90% | **95%** | 15% |
| capture2, traffic spike | 12% | **94%** | 94% |
| capture3, bad deployment | 95% | **100%** | 100% |
| capture3, traffic spike | 12% | **94%** | 6% |

* **The ruler repeated across all three drills:** 94 to 100% caught in every run,
  AUC 0.979 to 1.000. The noisiest normal reading was 6.2 wobbles from normal; the
  faults reached hundreds to over a thousand.
* **The forest was inconsistent:** 100% in three runs, 94% in one, and 15% and 6%
  in the other two. The two near-failures are the drills with the noisier normal
  (capture2, capture3). Its score stops growing once a reading is outside the range
  it learned, so it cannot tell "slightly outside" from "far outside". The ruler's
  score keeps growing with distance.
* **The threshold alarm** raised no false alarm in 120 healthy readings (40 per
  drill) and caught bad deployments (90 to 95%), but caught only 6 to 12% of
  spike readings: it fires once at the jump and then goes quiet.
* **"Normal" differs between drills.** Capture1's normal was very calm; capture2's
  and capture3's wobbled several times more (request rate 0.27 versus 2.0 and 0.88,
  latency 0.19 versus 0.92 and 1.01). The ruler's alert line still sat about 8 times
  above the noisiest normal reading.

### Live result (capture3)

The frozen reference was trained on capture1 and capture2 only (alert line 48.9
wobbles, margin 60x). M2 then ran live against M1 while the drill produced
capture3, a run the reference had never seen. `live-check`:

| | bad deployment | traffic spike |
|---|---|---|
| fault really started | 17:50:00 | 18:00:03 |
| M2 opened an incident | 17:50:29 | 18:00:17 |
| delay | 29 s | 14 s |
| alerts, all in ONE incident | 22 | 21 |

False alarms: none. Detection delays include M1's one-minute averaging window and
M2's 15-second poll.

**Limits.** The drill's healthy traffic is perfectly flat, so "normal" is
unrealistically calm: a normal swing in a real service could be tens of wobbles. These
results show the ruler separates these faults from this normal and stayed quiet for
about 15 healthy minutes; they do not show it stays quiet through a real busy day.
That needs a long healthy interval with natural variation (runbook Day 10). One demo
service. Only one live, fully independent run so far; the runbook's Day 7 asks for
three bad-deployment runs.

### Reading the report

1. **What the real readings look like.** Normal versus during the fault, for
   each metric. Look for metrics that never move when healthy (the demo's error
   rate is exactly 0): those are why the ruler measures such features instead
   of skipping them.
2. **The frozen baseline on real readings.** Thresholds tuned on synthetic
   data, never shown a real reading. A straight transfer test.
3. **Can each detector separate the fault from normal?** No alert line is
   chosen, because a few minutes of data can't justify one. Each detector is
   trained on the *other* runs' normal readings and scored on the run being
   judged. *AUC* is the chance a fault reading scores above a normal one (1.0
   always, 0.5 a coin toss). *Caught* is the share of fault readings above the
   highest normal reading of the same run, i.e. what an alert line with zero
   false alarms would catch.

M1's latency and rates use a one-minute window, so after a fault starts the
readings take about a minute to show its full size. Expect a ramp, and a
detection delay that includes it.

## The running service (Days 5, 6, 9, 12)

### What it does with a reading

```
TelemetrySnapshot -> score -> AnomalyEvent
                                  | crosses the alert line?
                                  v
              joins (or opens) ONE incident candidate for that service
              evidence saved: score, all 8 features, distance of each from normal
```

* **Which detector scores.** If a frozen reference exists for the service, the
  per-service ruler (`model: z_score_per_service`). Otherwise the threshold
  alarm (`model: threshold_baseline`), which needs no history, so a service
  nobody has trained on is still watched.
* **One fault, one incident.** Alerts for a service closer together than
  `M2_CORRELATION_GAP_S` (default 120 s) join the same candidate. The candidate
  lists the *signals* (metrics) that were abnormal at any point, which is the
  runbook's "which abnormal signals belong to the same incident?". Candidates
  are handed on as the frozen `Incident` contract, status `DETECTED`.
* **Deployment context.** If the service's `version` changed shortly before an
  alert, that is attached to the incident (`context.version_change`).
* **Evidence.** One line per alert in `data/evidence.jsonl` (on by default when
  polling is on; `M2_EVIDENCE_PATH` to move it, `none` to turn it off).
  `data/` is git-ignored.

### Bad data (runbook Day 12)

Nothing below ever produces a made-up reading or a confident anomaly:

| Situation | What M2 does |
|---|---|
| NaN or missing value in a reading | the ruler scores it 0 and it cannot alert |
| M1 unreachable or timed out | no reading is scored; health says `degraded: ... unreachable` |
| M1 answers with an error | M1's own error category is kept; health says so |
| M1 returns a malformed reading | rejected (`bad_response`); health says so |
| newest reading older than `M2_STALE_AFTER_S` | ignored (`stale`) |
| M1 repeats the same reading | scored once |
| a reading older than the newest already seen | scored with no "change" features; does not replace the newest |
| last reading more than 5 min old | the new reading is scored with no "change" features, so the first reading after an outage can't look like a jump |
| the reference file is broken or made for another feature order | the threshold alarm runs; health says `degraded: invalid reference` |

`/health` is `ok` or `degraded` and also reports `detector` and `dependencies`
(`m1`, `reference`).

### Handing incidents to the shared API (Days 5 and 8, M3)

The team's integration (`docs/REAL_SERVICE_INTEGRATION.md`,
`scripts/run-integration-incident.ps1`) says plainly that "automatic trained M2
detection is not established": an operator hand-writes an anomaly labelled
`manual-m1-observation` and posts it. M2 can now supply the real thing.

M2 repeats the operator's two calls exactly (the shared API is the M4 service,
`127.0.0.1:18004` in the team's script):

1. `POST /api/incidents/` with `incident_id, started_at, severity,
   affected_services, anomaly_ids: []`
2. `POST /internal/anomalies?incident_id=...` with the `AnomalyEvent`, unchanged

**Which anomalies, and why.** The shared API tells M3 about the *latest* linked
anomaly. A fault takes about a minute to show fully in M1's one-minute
windows, so the first alert can be a half-developed reading. M2 therefore links
the **first alert the moment the incident opens**, and the **strongest alert once
the incident has `M2_HANDOFF_SETTLE_ALERTS` alerts** (default 4, about a
minute). From then on that is what M3 reads.

**Rules taken from the shared API's own routes**, and what M2 does about them:

| The shared API | M2 |
|---|---|
| the path is `/api/incidents/` with the trailing slash (without it: 307) | uses the exact path |
| an incident or anomaly may not be in the future | caps the incident start at the current time; an anomaly refused as "future" is never altered, and is retried on a later alert |
| an existing incident answers 409 | counted as done, so a half-finished handoff can be completed |
| the same anomaly id with different evidence answers 409 | ids are unique, and events are never changed |
| an anomaly's service must be one of the incident's services | always true: an incident is per service |

A failed delivery **never stops scoring**. It is recorded on the incident
(`handoff` in `GET /internal/correlation/incidents/{id}`), shown in `/health`
(`dependencies.shared_api`, status `degraded`), logged as a warning, and retried
on a later alert, no sooner than 10 seconds after the last try.

It is **off by default**. To turn it on:

```bash
M2_HANDOFF_ENABLED=true M2_HANDOFF_URL=http://127.0.0.1:18004 \
M2_POLL_ENABLED=true python -m uvicorn app.main:app --port 8002
```

To do it once, by hand, from a running M2 (it also prints what M3 will read):

```bash
python -m app.realdata handoff --to http://127.0.0.1:18004
```

**M2's own route for M3.** `GET /internal/anomalies?incident_id=...` returns one
`AnomalyEvent` in exactly the frozen shape, which is what M3's HTTP provider
expects. `pick=peak` (default), `first` or `latest` chooses the alert.

**What has and has not been verified.** The handoff was run against the shared
API's own `incidents.py`, `anomalies.py`, `contracts.py` and state machine, taken
from the integration branch: an incident was created, its anomalies linked, and
`GET /internal/anomalies` returned the strongest. A stand-in that enforces the
same rules answers identically on 21 requests, and the tests use it. It has
**not** been run against a deployed shared API, nor with M3 or M4 calling it:
that needs the team's stack.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `M2_REFERENCE_PATH` | `reference/reference.json` if it exists | the frozen reference; `none` = threshold alarm only |
| `M2_POLL_ENABLED` | false | pull live snapshots from M1 |
| `M1_TELEMETRY_BASE_URL` | `http://localhost:8001` | where M1 is (shared setting) |
| `M2_POLL_SERVICES` | `payment-service` | comma-separated services to watch |
| `M2_POLL_INTERVAL_S` | 15 | seconds between polls |
| `M2_STALE_AFTER_S` | 120 | a reading older than this is ignored |
| `M2_CORRELATION_GAP_S` | 120 | alerts closer than this are one incident |
| `M2_EVIDENCE_PATH` | `data/evidence.jsonl` when polling | where evidence is appended |
| `M2_HANDOFF_ENABLED` | false | hand incidents to the shared API as they open |
| `M2_HANDOFF_URL` | `SHARED_NEXUS_API_BASE_URL` | where the shared API is (the team's script uses `http://127.0.0.1:18004`) |
| `M2_HANDOFF_SETTLE_ALERTS` | 4 | alerts after which the strongest one is linked too |
| `M2_JSON_LOGGING` | on | `off` leaves logging alone |
| `SERVICE_NAME`, `SERVICE_VERSION`, `ENVIRONMENT`, `LOG_LEVEL` | `anomaly-engine`, `0.1.0`, `development`, `INFO` | the shared runtime variables (via `shared.config`) |
| `SHARED_NEXUS_API_BASE_URL` | `http://localhost:8000` | the shared settings default; note the team's script uses port 18004, so set `M2_HANDOFF_URL` |

### From captures to a running detector

```bash
cd services/anomaly-engine

# 1. Build the frozen reference and choose the alert line FROM THE DATA.
#    Refuses (and writes nothing) if normal and fault readings overlap.
python -m app.realdata train capture1.json capture2.json

# 2. Run M2 against live M1 (M1's stack must be up).
M2_POLL_ENABLED=true python -m uvicorn app.main:app --port 8002

# 3. In another terminal, make real faults. M2 should open one incident per fault.
python -m app.realdata drill --stack-dir ~/Nerva-m1 --out capture3.json

# 4. When it finishes:
python -m app.realdata live-check capture3.json     # M2's incidents vs the drill's answer key
python -m app.realdata evaluate   capture3.json --csv results.csv
python -m app.realdata report capture1.json capture2.json capture3.json
```

How the alert line is chosen: every reading is scored by a ruler that never saw
its run; the line goes in the middle (on a log scale) of the gap between the
noisiest normal reading and the quietest *settled* fault reading (the first four
readings of a fault are skipped, because M1's one-minute windows show a fault
only gradually). The margin between the two is saved with the reference.
Because the line sits in the gap by construction, `train`'s own table is clean
by design. **The independent test is `evaluate` and `live-check` on a capture
the reference was not built from.** `evaluate` says so if you give it one that
was.

`live-check` prints, for every injected fault: when it started, when M2 opened
an incident, the delay, the number of alerts and the signals involved; and
counts every incident that matched no fault as a false alarm. It exits 0 only
if every fault was caught and nothing false-alarmed.

### What is not finished, honestly

* **False alarms on a normal busy day are unmeasured.** The drill's healthy
  traffic is perfectly flat. A real service's normal swings are far larger, and
  a ruler trained on a flat history may call them anomalies. The runbook's
  Day 10 asks for a defined healthy interval with natural variation; that needs
  a longer, wavier healthy period than the drill produces.
* **One demo service.** Per-service references have only been exercised on
  `payment-service`. Other services use the threshold alarm until trained.
* **M3 has not been run against M2.** The route M3's provider calls exists, and
  the handoff to the shared API (where the integration keeps incidents and
  anomalies) matches the shared API's own routes. A full run (M2 live, handoff
  on, M3 and M4 reading it, approval, recovery) needs the team's stack and has not
  been done. Scores near 1 saturate, so "the strongest alert" among near-ties is
  arbitrary; it does not change which fault was seen.
* **Cross-service grouping is not done.** Correlation is per service. Grouping
  incidents across services (for example by a shared deployment) needs a second
  service to try it on.
* **Severity is a label.** `high` starts at 4 times the alert line. It has not
  been evaluated; don't build on it.
* **Incident candidates live in memory.** A restart forgets them (the evidence
  file keeps the alerts). Persisting candidates is not done.

## Things to know

* **Port 8002 is the agreed port** (`docs/runtime-conventions.md`). `/health`
  returns the four fields that document requires (`service`, `status`,
  `version`, `environment`) through `shared.config`, and logs structured JSON
  through `shared.logging`, both as that document requires.
* **Every snapshot gets an event,** including healthy ones with a low score.
  When to emit versus stay quiet is a Day 5 decision.
* **Events carry the snapshot's timestamp,** not the wall clock, so replayed
  windows and detection-delay numbers stay meaningful.
* **The event carries 4 features; M2 uses 8 internally.** The other four
  (`request_rate_change`, `latency_change`, `memory`, `replica_count`) are not
  in the frozen contract. The tuned baseline leans on the two change features,
  so if M3 needs them to tell a traffic spike from a bad deployment, propose a
  contract version bump to the team lead.
