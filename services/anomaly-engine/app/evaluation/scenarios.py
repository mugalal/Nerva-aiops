"""
Synthetic telemetry for the three Day-2 scenarios (M2 runbook).

    healthy          normal behaviour, plus the occasional harmless blip
    bad_deployment   latency and 5xx jump while request_rate stays flat
    traffic_spike    request_rate, CPU and latency all rise together

These are my assumptions about what such telemetry looks like, not
measurements. They are good for proving the pipeline works and for comparing
one detector against another on identical data. They say little about how a
detector will do on real traffic; Day 3 replaces them with M1's real windows.

Everything is drawn from a seeded `random.Random`, so a seed always produces
exactly the same run. Only random(), gauss() and basic arithmetic are used,
because those give identical output across Python versions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from shared.contracts import Metrics, TelemetrySnapshot

INTERVAL_S = 15                 # seconds between snapshots
RUN_LENGTH = 60                 # snapshots per run (15 minutes)
FAULT_START_RANGE = (20, 35)    # the fault begins at a snapshot in this range
BLIP_PROB = 0.005               # chance a healthy snapshot has a one-off latency blip
BLIP_RANGE = (1.5, 2.2)         # how much a blip multiplies latency by
BASE_TIME = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)

SCENARIOS = ("healthy", "bad_deployment", "traffic_spike")


@dataclass(frozen=True)
class ServiceProfile:
    """What 'normal' looks like for one service: (mean, std) per metric."""

    name: str
    latency_p95_ms: tuple[float, float]
    request_rate: tuple[float, float]
    cpu: tuple[float, float]
    memory: tuple[float, float]
    http_5xx_rate: tuple[float, float]
    replica_count: int


# Four services with deliberately different "normal", so a single fixed
# threshold cannot suit all of them. search-service is slow even when healthy.
PROFILES = (
    ServiceProfile("payment-service", (120, 12), (100, 8), (0.30, 0.04), (0.45, 0.02), (0.002, 0.0015), 3),
    ServiceProfile("checkout-service", (250, 25), (60, 6), (0.40, 0.05), (0.55, 0.03), (0.003, 0.002), 2),
    ServiceProfile("search-service", (420, 40), (220, 20), (0.55, 0.06), (0.60, 0.03), (0.004, 0.002), 4),
    ServiceProfile("auth-service", (60, 6), (150, 12), (0.25, 0.03), (0.35, 0.02), (0.001, 0.001), 2),
)


@dataclass(frozen=True)
class Run:
    """One simulated stretch of telemetry for one service, with the answer key."""

    run_id: str
    service: str
    scenario: str
    fault_start: int | None            # snapshot index where the fault begins; None = healthy
    snapshots: tuple[TelemetrySnapshot, ...]

    @property
    def anomalous(self) -> bool:
        return self.fault_start is not None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _uniform(rng: random.Random, low: float, high: float) -> float:
    return low + (high - low) * rng.random()


def generate_run(profile: ServiceProfile, scenario: str, seed: int, run_id: str | None = None) -> Run:
    """Build one run. Same (profile, scenario, seed) always gives the same run."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")

    rng = random.Random(seed)

    lo, hi = FAULT_START_RANGE
    fault_start = None if scenario == "healthy" else lo + int(rng.random() * (hi - lo + 1))

    # How severe this particular fault is. Drawn for every scenario so the
    # random stream has the same shape regardless of which one is used.
    latency_mult = _uniform(rng, 1.6, 6.0)      # bad_deployment: latency x1.6 .. x6
    error_add = _uniform(rng, 0.02, 0.18)       # bad_deployment: 5xx rate +2% .. +18%
    cpu_add = _uniform(rng, 0.0, 0.25)          # bad_deployment: retries burn some CPU
    spike_mult = _uniform(rng, 2.0, 4.5)        # traffic_spike: traffic x2 .. x4.5
    ramp = 2 if scenario == "bad_deployment" else 4   # snapshots to reach full effect

    snapshots = []
    for i in range(RUN_LENGTH):
        latency = rng.gauss(*profile.latency_p95_ms)
        request_rate = rng.gauss(*profile.request_rate)
        cpu = rng.gauss(*profile.cpu)
        memory = rng.gauss(*profile.memory)
        errors = rng.gauss(*profile.http_5xx_rate)
        blip_roll = rng.random()
        blip_size = _uniform(rng, *BLIP_RANGE)

        in_fault = fault_start is not None and i >= fault_start
        version = "v1"

        if in_fault:
            progress = min(1.0, (i - fault_start + 1) / ramp)
            if scenario == "bad_deployment":
                version = "v2"
                latency *= 1 + (latency_mult - 1) * progress
                errors += error_add * progress
                cpu += cpu_add * progress
            else:  # traffic_spike
                request_rate *= 1 + (spike_mult - 1) * progress
                cpu += 0.12 * (spike_mult - 1) * progress
                latency *= 1 + 0.3 * (spike_mult - 1) * progress
                errors += 0.004 * (spike_mult - 1) * progress
        elif blip_roll < BLIP_PROB:
            latency *= blip_size

        snapshots.append(
            TelemetrySnapshot(
                timestamp=BASE_TIME + timedelta(seconds=INTERVAL_S * i),
                service=profile.name,
                version=version,
                metrics=Metrics(
                    cpu=round(_clip(cpu, 0.0, 0.99), 4),
                    memory=round(_clip(memory, 0.0, 0.99), 4),
                    request_rate=round(max(0.0, request_rate), 2),
                    latency_p95_ms=round(max(1.0, latency), 2),
                    http_5xx_rate=round(_clip(errors, 0.0, 1.0), 5),
                    replica_count=profile.replica_count,
                ),
            )
        )

    return Run(
        run_id=run_id or f"{profile.name}/{scenario}/{seed}",
        service=profile.name,
        scenario=scenario,
        fault_start=fault_start,
        snapshots=tuple(snapshots),
    )


def generate_dataset(base_seed: int, per_cell: int) -> list[Run]:
    """`per_cell` runs for every (service, scenario) pair.

    Different `base_seed` values give independent datasets from the same
    generator, which is how tuning data and held-out test data are kept apart.
    """
    runs = []
    for service_index, profile in enumerate(PROFILES):
        for scenario_index, scenario in enumerate(SCENARIOS):
            for k in range(per_cell):
                seed = base_seed * 1_000_003 + service_index * 10_007 + scenario_index * 1_009 + k
                runs.append(
                    generate_run(
                        profile,
                        scenario,
                        seed,
                        run_id=f"s{base_seed}-{profile.name}-{scenario}-{k:02d}",
                    )
                )
    return runs
