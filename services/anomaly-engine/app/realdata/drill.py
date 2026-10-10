"""
The drill (M2 runbook, Day 3).

Makes real healthy, bad-deployment and traffic-spike behaviour happen on M1's
demo `payment-service`, and writes down exactly when each fault began, so the
readings M1 reports can be judged against the truth.

    warm-up   steady traffic, readings ignored (M1's rates need a minute of data)
    healthy   steady traffic
    bad deployment   swap payment-service to the faulty v2 (+600 ms, 14% errors),
                     hold, swap back to v1
    recovery  steady traffic while the 1-minute windows clear
    traffic spike    many more workers sending traffic, hold, back to normal

The exact moment a fault starts is not guessed. Right after the swap command
returns, the demo app's /version endpoint is polled; the first time it answers
"v2" is when the faulty code started serving, because /version and /pay are
served by the same process.

If the drill is stopped part-way through the bad deployment (Ctrl+C, an error),
it puts payment-service back to healthy v1 before exiting.

Every outside effect (traffic, shell commands, the demo's /version, the clock)
is passed in, so the whole timeline can be tested without Docker.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from .capture import Capture, RunSpec
from .m1_client import DEFAULT_M1_URL, M1Client, M1Error

SERVICE = "payment-service"
PAYMENT_URL = "http://localhost:8000"

# From M1's README ("repeatable bad-deployment drill"), using `docker compose`.
# The faulty image is built BEFORE the timeline starts (BUILD_FAULTY_COMMAND),
# not during it: building needs the internet and a lot of CPU, and neither
# belongs in the middle of the healthy readings. The swap itself is then fast.
BUILD_FAULTY_COMMAND = [
    "docker", "compose",
    "-f", "observability/docker-compose.yml",
    "-f", "observability/docker-compose.faulty.yml",
    "build", "payment-service",
]
FAULT_COMMAND = [
    "docker", "compose",
    "-f", "observability/docker-compose.yml",
    "-f", "observability/docker-compose.faulty.yml",
    "up", "-d", "payment-service",
]
RESTORE_COMMAND = [
    "docker", "compose",
    "-f", "observability/docker-compose.yml",
    "up", "-d", "--force-recreate", "payment-service",
]


# A wait that takes this many seconds longer on the clock than it should means
# the computer slept or paused: the clock jumped but nothing was being measured.
SLEEP_JUMP_TOLERANCE_S = 20
SLEEP_CHUNK_S = 10

NETWORK_HINT = (
    "This looks like a network (DNS) problem inside WSL, common after the laptop sleeps.\n"
    "  1. Quit Docker Desktop (tray icon, right-click, Quit).\n"
    "  2. In PowerShell:  wsl --shutdown\n"
    "  3. Start Docker Desktop, wait for 'Engine running', then open Ubuntu again.\n"
    "  4. Check the name resolves:  getent hosts auth.docker.io"
)


class DrillError(Exception):
    pass


@dataclass(frozen=True)
class DrillPlan:
    """How long each stretch lasts, in seconds, and how hard the traffic is."""

    warmup_s: int = 90
    healthy_s: int = 600
    fault_s: int = 300
    recovery_s: int = 300
    spike_s: int = 240
    tail_s: int = 90
    context_s: int = 180        # healthy readings kept before each fault
    settle_s: int = 90          # after a fault ends, M1's 1-minute windows still show it
    base_workers: int = 5
    spike_workers: int = 30
    version_timeout_s: int = 240
    poll_s: float = 1.0
    step_seconds: int = 15

    def __post_init__(self) -> None:
        if self.healthy_s < self.context_s:
            raise ValueError("healthy_s must be at least context_s, or the bad deployment has no healthy lead-in")
        if self.recovery_s < self.settle_s + self.context_s:
            raise ValueError("recovery_s must be at least settle_s + context_s, or the spike has no healthy lead-in")
        if self.spike_workers <= self.base_workers:
            raise ValueError("spike_workers must be more than base_workers")

    @classmethod
    def quick(cls) -> "DrillPlan":
        """A short run to check the plumbing. Too short for meaningful results:
        M1's windows are a minute long."""
        return cls(warmup_s=45, healthy_s=120, fault_s=120, recovery_s=150, spike_s=90,
                   tail_s=30, context_s=60, settle_s=60)

    def total_s(self) -> int:
        return (self.warmup_s + self.healthy_s + self.fault_s + self.recovery_s
                + self.spike_s + self.tail_s)


