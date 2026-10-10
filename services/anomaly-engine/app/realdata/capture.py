"""
A capture: real readings from M1 plus the answer key (M2 runbook, Day 3).

Real data is only useful for judging a detector if we know the truth, meaning
when each fault really began. A capture holds both:

    points   what M1 reported, one TelemetrySnapshot per step
    runs     the answer key: for each stretch, its scenario and the exact time
             the fault started (None for a healthy stretch)
    events   a log of everything the drill did, with timestamps

`capture_to_runs` turns the answer key and the points into the same `Run`
objects the synthetic data produces, so everything built on Run (the scorer,
the tuner, the comparison) works on real data without changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from shared.contracts import TelemetrySnapshot

from ..evaluation.scenarios import Run

FORMAT_VERSION = 1

# A run needs this many readings before the fault to say anything about
# "normal" for it.
MIN_NORMAL_READINGS = 3


@dataclass(frozen=True)
class RunSpec:
    """One stretch of the timeline, with its answer."""

    run_id: str
    scenario: str                       # healthy | bad_deployment | traffic_spike
    start: datetime
    end: datetime
    fault_start: datetime | None        # None for a healthy stretch


@dataclass
class Capture:
    service: str
    step_seconds: int
    m1_url: str
    recorded_at: datetime
    events: list[dict] = field(default_factory=list)
    runs: list[RunSpec] = field(default_factory=list)
    points: list[TelemetrySnapshot] | None = None      # None until M1's history is fetched

    def span(self) -> tuple[datetime, datetime]:
        """The time range the history has to cover to include every run."""
        if not self.runs:
            raise ValueError("capture has no runs")
        return min(r.start for r in self.runs), max(r.end for r in self.runs)


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat().replace("+00:00", "Z")


def save_capture(path: str | Path, capture: Capture) -> None:
    body = {
        "format": FORMAT_VERSION,
        "service": capture.service,
        "step_seconds": capture.step_seconds,
        "m1_url": capture.m1_url,
        "recorded_at": _iso(capture.recorded_at),
        "events": capture.events,
        "runs": [
            {
                "run_id": r.run_id,
                "scenario": r.scenario,
                "start": _iso(r.start),
                "end": _iso(r.end),
                "fault_start": _iso(r.fault_start),
            }
            for r in capture.runs
        ],
        "points": None if capture.points is None else [p.model_dump(mode="json") for p in capture.points],
    }
    Path(path).write_text(json.dumps(body, indent=2) + "\n")


def _parse(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text.replace("Z", "+00:00"))


def load_capture(path: str | Path) -> Capture:
    body = json.loads(Path(path).read_text())
    if body.get("format") != FORMAT_VERSION:
        raise ValueError(f"{path}: unsupported capture format {body.get('format')!r}")
    points = body.get("points")
    return Capture(
        service=body["service"],
        step_seconds=body["step_seconds"],
        m1_url=body["m1_url"],
        recorded_at=_parse(body["recorded_at"]),
        events=body.get("events", []),
        runs=[
            RunSpec(
                run_id=r["run_id"],
                scenario=r["scenario"],
                start=_parse(r["start"]),
                end=_parse(r["end"]),
                fault_start=_parse(r["fault_start"]),
            )
            for r in body["runs"]
        ],
        points=None if points is None else [TelemetrySnapshot.model_validate(p) for p in points],
    )


def capture_to_runs(capture: Capture) -> tuple[list[Run], list[str]]:
    """Cut the history into Runs using the answer key.

    Returns (runs, notes). A stretch that can't make a usable run is left out
    and explained in `notes`; it is never padded or invented.
    """
    if capture.points is None:
        raise ValueError("this capture has no readings yet; fetch them from M1 first")

    runs: list[Run] = []
    notes: list[str] = []
    for spec in capture.runs:
        inside = sorted(
            (p for p in capture.points if spec.start <= p.timestamp <= spec.end),
            key=lambda p: p.timestamp,
        )
        if not inside:
            notes.append(f"{spec.run_id}: no readings between its start and end; left out")
            continue

        fault_index = None
        if spec.fault_start is not None:
            fault_index = next((i for i, p in enumerate(inside) if p.timestamp >= spec.fault_start), None)
            if fault_index is None:
                notes.append(f"{spec.run_id}: no readings after the fault started; left out")
                continue
            if fault_index < MIN_NORMAL_READINGS:
                notes.append(
                    f"{spec.run_id}: only {fault_index} readings before the fault "
                    f"(need {MIN_NORMAL_READINGS}); left out"
                )
                continue

        runs.append(
            Run(
                run_id=spec.run_id,
                service=capture.service,
                scenario=spec.scenario,
                fault_start=fault_index,
                snapshots=tuple(inside),
            )
        )
    return runs, notes
