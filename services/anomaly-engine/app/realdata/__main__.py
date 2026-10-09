"""
Day 3 from the command line. Run from services/anomaly-engine:

    python -m app.realdata drill  --stack-dir ~/Nerva-m1      # ~30 min, makes real faults
    python -m app.realdata fetch  capture.json                # re-fetch M1's history only
    python -m app.realdata report capture.json [more.json ...]
    python -m app.realdata train  capture1.json capture2.json   # build the frozen reference + alert line
    python -m app.realdata evaluate capture3.json               # the results table, frozen reference
    python -m app.realdata live-check capture3.json             # compare the RUNNING M2 with the drill
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .capture import capture_to_runs, load_capture, save_capture
from .drill import (
    PAYMENT_URL,
    Drill,
    DrillError,
    DrillPlan,
    ThreadedTraffic,
    fetch_points,
    http_pay,
    http_version,
    shell_command,
)
from ..evaluation.run import write_day11_table
from ..reference import load_reference
from .final import LiveCheckError, format_live_check, live_check, run_evaluate
from .m1_client import DEFAULT_M1_URL, M1Client, M1Error
from .report import build_report
from .train import TrainError, train_reference


def _read_capture(name: str):
    """Load a capture, or explain plainly why not. Returns None on failure."""
    try:
        return load_capture(name)
    except FileNotFoundError:
        print(f"Cannot find the capture file {name!r}. Check the name, and that you are in services/anomaly-engine.")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"{name!r} is not a usable capture file: {exc}")
    return None


def _fetch_and_save(capture, path: Path, client: M1Client) -> bool:
    try:
        fetch_points(capture, client)
    except M1Error as exc:
        print(f"\nCould not get the history from M1: {exc}")
        print(f"Your drill is saved. Once M1 is healthy, retry with:\n  python -m app.realdata fetch {path}")
        return False
    save_capture(path, capture)
    print(f"Saved {len(capture.points)} readings and the answer key to {path}")
    return True


def cmd_drill(args: argparse.Namespace) -> int:
    stack_dir = Path(args.stack_dir).expanduser().resolve()
    if not (stack_dir / "observability" / "docker-compose.yml").exists():
        print(
            f"{stack_dir} does not contain observability/docker-compose.yml.\n"
            "That folder has to show M1's version of the repo. Create one with:\n"
            "  git worktree add ~/Nerva-m1 origin/m1/prometheus-integration"
        )
        return 2

    plan = DrillPlan.quick() if args.quick else DrillPlan(healthy_s=args.healthy_min * 60)
    client = M1Client(args.m1_url)
    drill = Drill(
        plan=plan,
        traffic=ThreadedTraffic(http_pay(args.payment_url)),
        run_command=shell_command(str(stack_dir)),
        get_version=http_version(args.payment_url),
        m1_url=args.m1_url,
    )

    print("Checking the stack is up...")
    try:
        drill.preflight(client.health)
    except (DrillError, M1Error) as exc:
        print(f"\n{exc}")
        return 2

    if not args.skip_build:
        print("Building the faulty version now, so the drill never waits on the network later...")
        try:
            drill.build_faulty_image()
        except DrillError as exc:
            print(f"\n{exc}")
            return 2

    minutes = plan.total_s() / 60
    print(
        f"\nThe drill takes about {minutes:.0f} minutes.\n"
        "It will send steady traffic, swap payment-service to a faulty v2 and back, then raise the load.\n"
        "KEEP THE LAPTOP PLUGGED IN AND AWAKE: if it sleeps, the readings get a hole in them and the\n"
        "drill stops. Leave this window open. Ctrl+C is safe: it puts payment-service back to healthy."
    )
    if not args.yes and input("Start? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1

    try:
        capture = drill.run()
    except DrillError as exc:
        print(f"\nDrill stopped: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped by you. payment-service has been put back to v1.")
        return 1

    out = Path(args.out)
    save_capture(out, capture)                      # keep the answer key even if M1 fails next
    print(f"\nDrill finished. Answer key saved to {out}. Fetching M1's history...")
    return 0 if _fetch_and_save(capture, out, client) else 1


def cmd_fetch(args: argparse.Namespace) -> int:
    path = Path(args.capture)
    capture = _read_capture(args.capture)
    if capture is None:
        return 2
    return 0 if _fetch_and_save(capture, path, M1Client(args.m1_url or capture.m1_url)) else 1


def cmd_report(args: argparse.Namespace) -> int:
    runs = []
    for name in args.captures:
        path = Path(name)
        capture = _read_capture(name)
        if capture is None:
            return 2
        if capture.points is None:
            print(f"{path} has no readings yet. Run:  python -m app.realdata fetch {path}")
            return 2
        found, notes = capture_to_runs(capture)
        for note in notes:
            print(f"note: {note}")
        multiple = len(args.captures) > 1
        runs.extend(
            dataclasses.replace(r, run_id=f"{path.stem}:{r.run_id}") if multiple else r for r in found
        )

    if not runs:
        print("No usable runs.")
        return 2
    print(build_report(runs))
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    try:
        reference, text = train_reference(args.captures, args.out, args.min_samples)
    except FileNotFoundError as exc:
        print(f"Could not train: cannot find {exc.filename!r}. Check the name, and that you are in services/anomaly-engine.")
        return 2
    except (TrainError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Could not train: {exc}")
        return 2
    print(text)
    return 0 if reference is not None else 2


def cmd_evaluate(args: argparse.Namespace) -> int:
    try:
        reference = load_reference(args.reference)
        results, text = run_evaluate(args.captures, reference)
    except FileNotFoundError as exc:
        print(f"Could not evaluate: cannot find {exc.filename!r}. Check the name, and that you are in services/anomaly-engine.")
        return 2
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Could not evaluate: {exc}")
        return 2
    print(text)
    if args.csv:
        write_day11_table(args.csv, results)
        print(f"\nWrote the results table to {args.csv}")
    return 0


def cmd_live_check(args: argparse.Namespace) -> int:
    capture = _read_capture(args.capture)
    if capture is None:
        return 2
    try:
        result = live_check(capture, args.m2_url)
    except LiveCheckError as exc:
        print(f"Could not check: {exc}")
        return 2
    print(format_live_check(result))
    caught_all = all(f.incident_id is not None for f in result.faults)
    return 0 if caught_all and not result.false_alarms else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.realdata", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    drill = sub.add_parser("drill", help="make real faults on M1's demo service and record when they start")
    drill.add_argument("--stack-dir", default=".", help="folder showing M1's repo (needs observability/docker-compose.yml)")
    drill.add_argument("--out", default="capture.json")
    drill.add_argument("--m1-url", default=DEFAULT_M1_URL)
    drill.add_argument("--payment-url", default=PAYMENT_URL)
    drill.add_argument("--healthy-min", type=int, default=10, help="minutes of healthy traffic (default 10)")
    drill.add_argument("--quick", action="store_true", help="short run to check the plumbing (results not meaningful)")
    drill.add_argument("--skip-build", action="store_true",
                       help="don't build the faulty version first (only if it is already built)")
    drill.add_argument("--yes", action="store_true", help="don't ask before starting")
    drill.set_defaults(func=cmd_drill)

    fetch = sub.add_parser("fetch", help="get M1's history for an existing capture")
    fetch.add_argument("capture")
    fetch.add_argument("--m1-url")
    fetch.set_defaults(func=cmd_fetch)

    report = sub.add_parser("report", help="how the detectors do on a capture")
    report.add_argument("captures", nargs="+")
    report.set_defaults(func=cmd_report)

    train = sub.add_parser("train", help="build the frozen reference and choose the alert line from captures")
    train.add_argument("captures", nargs="+")
    train.add_argument("--out", default="reference/reference.json")
    train.add_argument("--min-samples", type=int, default=20)
    train.set_defaults(func=cmd_train)

    evaluate = sub.add_parser("evaluate", help="the results table, scored with the frozen reference")
    evaluate.add_argument("captures", nargs="+")
    evaluate.add_argument("--reference", default="reference/reference.json")
    evaluate.add_argument("--csv", help="also write the table as a CSV file")
    evaluate.set_defaults(func=cmd_evaluate)

    live = sub.add_parser("live-check", help="compare the running M2 service with the drill's answer key")
    live.add_argument("capture")
    live.add_argument("--m2-url", default="http://localhost:8002")
    live.set_defaults(func=cmd_live_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