# ---------------------------------------------------------------------------
# The real outside world (replaced by fakes in tests)
# ---------------------------------------------------------------------------

def http_pay(base_url: str = PAYMENT_URL, timeout: float = 10.0) -> Callable[[], None]:
    """One POST /pay. Connection errors are swallowed: during a swap the demo
    app is briefly down, and that is part of the drill, not a failure."""
    url = f"{base_url.rstrip('/')}/pay"

    def pay() -> None:
        request = urllib.request.Request(
            url, data=b"{}", method="POST", headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
        except Exception:  # noqa: BLE001 - a 500 or a refused connection is expected here
            pass

    return pay


def http_version(base_url: str = PAYMENT_URL, timeout: float = 3.0) -> Callable[[], str | None]:
    """Which version of payment-service is answering right now (None = nothing)."""
    url = f"{base_url.rstrip('/')}/version"

    def version() -> str | None:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8")).get("version")
        except Exception:  # noqa: BLE001
            return None

    return version


def shell_command(cwd: str, timeout: int = 1800) -> Callable[[list[str]], tuple[bool, str]]:
    def run(command: list[str]) -> tuple[bool, str]:
        try:
            done = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return False, f"{command[0]!r} was not found on this machine"
        except subprocess.TimeoutExpired:
            return False, "the command took too long"
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        return done.returncode == 0, " | ".join(tail)

    return run


class ThreadedTraffic:
    """Steady load: N workers each sending one request, pausing, repeating."""

    def __init__(self, pay: Callable[[], None], delay_s: float = 0.05) -> None:
        self._pay = pay
        self._delay_s = delay_s
        self._workers: list[tuple[threading.Thread, threading.Event]] = []

    def _loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._pay()
            stop.wait(self._delay_s)

    def set_concurrency(self, workers: int) -> None:
        while len(self._workers) < workers:
            stop = threading.Event()
            thread = threading.Thread(target=self._loop, args=(stop,), daemon=True)
            thread.start()
            self._workers.append((thread, stop))
        while len(self._workers) > workers:
            thread, stop = self._workers.pop()
            stop.set()

    def stop(self) -> None:
        self.set_concurrency(0)


# ---------------------------------------------------------------------------
# The drill
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class Drill:
    def __init__(
        self,
        plan: DrillPlan,
        traffic,
        run_command: Callable[[list[str]], tuple[bool, str]],
        get_version: Callable[[], str | None],
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        say: Callable[[str], None] = print,
        service: str = SERVICE,
        m1_url: str = DEFAULT_M1_URL,
    ) -> None:
        self.plan = plan
        self.traffic = traffic
        self.run_command = run_command
        self.get_version = get_version
        self.now = now
        self.sleep = sleep
        self.say = say
        self.service = service
        self.m1_url = m1_url
        self._events: list[dict] = []
        self._fault_active = False

    # -- helpers ----------------------------------------------------------

    def _event(self, name: str, at: datetime | None = None, **extra) -> None:
        self._events.append({"at": _iso(at or self.now()), "name": name, **extra})

    def _phase(self, label: str, seconds: int) -> None:
        """Wait `seconds`, in short chunks, stopping if the computer slept.

        If the laptop sleeps, the demo and Prometheus are paused with it: the
        readings from that stretch have a hole in them. The clock jumps on
        wake-up though, so a chunk that took far longer than it should is the
        sign. The drill stops (and restores v1 if a fault was live) rather than
        carry on and produce data that looks fine but isn't.
        """
        self.say(f"[{self.now():%H:%M:%S}] {label}: {seconds}s")
        remaining = float(seconds)
        while remaining > 0:
            chunk = min(float(SLEEP_CHUNK_S), remaining)
            before = self.now()
            self.sleep(chunk)
            took = (self.now() - before).total_seconds()
            if took - chunk > SLEEP_JUMP_TOLERANCE_S:
                raise DrillError(
                    f"the clock jumped {took - chunk:.0f}s during a {chunk:.0f}s wait at {before:%H:%M:%S}: "
                    "the computer probably went to sleep. Readings from that time have a gap, so this "
                    "run can't be trusted. Stop the laptop sleeping (plugged in, sleep set to Never) "
                    "and run the drill again."
                )
            remaining -= chunk

    def _wait_for_version(self, wanted: str) -> datetime | None:
        deadline = self.now() + timedelta(seconds=self.plan.version_timeout_s)
        while self.now() < deadline:
            seen_at = self.now()
            if self.get_version() == wanted:
                return seen_at
            self.sleep(self.plan.poll_s)
        return None

    def _switch(self, command: list[str], wanted: str) -> datetime:
        """Run a swap command, then find the exact moment `wanted` goes live."""
        self.say(f"  running: {' '.join(command)}")
        ok, output = self.run_command(command)
        if not ok:
            self.say(
                f"  could not run it automatically ({output}).\n"
                f"  Run this yourself in another terminal, from the stack folder:\n"
                f"    {' '.join(command)}\n"
                f"  Waiting up to {self.plan.version_timeout_s}s for payment-service to report {wanted}..."
            )
        moment = self._wait_for_version(wanted)
        if moment is None:
            raise DrillError(
                f"payment-service never reported version {wanted} within {self.plan.version_timeout_s}s"
            )
        return moment

    # -- checks before starting -------------------------------------------

    def preflight(self, m1_health: Callable[[], dict] | None = None) -> None:
        version = self.get_version()
        if version is None:
            raise DrillError(
                "cannot reach payment-service. Start M1's stack first:\n"
                "  docker compose -f observability/docker-compose.yml up -d --build"
            )
        if version != "v1":
            raise DrillError(
                f"payment-service is running {version}, not the healthy v1. Put it back first:\n"
                f"  {' '.join(RESTORE_COMMAND)}"
            )
        if m1_health is not None:
            status = m1_health().get("status")
            if status == "unavailable":
                raise DrillError("M1's telemetry service reports 'unavailable'; it can't serve history")
            self.say(f"  M1 telemetry service: {status}")
        self.say(f"  payment-service: {version}")

    def build_faulty_image(self) -> None:
        """Build the faulty v2 image now, so the swap later needs no network."""
        self.say(f"  building the faulty version: {' '.join(BUILD_FAULTY_COMMAND)}")
        ok, output = self.run_command(BUILD_FAULTY_COMMAND)
        if not ok:
            hint = ""
            if any(word in output.lower() for word in ("lookup", "i/o timeout", "no such host", "dial tcp")):
                hint = f"\n{NETWORK_HINT}"
            raise DrillError(f"could not build the faulty version: {output}{hint}")

    # -- the timeline -----------------------------------------------------

    def run(self) -> Capture:
        plan = self.plan
        self._event("drill_start")
        try:
            self.traffic.set_concurrency(plan.base_workers)
            self._phase("warm-up (readings ignored)", plan.warmup_s)

            healthy_start = self.now()
            self._event("healthy_start", at=healthy_start)
            self._phase("healthy", plan.healthy_s)
            healthy_end = self.now()
            self._event("healthy_end", at=healthy_end)

            # -- bad deployment
            self._fault_active = True          # set first: the command may half-work
            fault_start = self._switch(FAULT_COMMAND, "v2")
            self._event("bad_deployment_start", at=fault_start)
            self._phase("bad deployment (faulty v2 is live)", plan.fault_s)
            restored = self._switch(RESTORE_COMMAND, "v1")
            self._fault_active = False
            self._event("bad_deployment_end", at=restored)
            self._phase("recovery (healthy v1 again)", plan.recovery_s)

            # -- traffic spike
            spike_start = self.now()
            self.traffic.set_concurrency(plan.spike_workers)
            self._event("traffic_spike_start", at=spike_start, workers=plan.spike_workers)
            self._phase("traffic spike", plan.spike_s)
            spike_end = self.now()
            self.traffic.set_concurrency(plan.base_workers)
            self._event("traffic_spike_end", at=spike_end)
            self._phase("tail", plan.tail_s)
            self._event("drill_end")
        finally:
            self.traffic.stop()
            if self._fault_active:
                self.say("Stopping early: putting payment-service back to healthy v1...")
                ok, output = self.run_command(RESTORE_COMMAND)
                if not ok:
                    self.say(f"  could not do it automatically ({output}). Run:\n    {' '.join(RESTORE_COMMAND)}")

        lead_in = timedelta(seconds=plan.context_s)
        runs = [
            RunSpec("healthy-1", "healthy", healthy_start, healthy_end, None),
            RunSpec("bad_deployment-1", "bad_deployment", fault_start - lead_in, restored, fault_start),
            RunSpec("traffic_spike-1", "traffic_spike", spike_start - lead_in, spike_end, spike_start),
        ]
        return Capture(
            service=self.service,
            step_seconds=plan.step_seconds,
            m1_url=self.m1_url,
            recorded_at=self.now(),
            events=self._events,
            runs=runs,
        )


def fetch_points(capture: Capture, client: M1Client) -> Capture:
    """Read verified history without combining different release versions.

    M1 intentionally rejects a window crossing a rollout, including its metric
    lookback. Split that window on the same sampling grid. Ambiguous individual
    readings remain absent and are recorded; unavailable providers still fail
    the capture. Never extend the request beyond the measured drill.
    """
    start, end = capture.span()
    if capture.step_seconds < 5 or start >= end or end > capture.recorded_at:
        raise ValueError("capture needs a completed positive window and a valid sampling step")
    # M1Client's wire timestamps have whole-second precision.
    start, end = start.replace(microsecond=0), end.replace(microsecond=0)
    step = timedelta(seconds=capture.step_seconds)
    count = int((end - start).total_seconds() // capture.step_seconds) + 1
    omitted = []

    def read(first: int, last: int):
        begin, finish = start + first * step, start + last * step
        # M1 requires start < end. A one-second range has exactly one grid point.
        query_end = finish if last > first else min(
            begin + timedelta(seconds=1), end
        )
        if query_end <= begin:
            # The final boundary has no positive interval inside the capture.
            omitted.append({"at": _iso(begin), "name": "history_point_unavailable",
                            "reason": "final boundary cannot form a positive history interval"})
            return []
        try:
            return client.window(capture.service, begin, query_end, capture.step_seconds)
        except M1Error as exc:
            message = exc.message.lower()
            mixed = exc.category == "telemetry_missing" and (
                "mixed versions" in message or "version is inconsistent" in message
            )
            if not mixed:
                raise
            if first == last:
                omitted.append({"at": _iso(begin), "name": "history_point_unavailable",
                                "category": exc.category, "reason": exc.message})
                return []
            middle = (first + last) // 2
            return read(first, middle) + read(middle + 1, last)

    points = read(0, count - 1)
    if not points:
        raise M1Error("No unambiguous history survived the rollout boundaries", category="no_data")
    capture.points = sorted(points, key=lambda point: point.timestamp)
    capture.events.extend(omitted)
    return capture
