"""Export experiment_runs to CSV and JSON. MTTD/MTTR are computed from timestamps.

Definitions (not specified by the project, chosen by M6):
  mttd_seconds  = detection_time - injection_time
  mttr_seconds  = recovery_time  - detection_time
  total_impact_seconds = recovery_time - injection_time
"""
import argparse
import csv
import json
from pathlib import Path

FIELDS = [
    "scenario", "run_id", "incident_id", "injection_time", "detection_time",
    "rca_correct", "action_time", "recovery_time",
    "mttd_seconds", "mttr_seconds", "total_impact_seconds", "cost_slo_effect",
]


def _seconds(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _iso(value):
    return value.isoformat() if value is not None else None


def build_rows(records: list[dict]) -> list[dict]:
    rows = []
    for r in records:
        rows.append({
            "scenario": r["scenario"],
            "run_id": r["run_id"],
            "incident_id": r.get("incident_id"),
            "injection_time": _iso(r.get("injection_time")),
            "detection_time": _iso(r.get("detection_time")),
            "rca_correct": r.get("rca_correct"),
            "action_time": _iso(r.get("action_time")),
            "recovery_time": _iso(r.get("recovery_time")),
            "mttd_seconds": _seconds(r.get("injection_time"), r.get("detection_time")),
            "mttr_seconds": _seconds(r.get("detection_time"), r.get("recovery_time")),
            "total_impact_seconds": _seconds(r.get("injection_time"), r.get("recovery_time")),
            "cost_slo_effect": json.dumps(r.get("cost_slo_effect")),
        })
    return rows


def write_files(rows: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "experiments.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "experiments.json").write_text(json.dumps(rows, indent=2))


def main() -> None:
    from .repository import get_records  # imported here so build_rows needs no database

    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="exports")
    args = parser.parse_args()
    rows = build_rows(get_records("experiment_runs"))
    write_files(rows, Path(args.out_dir))
    print(f"exported {len(rows)} runs to {args.out_dir}")


if __name__ == "__main__":
    main()