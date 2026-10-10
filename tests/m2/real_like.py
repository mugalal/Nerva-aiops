"""
Readings shaped like the REAL drill data, for tests (not real data).

The numbers come from what M1 reported during the two real drills: very flat
healthy traffic (about 85 requests a second, 10 ms latency, an error rate that
is always exactly 0, CPU near 0.24, memory frozen), a bad deployment that
drops the request rate and raises latency and errors, and a traffic spike that
raises the request rate and CPU but not latency. Faults arrive over about a
minute (four 15-second readings) because M1 averages over a minute.

`fault_scale` shrinks the faults towards nothing, to build data a detector
could NOT separate from normal.
"""

import random
from datetime import datetime, timedelta, timezone

import m2_loader

from shared.contracts import Metrics, TelemetrySnapshot

capture_mod = m2_loader.module("realdata.capture")
features_mod = m2_loader.module("features")

T0 = datetime(2026, 10, 7, 9, 0, 0, tzinfo=timezone.utc)
SERVICE = "payment-service"


def snapshot(t, kind="none", progress=0.0, noise=1.0, rng=None, service=SERVICE, version="v1", fault_scale=1.0):
    rng = rng or random.Random(0)
    rr = rng.gauss(85, 0.4 * noise)
    latency = rng.gauss(10.0, 0.19 * noise)
    cpu = rng.gauss(0.242, 0.009 * noise)
    errors = 0.0
    k = progress * fault_scale
    if kind == "bad":
        rr += (13 - 85) * k
        latency += 680 * k
        errors = 0.125 * k
        cpu += (0.047 - 0.242) * k
    elif kind == "spike":
        rr += 410 * k
        cpu += 0.32 * k
    return TelemetrySnapshot(
        timestamp=t, service=service, version=version,
        metrics=Metrics(cpu=round(cpu, 4), memory=0.0917, request_rate=round(rr, 2),
                        latency_p95_ms=round(latency, 2), http_5xx_rate=round(max(errors, 0.0), 5),
                        replica_count=1),
    )


def normal_rows(n, seed=0, service=SERVICE):
    """n healthy readings as the 8-feature vectors the detectors learn from."""
    rng = random.Random(seed)
    rows, previous = [], None
    for i in range(n):
        snap = snapshot(T0 + timedelta(seconds=15 * i), rng=rng, service=service)
        rows.append(features_mod.extract_features(snap, previous).as_vector())
        previous = snap
    return rows


def make_capture(seed, noise_bad=1.0, fault_scale=1.0, with_points=True):
    """A capture laid out like a real drill: a healthy run, a bad-deployment
    run and a traffic-spike run, with the answer key."""
    rng = random.Random(seed)
    plan = [
        ("healthy-1", "healthy", 40, 0, "none", 1.0),
        ("bad_deployment-1", "bad_deployment", 12, 20, "bad", noise_bad),
        ("traffic_spike-1", "traffic_spike", 12, 16, "spike", 1.0),
    ]
    points, specs, cursor = [], [], T0
    for run_id, scenario, n_normal, n_fault, kind, noise in plan:
        run = []
        for i in range(n_normal + n_fault):
            progress = 0.0 if i < n_normal else min(1.0, (i - n_normal + 1) / 4)
            run.append(snapshot(cursor + timedelta(seconds=15 * i), kind if i >= n_normal else "none",
                                progress, noise, rng, fault_scale=fault_scale))
        points += run
        specs.append(capture_mod.RunSpec(
            run_id, scenario, run[0].timestamp, run[-1].timestamp,
            None if n_fault == 0 else run[n_normal].timestamp))
        cursor = run[-1].timestamp + timedelta(seconds=600)       # longer than the 5-minute change-feature limit
    return capture_mod.Capture(SERVICE, 15, "http://localhost:8001", cursor, [], specs,
                               points if with_points else None)
