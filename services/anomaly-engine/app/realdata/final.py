"""
The two final checks (M2 runbook, Days 7, 10 and 11).

evaluate    Score captures with the FROZEN reference and its alert line, with
            no re-learning, and print the results table the runbook asks for
            (run, scenario, ground truth, detected, score, detection delay,
            prediction) plus precision, recall, F1 and the false-alarm rate.

            This is only an independent test on a capture the reference was
            NOT built from. A capture listed in the reference's `trained_on`
            is in-sample: the alert line was placed in the gap between its
            normal and its faults, so it will look perfect. The command says
            so when that is the case.

live-check  Compare what the RUNNING M2 service decided against the drill's
            answer key: for every injected fault, when did M2 open an incident
            candidate, how long after the fault started, and how many alerts
            and signals did it take? Any candidate that matches no fault is
            counted as a false alarm. This is the runbook's Day-7 flagship
            check: stable detection, with no threshold edited between runs.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..evaluation.metrics import RunResult, prepare_all, result_from_scores, summarize
from ..evaluation.scenarios import Run
from ..reference import Reference
from .capture import Capture, capture_to_runs, load_capture

_EPS = 1e-9


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def evaluate_frozen(runs: list[Run], reference: Reference) -> list[RunResult]:
    cutoff = reference.alert.score - _EPS
    results = []
    for prepared in prepare_all(runs):
        service = prepared.run.service
        if service not in reference.services:
            raise ValueError(f"the reference has no entry for service {service!r}")
        scores = reference.detector.score_many(service, prepared.full).tolist()
        results.append(result_from_scores(prepared.run, scores, cutoff))
    return results


def format_evaluation(results: list[RunResult]) -> str:
    s = summarize(results)
    lines = [
        f"{'run':30}{'scenario':16}{'truth':11}{'detected':10}{'top score':>10}{'delay':>8}  prediction",
    ]
    for r in results:
        delay = "-" if r.detection_delay_s is None else f"{r.detection_delay_s:.0f}s"
        lines.append(
            f"{r.run_id:30}{r.scenario:16}{'anomalous' if r.ground_truth else 'normal':11}"
            f"{str(r.detected).lower():10}{r.score:>10.3f}{delay:>8}  {'anomalous' if r.prediction else 'normal'}"
        )
    lines += [
        "",
        f"precision {s.precision:.3f}   recall {s.recall:.3f}   F1 {s.f1:.3f}",
        f"healthy runs falsely flagged: {s.fp} of {s.fp + s.tn}",
        f"normal readings falsely flagged: {s.false_alert_snapshots} of {s.normal_snapshots}",
    ]
    if s.delay_mean_s is not None:
        lines.append(
            f"detection delay: mean {s.delay_mean_s:.0f}s, median {s.delay_median_s:.0f}s, worst {s.delay_max_s:.0f}s"
        )
    for scenario, recall in s.recall_by_scenario.items():
        lines.append(f"  {scenario}: detected {100 * recall:.0f}%")
    return "\n".join(lines)


def run_evaluate(capture_paths: list[str], reference: Reference) -> tuple[list[RunResult], str]:
    runs: list[Run] = []
    warnings: list[str] = []
    for name in capture_paths:
        capture = load_capture(name)
        if capture.points is None:
            raise ValueError(f"{name} has no readings yet; run: python -m app.realdata fetch {name}")
        found, notes = capture_to_runs(capture)
        warnings += [f"{Path(name).name}: {n}" for n in notes]
        if Path(name).name in reference.trained_on:
            warnings.append(
                f"{Path(name).name} was used to build this reference, so its results are IN-SAMPLE "
                "(the alert line was placed in the gap between its normal and its faults). "
                "Use a capture the reference has not seen for an independent test."
            )
        multiple = len(capture_paths) > 1
        runs += [
            Run(f"{Path(name).stem}:{r.run_id}" if multiple else r.run_id, r.service, r.scenario, r.fault_start, r.snapshots)
            for r in found
        ]
    results = evaluate_frozen(runs, reference)
    text = format_evaluation(results)
    if warnings:
        text = "NOTES\n  " + "\n  ".join(warnings) + "\n\n" + text
    return results, text


# ---------------------------------------------------------------------------
# live-check
# ---------------------------------------------------------------------------

class LiveCheckError(Exception):
    pass


def _get_json(url: str, timeout: float = 10.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LiveCheckError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LiveCheckError(f"could not reach M2 at {url}: {getattr(exc, 'reason', exc)}") from None
    except json.JSONDecodeError:
        raise LiveCheckError(f"{url} did not return JSON") from None


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@dataclass(frozen=True)
class FaultOutcome:
    run_id: str
    scenario: str
    fault_start: datetime
    incident_id: str | None
    delay_s: float | None
    alert_count: int | None
    signals: list[str]
    peak_score: float | None
    extra_candidates: int            # further candidates for the same fault (should be 0)


@dataclass(frozen=True)
class LiveCheck:
    faults: list[FaultOutcome]
    false_alarms: list[tuple[str, datetime]]     # (incident id, started_at) matching no fault


def live_check(capture: Capture, m2_url: str, tolerance_s: float = 30.0, settle_s: float = 120.0) -> LiveCheck:
    """Match M2's incident candidates with the faults the drill injected.

    A candidate belongs to a fault if it started between `tolerance_s` before
    the fault and `settle_s` after the fault's run ended (M1's one-minute
    windows keep showing a fault a while after it stops).
    """
    base = m2_url.rstrip("/")
    listed = _get_json(f"{base}/internal/correlation/incidents?limit=200")
    start, end = capture.span()
    in_span = sorted(
        (i for i in listed if capture.service in i.get("affected_services", [])
         and start - timedelta(seconds=tolerance_s) <= _when(i["started_at"]) <= end + timedelta(seconds=settle_s)),
        key=lambda i: _when(i["started_at"]),
    )

    claimed: set[str] = set()
    faults: list[FaultOutcome] = []
    for spec in capture.runs:
        if spec.fault_start is None:
            continue
        window = [
            i for i in in_span
            if spec.fault_start - timedelta(seconds=tolerance_s) <= _when(i["started_at"])
            <= spec.end + timedelta(seconds=settle_s)
        ]
        if not window:
            faults.append(FaultOutcome(spec.run_id, spec.scenario, spec.fault_start, None, None, None, [], None, 0))
            continue
        first = window[0]
        claimed.update(i["incident_id"] for i in window)
        detail = _get_json(f"{base}/internal/correlation/incidents/{first['incident_id']}")
        faults.append(
            FaultOutcome(
                spec.run_id, spec.scenario, spec.fault_start, first["incident_id"],
                (_when(first["started_at"]) - spec.fault_start).total_seconds(),
                detail.get("alert_count"), detail.get("signals", []), detail.get("peak_score"),
                len(window) - 1,
            )
        )

    false_alarms = [(i["incident_id"], _when(i["started_at"])) for i in in_span if i["incident_id"] not in claimed]
    return LiveCheck(faults, false_alarms)


def format_live_check(result: LiveCheck) -> str:
    lines = [f"{'fault':22}{'fault started':>15}{'M2 opened':>12}{'delay':>8}{'alerts':>8}  signals"]
    for f in result.faults:
        if f.incident_id is None:
            lines.append(f"{f.run_id:22}{f.fault_start:%H:%M:%S}{'':>7}{'MISSED':>12}")
            continue
        opened = f.fault_start + timedelta(seconds=f.delay_s)
        extra = f"   (+{f.extra_candidates} extra candidate(s): one fault should be one incident)" if f.extra_candidates else ""
        lines.append(
            f"{f.run_id:22}{f.fault_start:%H:%M:%S}{'':>7}{opened:%H:%M:%S}{'':>4}{f.delay_s:>7.0f}s{f.alert_count:>8}  "
            f"{', '.join(f.signals) or '-'}{extra}"
        )
    caught = sum(1 for f in result.faults if f.incident_id is not None)
    lines.append(f"\nfaults caught: {caught} of {len(result.faults)}")
    delays = [f.delay_s for f in result.faults if f.delay_s is not None]
    if delays:
        lines.append(f"delay after the fault began: " + ", ".join(f"{d:.0f}s" for d in delays))
    if result.false_alarms:
        lines.append(f"FALSE ALARMS: {len(result.false_alarms)} incident candidate(s) matched no fault:")
        lines += [f"  {i} at {t:%H:%M:%S}" for i, t in result.false_alarms]
    else:
        lines.append("false alarms: none. Every incident candidate belongs to an injected fault.")
    return "\n".join(lines)
