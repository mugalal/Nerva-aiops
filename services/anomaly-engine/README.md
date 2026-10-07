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
| `app/main.py` | The two endpoints |
| `app/evaluation/scenarios.py` | Generates synthetic healthy / bad-deployment / traffic-spike runs |
| `app/evaluation/metrics.py` | Scores a detector against those runs (precision, recall, F1, false alarms, detection delay) |
| `app/evaluation/tune.py` | Searches threshold combinations |
| `app/evaluation/run.py` | Command line that ties the three together |

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

## Things to know

* **Port 8002 is a guess.** Check `docs/runtime-conventions.md` for the agreed
  port and change it here if it differs.
* **Every snapshot gets an event,** including healthy ones with a low score.
  When to emit versus stay quiet is a Day 5 decision.
* **Events carry the snapshot's timestamp,** not the wall clock, so replayed
  windows and detection-delay numbers stay meaningful.
* **The event carries 4 features; M2 uses 8 internally.** The other four
  (`request_rate_change`, `latency_change`, `memory`, `replica_count`) are not
  in the frozen contract. The tuned baseline leans on the two change features,
  so if M3 needs them to tell a traffic spike from a bad deployment, propose a
  contract version bump to the team lead.
