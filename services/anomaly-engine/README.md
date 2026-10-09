# anomaly-engine (M2)

Scores telemetry from M1 and returns an `AnomalyEvent` (the frozen contract
in `/contracts/anomaly_event.json`).

```
GET  /health
POST /internal/anomalies/evaluate      TelemetrySnapshot -> AnomalyEvent
```

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

## Things to know

* **Port 8002 is the agreed port** (`docs/runtime-conventions.md`). `/health`
  returns the four fields that document requires (`service`, `status`,
  `version`, `environment`) and reads `SERVICE_NAME`, `SERVICE_VERSION` and
  `ENVIRONMENT`. Not done yet: structured JSON logging through
  `shared/logging`, which that document also requires.
* **Every snapshot gets an event,** including healthy ones with a low score.
  When to emit versus stay quiet is a Day 5 decision.
* **Events carry the snapshot's timestamp,** not the wall clock, so replayed
  windows and detection-delay numbers stay meaningful.
* **The event carries 4 features; M2 uses 8 internally.** The other four
  (`request_rate_change`, `latency_change`, `memory`, `replica_count`) are not
  in the frozen contract. The tuned baseline leans on the two change features,
  so if M3 needs them to tell a traffic spike from a bad deployment, propose a
  contract version bump to the team lead.
