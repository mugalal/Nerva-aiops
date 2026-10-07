"""
Tests for the Day-2 evaluation tools: scenario generator, metrics, tuner,
results table, and that the service and the evaluation score identically.

Run from the repo root:   python -m pytest tests/m2 -v
"""

import csv
from statistics import mean

import m2_loader
import pytest
from fastapi.testclient import TestClient

baseline_mod = m2_loader.module("baseline")
main_mod = m2_loader.module("main")
scenarios = m2_loader.module("evaluation.scenarios")
metrics = m2_loader.module("evaluation.metrics")
tune_mod = m2_loader.module("evaluation.tune")
run_mod = m2_loader.module("evaluation.run")

BaselineConfig = baseline_mod.BaselineConfig


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def test_same_seed_gives_the_same_run():
    profile = scenarios.PROFILES[0]
    a = scenarios.generate_run(profile, "bad_deployment", seed=7)
    b = scenarios.generate_run(profile, "bad_deployment", seed=7)
    assert a.snapshots == b.snapshots and a.fault_start == b.fault_start


def test_different_seeds_give_different_runs():
    profile = scenarios.PROFILES[0]
    a = scenarios.generate_run(profile, "healthy", seed=1)
    b = scenarios.generate_run(profile, "healthy", seed=2)
    assert a.snapshots != b.snapshots


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        scenarios.generate_run(scenarios.PROFILES[0], "meltdown", seed=1)


def test_dataset_has_one_run_per_cell_times_per_cell_count_with_unique_ids():
    runs = scenarios.generate_dataset(base_seed=5, per_cell=3)
    assert len(runs) == len(scenarios.PROFILES) * len(scenarios.SCENARIOS) * 3
    assert len({r.run_id for r in runs}) == len(runs)


def test_different_base_seeds_give_independent_datasets():
    a = scenarios.generate_dataset(base_seed=1, per_cell=1)
    b = scenarios.generate_dataset(base_seed=2, per_cell=1)
    assert [r.snapshots for r in a] != [r.snapshots for r in b]


@pytest.fixture(scope="module")
def dataset():
    return scenarios.generate_dataset(base_seed=11, per_cell=8)


def test_answer_key_matches_scenario(dataset):
    lo, hi = scenarios.FAULT_START_RANGE
    for run in dataset:
        assert len(run.snapshots) == scenarios.RUN_LENGTH
        if run.scenario == "healthy":
            assert run.fault_start is None and not run.anomalous
        else:
            assert run.anomalous and lo <= run.fault_start <= hi


def test_snapshots_are_evenly_spaced_and_sane(dataset):
    for run in dataset:
        times = [s.timestamp for s in run.snapshots]
        gaps = {(b - a).total_seconds() for a, b in zip(times, times[1:])}
        assert gaps == {scenarios.INTERVAL_S}
        for s in run.snapshots:
            assert 0.0 <= s.metrics.cpu <= 1.0
            assert 0.0 <= s.metrics.http_5xx_rate <= 1.0
            assert s.metrics.latency_p95_ms > 0 and s.metrics.request_rate >= 0


def _before_after(run, field):
    """Average of a metric before the fault vs after it has fully ramped up."""
    f = run.fault_start
    before = mean(getattr(s.metrics, field) for s in run.snapshots[:f])
    after = mean(getattr(s.metrics, field) for s in run.snapshots[f + 4:])
    return before, after


def test_bad_deployment_signature_latency_up_traffic_flat_version_changes(dataset):
    runs = [r for r in dataset if r.scenario == "bad_deployment"]
    rate = mean(b and a / b for b, a in (_before_after(r, "request_rate") for r in runs))
    latency = mean(a / b for b, a in (_before_after(r, "latency_p95_ms") for r in runs))
    errors = mean(a - b for b, a in (_before_after(r, "http_5xx_rate") for r in runs))
    assert 0.9 < rate < 1.1          # traffic does not explain it
    assert latency > 1.5
    assert errors > 0.02
    for r in runs:
        assert r.snapshots[r.fault_start - 1].version == "v1"
        assert r.snapshots[r.fault_start].version == "v2"


