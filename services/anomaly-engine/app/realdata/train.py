"""
Build the frozen reference from real captures (M2 runbook, Days 5 and 10).

Two jobs:

1. Learn each service's normal: the average and usual wobble of every feature,
   from every normal reading in the captures (healthy runs, and the stretch
   before each fault).

2. Choose the alert line, the distance from normal, in wobbles, at which a
   reading counts as an alert. Chosen from the data, not guessed:

     * score every reading with a ruler trained on the OTHER runs only, so
       no reading is judged by a reference that learned from it
     * find the noisiest NORMAL reading (the line must be above it)
     * find the quietest SETTLED fault reading (the line must be below it).
       "Settled" skips the first few readings of a fault, because M1 averages
       over a minute and so shows a new fault only gradually
     * if there is a gap, put the line in the middle of it on a log scale
       (the geometric mean). Wobbles span orders of magnitude, so the middle
       is the point equally many times above the normal as below the fault
     * if there is no gap, say so and write nothing. A line can't be honestly
       chosen from data that doesn't separate

The margin (how many times further the quietest fault was than the noisiest
normal) is saved with the reference, so how confident the choice was stays
visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..evaluation.metrics import PreparedRun, prepare_all
from ..evaluation.scenarios import Run
from ..model import ZScoreDetector
from ..reference import AlertLine, Reference, now_text, save_reference
from .capture import capture_to_runs, load_capture

# Readings at the start of a fault that may only show part of it.
RAMP_READINGS = 4
MIN_TRAINING_SAMPLES = 20


class TrainError(Exception):
    pass


@dataclass(frozen=True)
class RunAtLine:
    run_id: str
    scenario: str
    normal_readings: int
    false_alarms: int
    normal_max: float
    settled_readings: int
    caught: int
    settled_min: float | None
    first_alert_s: float | None


@dataclass(frozen=True)
class LineChoice:
    alert: AlertLine | None
    reason: str
    runs: list[RunAtLine]


def normal_rows(prepared: PreparedRun) -> list[tuple[float, ...]]:
    stop = len(prepared.full) if prepared.run.fault_start is None else prepared.run.fault_start
    return list(prepared.full[:stop])


def leave_one_out_distances(prepared: list[PreparedRun], min_samples: int = MIN_TRAINING_SAMPLES) -> list[list[float]]:
    """For each run, its readings' distances from normal, as judged by a ruler
    trained on the normal readings of every other run of the same service."""
    distances = []
    for index, target in enumerate(prepared):
        service = target.run.service
        rows = [
            row
            for j, other in enumerate(prepared)
            if j != index and other.run.service == service
            for row in normal_rows(other)
        ]
        if len(rows) < min_samples:
            raise TrainError(
                f"{target.run.run_id}: only {len(rows)} normal readings from the other runs; "
                f"need {min_samples}. Add another capture."
            )
        ruler = ZScoreDetector(min_samples=min_samples).fit({service: rows})
        distances.append([float(d) for d in ruler.distance_many(service, target.full)])
    return distances


def choose_alert_line(prepared: list[PreparedRun], min_samples: int = MIN_TRAINING_SAMPLES) -> LineChoice:
    distances = leave_one_out_distances(prepared, min_samples)

    normal_max = 0.0
    settled_min: float | None = None
    for p, d in zip(prepared, distances):
        fault = p.run.fault_start
        normal_max = max(normal_max, max(d if fault is None else d[:fault]))
        if fault is not None and len(d) > fault + RAMP_READINGS:
            run_min = min(d[fault + RAMP_READINGS:])
            settled_min = run_min if settled_min is None else min(settled_min, run_min)

    if settled_min is None:
        return LineChoice(None, "no faulty run has enough readings after its fault starts", [])

    floor = max(normal_max, 1.0)
    if settled_min <= floor:
        return LineChoice(
            None,
            f"no clean gap: the noisiest normal reading is {normal_max:.1f} wobbles from normal, "
            f"but a settled fault reading was only {settled_min:.1f}. "
            "No alert line can separate them without false alarms or misses.",
            _per_run(prepared, distances, line=None),
        )

    line = math.sqrt(floor * settled_min)
    alert = AlertLine(wobbles=line, normal_max_wobbles=normal_max, fault_min_wobbles=settled_min)
    return LineChoice(alert, "", _per_run(prepared, distances, line))


def _per_run(prepared, distances, line: float | None) -> list[RunAtLine]:
    rows = []
    for p, d in zip(prepared, distances):
        run, fault = p.run, p.run.fault_start
        normal = d if fault is None else d[:fault]
        settled = [] if fault is None else d[fault + RAMP_READINGS:]
        first = None
        if line is not None and fault is not None:
            hit = next((i for i in range(fault, len(d)) if d[i] >= line), None)
            if hit is not None:
                first = (run.snapshots[hit].timestamp - run.snapshots[fault].timestamp).total_seconds()
        rows.append(
            RunAtLine(
                run_id=run.run_id,
                scenario=run.scenario,
                normal_readings=len(normal),
                false_alarms=0 if line is None else sum(1 for x in normal if x >= line),
                normal_max=max(normal),
                settled_readings=len(settled),
                caught=0 if line is None else sum(1 for x in settled if x >= line),
                settled_min=min(settled) if settled else None,
                first_alert_s=first,
            )
        )
    return rows


def describe(choice: LineChoice) -> str:
    lines = []
    if choice.alert is None:
        lines.append(f"NO ALERT LINE CHOSEN. {choice.reason}")
    else:
        a = choice.alert
        lines.append(
            f"Alert line: {a.wobbles:.1f} wobbles from normal (score {a.score:.3f}).\n"
            f"  noisiest normal reading:      {a.normal_max_wobbles:8.1f} wobbles\n"
            f"  quietest settled fault:       {a.fault_min_wobbles:8.1f} wobbles\n"
            f"  margin: the quietest fault is {a.margin:.0f}x further out than the noisiest normal reading"
        )
    if choice.runs:
        lines.append(
            "\n  per run, each judged by a ruler that never saw it. The line sits in the gap by"
            "\n  construction, so these rows are clean by design: they show how WIDE the gap is,"
            "\n  not an independent test. For that, run `evaluate` on a capture not used here."
        )
        lines.append(f"    {'run':28}{'normal max':>11}{'false alarms':>14}{'settled min':>13}{'caught':>10}{'first alert':>13}")
        for r in choice.runs:
            settled = "-" if r.settled_min is None else f"{r.settled_min:.1f}"
            caught = "-" if not r.settled_readings else f"{r.caught}/{r.settled_readings}"
            first = "-" if r.first_alert_s is None else f"{r.first_alert_s:.0f}s"
            lines.append(
                f"    {r.run_id:28}{r.normal_max:>11.1f}{f'{r.false_alarms}/{r.normal_readings}':>14}"
                f"{settled:>13}{caught:>10}{first:>13}"
            )
    return "\n".join(lines)


def train_reference(
    capture_paths: list[str],
    out_path: str | Path,
    min_samples: int = MIN_TRAINING_SAMPLES,
) -> tuple[Reference | None, str]:
    """Build and save the reference. Returns (reference or None, a report).
    Nothing is written when no honest alert line exists."""
    runs: list[Run] = []
    notes: list[str] = []
    for name in capture_paths:
        capture = load_capture(name)
        if capture.points is None:
            raise TrainError(f"{name} has no readings yet; run: python -m app.realdata fetch {name}")
        found, skipped = capture_to_runs(capture)
        notes += [f"{Path(name).name}: {n}" for n in skipped]
        multiple = len(capture_paths) > 1
        runs += [
            Run(f"{Path(name).stem}:{r.run_id}" if multiple else r.run_id, r.service, r.scenario, r.fault_start, r.snapshots)
            for r in found
        ]
    if not runs:
        raise TrainError("no usable runs in those captures")

    prepared = prepare_all(runs)
    choice = choose_alert_line(prepared, min_samples)
    report = describe(choice)
    if notes:
        report = "Left out:\n  " + "\n  ".join(notes) + "\n\n" + report
    if choice.alert is None:
        return None, report

    rows: dict[str, list] = {}
    for p in prepared:
        rows.setdefault(p.run.service, []).extend(normal_rows(p))
    detector = ZScoreDetector(min_samples=min_samples).fit(rows)
    reference = Reference(
        detector=detector,
        alert=choice.alert,
        trained_on=tuple(Path(n).name for n in capture_paths),
        created=now_text(),
    )
    save_reference(out_path, reference)
    counts = ", ".join(f"{s}: {len(r)} readings" for s, r in sorted(rows.items()))
    return reference, f"{report}\n\nSaved the reference to {out_path}  ({counts})"
