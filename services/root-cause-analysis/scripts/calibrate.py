"""Fit M3's confidence calibration on labelled real captures, and evaluate it on held-out ones.

    cd services/root-cause-analysis
    python scripts/calibrate.py            # writes app/calibration.json and prints the report

Data: the real kind-cluster captures in docs/evidence/m2 (M1 readings plus the exact time each fault began).
  training  capture1, capture2 (payment-service), capture4, capture5 (checkout-service)
  held out  capture3 (payment-service), capture6 (checkout-service): never used for fitting

For every reading, each cause's raw evidence score is computed exactly as M3 computes it (scoring.raw_score)
from usual deviations. For TRAINING readings the per-service normal is learned from the OTHER training
captures of that service only (leave one capture out), so no reading is judged by statistics that saw it.
HELD-OUT readings use the frozen M2 reference, as the deployed system does. In both, the learned noise shape
is anchored at the capture's own measured operating point (its first six healthy readings, like M1's baseline
window), exactly as M3 does with M1's baseline; those baseline readings are not scored.

Labels: a reading after a fault of that cause began (including the first readings, where M1's one-minute
windows only partly show it) is positive; every healthy reading is negative. M3's rule gates (a release
within five minutes, no recent release for traffic, ...) decide WHETHER a cause is considered; this fit
answers how strongly the evidence then indicates a real fault rather than normal behaviour.

Model: P(cause | raw) = sigmoid(a * raw + b), ridge-regularised logistic regression (stable even when the
classes separate perfectly, which they nearly do here).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
SERVICE = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(SERVICE))

from app.deviation import (MIN_ABSOLUTE_SPREAD, MIN_RELATIVE_SPREAD, SIGNALS, Normal,  # noqa: E402
                           Reference, anchored, deviations)
from app.scoring import FAULTY_DEPLOYMENT, TRAFFIC_SPIKE, raw_score  # noqa: E402

CAPTURES = REPO / "docs" / "evidence" / "m2"
TRAINING = ["capture1.json", "capture2.json", "capture4-checkout.json", "capture5-checkout.json"]
HELD_OUT = ["capture3.json", "capture6-checkout.json"]
SCENARIO = {"bad_deployment": FAULTY_DEPLOYMENT, "traffic_spike": TRAFFIC_SPIKE}
RIDGE = 1.0


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def readings(name: str) -> list[dict]:
    """Each reading once, labelled healthy or with the cause whose fault was under way."""
    capture = json.loads((CAPTURES / name).read_text(encoding="utf-8"))
    labelled = {}
    for point in capture["points"]:
        moment = _time(point["timestamp"])
        label = None
        for run in capture["runs"]:
            start, end = _time(run["start"]), _time(run["end"])
            if not start <= moment <= end:
                continue
            fault = _time(run["fault_start"]) if run["fault_start"] else None
            if fault is not None and moment >= fault:
                label = SCENARIO[run["scenario"]]
            elif label is None:
                label = "healthy"
        if label is not None:
            labelled[point["timestamp"]] = {"service": capture["service"], "metrics": point["metrics"], "label": label}
    return list(labelled.values())


def learned_normal(rows: list[dict]) -> Normal:
    """M2's statistic on healthy rows: mean and sample std per signal, load per replica, M2's floors."""
    mean, spread = {}, {}
    for name in SIGNALS:
        values = [r["metrics"][name] / (max(1.0, r["metrics"]["replica_count"]) if name == "request_rate" else 1.0)
                  for r in rows]
        mean[name] = statistics.fmean(values)
        spread[name] = max(statistics.stdev(values), MIN_RELATIVE_SPREAD * abs(mean[name]), MIN_ABSOLUTE_SPREAD)
    return Normal(mean, spread, "learned", len(rows))


BASELINE_READINGS = 6   # like M1's 75 s baseline window at a 15 s step


def operating_point(rows: list[dict]) -> tuple[dict[str, float], list[dict]]:
    """M1's measured operating point: the first healthy readings, which are then not scored themselves."""
    window = [r for r in rows if r["label"] == "healthy"][:BASELINE_READINGS]
    point = {name: statistics.fmean(r["metrics"][name] / (max(1.0, r["metrics"]["replica_count"])
                                                         if name == "request_rate" else 1.0) for r in window)
             for name in SIGNALS}
    return point, [r for r in rows if not any(r is w for w in window)]


def scored(rows: list[dict], learned: Normal) -> list[dict]:
    """Deviations exactly as deployed: learned noise anchored at this capture's measured operating point."""
    point, rows = operating_point(rows)
    normal = anchored(learned, point, BASELINE_READINGS)
    out = []
    for row in rows:
        d = deviations(normal, {name: row["metrics"][name] for name in SIGNALS}, row["metrics"]["replica_count"])
        out.append({**row, "raw": {cause: raw_score(cause, d) for cause in (FAULTY_DEPLOYMENT, TRAFFIC_SPIKE)}})
    return out


