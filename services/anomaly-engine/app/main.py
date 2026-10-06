"""
M2 anomaly-engine service.

    GET  /health
    POST /internal/anomalies/evaluate     TelemetrySnapshot -> AnomalyEvent

Run (from services/anomaly-engine):
    uvicorn app.main:app --host 0.0.0.0 --port 8002

Scoring is the Day-2 threshold baseline for now. Swapping in the trained
model on Day 5 changes the scoring call and MODEL_NAME below; the endpoint
and the AnomalyEvent it returns stay the same.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from shared.contracts import AnomalyEvent, TelemetrySnapshot

from .baseline import MODEL_NAME, baseline_score
from .features import extract_features

app = FastAPI(title="M2 Anomaly Engine")

# Previous snapshot per service, used for the *_change features. In memory
# only. Day 3 (real M1 windows) decides whether this needs persisting.
_previous: dict[str, TelemetrySnapshot] = {}


def reset_state() -> None:
    """Forget all previous snapshots. Used by tests."""
    _previous.clear()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "anomaly-engine"}


@app.post("/internal/anomalies/evaluate", response_model=AnomalyEvent)
def evaluate(snapshot: TelemetrySnapshot) -> AnomalyEvent:
    """Score one snapshot and return an AnomalyEvent.

    Every snapshot gets an event, including healthy ones (low score). When to
    emit versus stay quiet is a threshold decision for Day 2 / Day 5.

    The event is stamped with the snapshot's own timestamp, not the wall
    clock. That keeps replayed historical windows (Day 3) and the detection-
    latency numbers (Day 7, Day 11) meaningful.
    """
    features = extract_features(snapshot, _previous.get(snapshot.service))
    _previous[snapshot.service] = snapshot

    score, severity = baseline_score(features)

    return AnomalyEvent(
        anomaly_id=f"ANO-{uuid.uuid4().hex[:8]}",
        timestamp=snapshot.timestamp,
        service=snapshot.service,
        score=score,
        severity=severity,
        model=MODEL_NAME,
        features=features.to_contract(),
    )