def test_traffic_spike_signature_traffic_cpu_latency_up_no_deployment(dataset):
    runs = [r for r in dataset if r.scenario == "traffic_spike"]
    rate = mean(a / b for b, a in (_before_after(r, "request_rate") for r in runs))
    cpu = mean(a / b for b, a in (_before_after(r, "cpu") for r in runs))
    latency = mean(a / b for b, a in (_before_after(r, "latency_p95_ms") for r in runs))
    assert rate > 2.0 and cpu > 1.2 and latency > 1.2
    for r in runs:
        assert {s.version for s in r.snapshots} == {"v1"}


def test_healthy_runs_have_no_sustained_shift(dataset):
    for run in (r for r in dataset if r.scenario == "healthy"):
        first = mean(s.metrics.latency_p95_ms for s in run.snapshots[:30])
        second = mean(s.metrics.latency_p95_ms for s in run.snapshots[30:])
        assert 0.8 < second / first < 1.25


# ---------------------------------------------------------------------------
# Metrics, checked by hand on tiny runs
# ---------------------------------------------------------------------------

# Only the latency rule is on (weight 1.5, total 1.5), so a breach scores 1.0.
LATENCY_ONLY = BaselineConfig(
    latency_p95_ms=500.0, http_5xx_rate=None, cpu=None, memory=None,
    request_rate_change=None, latency_change=None, alert_score=0.5,
)


def _prepared(scenario, fault_start, latencies):
    base = scenarios.generate_run(scenarios.PROFILES[0], "healthy", seed=1)
    run = scenarios.Run(
        run_id=f"hand-{scenario}", service=base.service, scenario=scenario,
        fault_start=fault_start, snapshots=base.snapshots[: len(latencies)],
    )
    values = tuple((lat, 0.0, 0.0, 0.0, 0.0, 0.0) for lat in latencies)
    return metrics.PreparedRun(run=run, values=values)


def test_detection_delay_is_seconds_from_fault_start_to_first_alert():
    # fault at index 3, first alert at index 5 -> 2 snapshots -> 30 s
    result = metrics.evaluate_prepared(
        _prepared("bad_deployment", 3, [100, 100, 100, 100, 100, 600, 600]), LATENCY_ONLY)
    assert result.detected and result.prediction
    assert result.detection_delay_s == 2 * scenarios.INTERVAL_S
    assert result.false_alert_snapshots == 0 and result.normal_snapshots == 3
    assert result.score == 1.0


def test_an_alert_before_the_fault_is_a_false_alarm_not_a_detection():
    result = metrics.evaluate_prepared(
        _prepared("bad_deployment", 4, [100, 600, 100, 100, 100, 100, 100]), LATENCY_ONLY)
    assert not result.detected and not result.prediction
    assert result.detection_delay_s is None
    assert result.false_alert_snapshots == 1 and result.normal_snapshots == 4


def test_a_healthy_run_with_any_alert_counts_as_a_false_alarm():
    result = metrics.evaluate_prepared(
        _prepared("healthy", None, [100, 100, 600, 100]), LATENCY_ONLY)
    assert result.prediction and not result.ground_truth
    assert result.false_alert_snapshots == 1 and result.normal_snapshots == 4


def test_summary_counts_and_rates():
    results = metrics.evaluate(
        [
            _prepared("bad_deployment", 3, [100, 100, 100, 600, 600]),    # hit
            _prepared("traffic_spike", 3, [100, 100, 100, 100, 100]),     # miss
            _prepared("healthy", None, [100, 600, 100, 100, 100]),        # false alarm
            _prepared("healthy", None, [100, 100, 100, 100, 100]),        # correct
        ],
        LATENCY_ONLY,
    )
    s = metrics.summarize(results)
    assert (s.tp, s.fn, s.fp, s.tn) == (1, 1, 1, 1)
    assert s.precision == 0.5 and s.recall == 0.5 and s.f1 == 0.5
    assert s.run_fpr == 0.5
    assert (s.false_alert_snapshots, s.normal_snapshots) == (1, 3 + 3 + 5 + 5)
    assert s.recall_by_scenario == {"bad_deployment": 1.0, "traffic_spike": 0.0}
    assert s.delay_mean_s == 0.0


def test_summary_handles_no_predictions_without_dividing_by_zero():
    s = metrics.summarize(
        metrics.evaluate([_prepared("healthy", None, [100, 100])], LATENCY_ONLY))
    assert s.precision == 0.0 and s.recall == 0.0 and s.f1 == 0.0
    assert s.delay_mean_s is None