def dataset(rows: list[dict], cause: str) -> list[tuple[float, int]]:
    return [(r["raw"][cause], 1 if r["label"] == cause else 0) for r in rows if r["label"] in (cause, "healthy")]


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit(points: list[tuple[float, int]]) -> tuple[float, float]:
    """Ridge logistic regression by Newton's method (2 parameters; no external dependencies)."""
    a, b = 1.0, 0.0
    for _ in range(100):
        ga, gb = RIDGE * a, 0.0
        haa, hab, hbb = RIDGE, 0.0, 1e-6
        for x, y in points:
            p = sigmoid(a * x + b)
            ga += (p - y) * x
            gb += (p - y)
            w = p * (1 - p)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        step = max(abs(da), abs(db))
        if step > 2.0:                       # damped Newton: never jump far on near-separable data
            da, db = da * 2.0 / step, db * 2.0 / step
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-10:
            break
    return a, b


def evaluate(points: list[tuple[float, int]], a: float, b: float) -> dict:
    probabilities = [(sigmoid(a * x + b), y) for x, y in points]
    brier = statistics.fmean((p - y) ** 2 for p, y in probabilities)
    correct = sum((p >= 0.5) == bool(y) for p, y in probabilities)
    above = [y for p, y in probabilities if p >= 0.6]
    bins = []
    for low in (0.0, 0.2, 0.4, 0.6, 0.8):
        inside = [(p, y) for p, y in probabilities if low <= p < low + 0.2 or (low == 0.8 and p == 1.0)]
        if inside:
            bins.append({"predicted": f"{low:.1f}-{low + 0.2:.1f}", "readings": len(inside),
                         "mean_predicted": round(statistics.fmean(p for p, _ in inside), 3),
                         "observed_rate": round(statistics.fmean(y for _, y in inside), 3)})
    return {"readings": len(points), "faulty_readings": sum(y for _, y in points),
            "brier": round(brier, 4), "accuracy_at_0_5": round(correct / len(points), 4),
            "precision_at_0_6": round(statistics.fmean(above), 4) if above else None,
            "recall_at_0_6": round(sum(above) / max(1, sum(y for _, y in points)), 4), "reliability": bins}


def main() -> int:
    training_rows = {name: readings(name) for name in TRAINING}
    loco = []
    for name, rows in training_rows.items():
        service = rows[0]["service"]
        others = [r for other, other_rows in training_rows.items() if other != name
                  for r in other_rows if r["service"] == service and r["label"] == "healthy"]
        loco += scored(rows, learned_normal(others))
    reference = Reference(REPO / "services" / "anomaly-engine" / "reference" / "reference.json")
    held_out = []
    for name in HELD_OUT:
        rows = readings(name)
        held_out += scored(rows, reference.normal(rows[0]["service"]))

    result = {"model": "P(cause | raw) = 1 / (1 + exp(-(a * raw + b)))", "ridge": RIDGE,
              "training_captures": TRAINING, "held_out_captures": HELD_OUT,
              "labels": "positive: readings after a fault of that cause began; negative: healthy readings",
              "causes": {}}
    print(f"{'cause':<18}{'set':<10}{'readings':>9}{'faulty':>8}{'brier':>8}{'acc@0.5':>9}{'prec@0.6':>10}{'rec@0.6':>9}")
    for cause in (FAULTY_DEPLOYMENT, TRAFFIC_SPIKE):
        train = dataset(loco, cause)
        a, b = fit(train)
        result["causes"][cause] = {"a": round(a, 6), "b": round(b, 6),
                                   "training": evaluate(train, a, b),
                                   "held_out": evaluate(dataset(held_out, cause), a, b)}
        for label, report in (("training", result["causes"][cause]["training"]),
                              ("held-out", result["causes"][cause]["held_out"])):
            print(f"{cause:<18}{label:<10}{report['readings']:>9}{report['faulty_readings']:>8}{report['brier']:>8}"
                  f"{report['accuracy_at_0_5']:>9}{str(report['precision_at_0_6']):>10}{report['recall_at_0_6']:>9}")
        print(f"{'':<18}a={a:.3f} b={b:.3f}; held-out reliability: "
              + "; ".join(f"{x['predicted']}: {x['observed_rate']} of {x['readings']}" for x in result["causes"][cause]["held_out"]["reliability"]))
    (SERVICE / "app" / "calibration.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("wrote app/calibration.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
