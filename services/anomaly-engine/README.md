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
| `app/baseline.py` | Day-2 threshold baseline. **Not ML.** The yardstick the Day-4 model has to beat |
| `app/main.py` | The two endpoints |

The shared models live in `shared/contracts/` (import `from shared.contracts import ...`).

## Things to know

* **Port 8002 is a guess.** Check `docs/runtime-conventions.md` for the agreed
  port and change it here if it differs.
* **Baseline thresholds are placeholders** (`latency_p95_ms > 500`,
  `http_5xx_rate > 0.05`, ...), not tuned numbers. Day 2 tunes them.
* **Every snapshot gets an event,** including healthy ones with a low score.
  When to emit versus stay quiet is a Day 2 / Day 5 decision.
* **Events carry the snapshot's timestamp,** not the wall clock, so replayed
  windows and detection-latency numbers stay meaningful.
* **The event carries 4 features; M2 uses 8 internally.** The other four
  (`request_rate_change`, `latency_change`, `memory`, `replica_count`) are not
  in the frozen contract. If M3 needs them, propose a contract version bump to
  the team lead.
