"""
Day 3 from the command line. Run from services/anomaly-engine:

    python -m app.realdata drill  --stack-dir ~/Nerva-m1      # ~30 min, makes real faults
    python -m app.realdata fetch  capture.json                # re-fetch M1's history only
    python -m app.realdata report capture.json [more.json ...]
"""

from __future__ import annotations

import argparse
import dataclasses
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
from .m1_client import DEFAULT_M1_URL, M1Client, M1Error
from .report import build_report


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
    capture = load_capture(path)
    return 0 if _fetch_and_save(capture, path, M1Client(args.m1_url or capture.m1_url)) else 1


def cmd_report(args: argparse.Namespace) -> int:
    runs = []
    for name in args.captures:
        path = Path(name)
        capture = load_capture(path)
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