# ---------------------------------------------------------------------------
# The service and the evaluation must score every snapshot identically
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", scenarios.SCENARIOS)
def test_service_scores_match_evaluation_scores(scenario):
    run = scenarios.generate_run(scenarios.PROFILES[2], scenario, seed=99)
    thresholds, total = baseline_mod.DEFAULT_CONFIG.compiled()
    expected = [baseline_mod.score_values(v, thresholds, total)
                for v in metrics.prepare(run).values]

    main_mod.reset_state()
    client = TestClient(main_mod.app)
    served = [
        client.post("/internal/anomalies/evaluate",
                    json=snap.model_dump(mode="json")).json()["score"]
        for snap in run.snapshots
    ]
    assert served == expected


# ---------------------------------------------------------------------------
# Tuner
# ---------------------------------------------------------------------------

SMALL_GRID = {
    "latency_p95_ms": [300.0, 800.0],
    "http_5xx_rate": [0.02, 0.08],
    "cpu": [0.75, 0.92],
    "request_rate_change": [None, 0.6],
    "latency_change": [None, 2.0],
    "alert_score": [0.1, 0.3],
}


@pytest.fixture(scope="module")
def tiny_prepared():
    return metrics.prepare_all(scenarios.generate_dataset(base_seed=3, per_cell=2))


def test_tuner_picks_a_config_from_the_grid_that_fits_the_budget(tiny_prepared):
    result = tune_mod.tune(tiny_prepared, max_fpr=0.01, grid=SMALL_GRID)
    assert result.configs_tried == 2 ** 6
    assert result.best_config is not None
    assert result.best_summary.snapshot_fpr <= 0.01
    assert result.best_config.latency_p95_ms in SMALL_GRID["latency_p95_ms"]
    assert result.best_config.alert_score in SMALL_GRID["alert_score"]


def test_tuner_reports_no_winner_when_nothing_fits_the_budget(tiny_prepared):
    result = tune_mod.tune(tiny_prepared, max_fpr=-1.0, grid=SMALL_GRID)
    assert result.best_config is None and result.configs_within_budget == 0


def test_absolute_only_search_pins_the_change_rules_off(tiny_prepared):
    result = tune_mod.tune(tiny_prepared, max_fpr=0.01, grid=SMALL_GRID,
                           allow_change_rules=False)
    assert result.best_config.request_rate_change is None
    assert result.best_config.latency_change is None
    assert result.configs_tried == 2 ** 4


# ---------------------------------------------------------------------------
# Results table and command line
# ---------------------------------------------------------------------------

def test_day11_table_has_the_required_columns(tmp_path):
    prepared = metrics.prepare_all(scenarios.generate_dataset(base_seed=4, per_cell=1))
    results = metrics.evaluate(prepared, baseline_mod.DEFAULT_CONFIG)
    path = tmp_path / "table.csv"
    run_mod.write_day11_table(str(path), results)

    rows = list(csv.DictReader(path.open()))
    assert list(rows[0]) == ["run", "scenario", "ground_truth", "detected",
                             "score", "detection_delay", "prediction"]
    assert len(rows) == len(results)
    assert {r["ground_truth"] for r in rows} <= {"normal", "anomalous"}
    for r in rows:
        if r["detected"] == "false":
            assert r["detection_delay"] == ""


def test_command_line_runs_and_prints_the_comparison(capsys):
    run_mod.main(["--per-cell", "1"])
    out = capsys.readouterr().out
    assert "HELD-OUT" in out and "placeholder" in out and "current" in out


# ---------------------------------------------------------------------------
# Guard rails on the frozen config (held-out synthetic data, seed 2000)
# ---------------------------------------------------------------------------

def test_default_config_beats_the_placeholder_and_stays_inside_the_false_alarm_budget():
    held_out = metrics.prepare_all(scenarios.generate_dataset(2000, per_cell=10))
    tuned = metrics.summarize(metrics.evaluate(held_out, baseline_mod.DEFAULT_CONFIG))
    placeholder = metrics.summarize(metrics.evaluate(held_out, baseline_mod.PLACEHOLDER_CONFIG))

    assert tuned.f1 > placeholder.f1
    assert tuned.snapshot_fpr <= 0.002
    assert tuned.recall_by_scenario["bad_deployment"] >= 0.95
    assert tuned.f1 >= 0.82   # measured 0.871; slack for harmless numeric drift
