"""
Tests for the Day-4 detectors (Isolation Forest, z-score ruler) and the
comparison harness.

These need numpy and scikit-learn. If they aren't installed the whole file is
skipped (not failed):  pip install scikit-learn numpy

Run from the repo root:   python -m pytest tests/m2 -v
"""

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

features_mod = m2_loader.module("features")
model_mod = m2_loader.module("model")
scenarios = m2_loader.module("evaluation.scenarios")
metrics = m2_loader.module("evaluation.metrics")
compare = m2_loader.module("evaluation.compare")

FEATURE_ORDER = features_mod.FEATURE_ORDER
SERVICE = "payment-service"


def _index(name: str) -> int:
    return FEATURE_ORDER.index(name)


@pytest.fixture(scope="module")
def prepared():
    return metrics.prepare_all(scenarios.generate_dataset(base_seed=21, per_cell=3))


@pytest.fixture(scope="module")
def healthy(prepared):
    return compare.normal_training_matrices(prepared)


@pytest.fixture(scope="module")
def forest(healthy):
    return model_mod.IsolationForestDetector().fit(healthy)


@pytest.fixture(scope="module")
def ruler(healthy):
    return model_mod.ZScoreDetector().fit(healthy)


def _abnormal(healthy_rows):
    """A typical healthy reading, pushed a long way out on three features."""
    vector = np.mean(healthy_rows, axis=0)
    vector[_index("request_rate")] *= 4
    vector[_index("latency_p95_ms")] *= 6
    vector[_index("http_5xx_rate")] += 0.15
    return vector


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------

def test_feature_order_is_the_eight_frozen_features():
    assert FEATURE_ORDER == (
        "request_rate", "request_rate_change", "latency_p95_ms", "latency_change",
        "http_5xx_rate", "cpu", "memory", "replica_count",
    )


def test_prepared_runs_carry_all_eight_features(prepared):
    for p in prepared[:5]:
        assert len(p.full) == len(p.run.snapshots)
        assert all(len(row) == 8 for row in p.full)


# ---------------------------------------------------------------------------
# What the models are allowed to learn from
# ---------------------------------------------------------------------------

def test_training_data_never_includes_a_fault_reading(prepared, healthy):
    expected = {}
    for p in prepared:
        stop = len(p.full) if p.run.fault_start is None else p.run.fault_start
        expected[p.run.service] = expected.get(p.run.service, 0) + stop
    assert {s: len(rows) for s, rows in healthy.items()} == expected


def test_every_service_gets_its_own_training_rows(healthy):
    assert set(healthy) == {p.name for p in scenarios.PROFILES}


# ---------------------------------------------------------------------------
# Isolation Forest
# ---------------------------------------------------------------------------

def test_forest_scores_stay_between_0_and_1(forest, healthy):
    scores = forest.score_many(SERVICE, healthy[SERVICE])
    assert scores.min() >= 0.0 and scores.max() <= 1.0


def test_forest_scores_an_abnormal_reading_higher_than_a_typical_one(forest, healthy):
    typical = forest.score(SERVICE, np.mean(healthy[SERVICE], axis=0))
    abnormal = forest.score(SERVICE, _abnormal(healthy[SERVICE]))
    assert abnormal > typical + 0.1


def test_forest_is_repeatable_with_the_same_random_state(healthy):
    a = model_mod.IsolationForestDetector(n_estimators=40, random_state=3).fit(healthy)
    b = model_mod.IsolationForestDetector(n_estimators=40, random_state=3).fit(healthy)
    rows = healthy[SERVICE][:20]
    assert list(a.score_many(SERVICE, rows)) == list(b.score_many(SERVICE, rows))


def test_per_service_forest_rejects_a_service_it_has_no_history_for(forest, healthy):
    with pytest.raises(model_mod.UnknownServiceError):
        forest.score("brand-new-service", healthy[SERVICE][0])


def test_pooled_forest_accepts_any_service_name(healthy):
    pooled = model_mod.IsolationForestDetector(per_service=False).fit(healthy)
    assert 0.0 <= pooled.score("brand-new-service", healthy[SERVICE][0]) <= 1.0
    assert pooled.services == [model_mod.POOLED_KEY]


# ---------------------------------------------------------------------------
# z-score ruler
# ---------------------------------------------------------------------------

def _toy_history():
    """Feature 0 swings +-1, feature 1 never moves, feature 2 swings +-2."""
    rows = [[(-1) ** i, 7.0, 2.0 * (-1) ** i] for i in range(100)]
    return {"toy": rows}


def test_ruler_distance_is_measured_in_usual_wobbles():
    history = _toy_history()
    ruler = model_mod.ZScoreDetector().fit(history)
    wobble = np.std([r[0] for r in history["toy"]], ddof=1)
    assert ruler.distance_many("toy", [[0.0, 7.0, 0.0]])[0] == pytest.approx(0.0)
    assert ruler.distance_many("toy", [[5.0, 7.0, 0.0]])[0] == pytest.approx(5.0 / wobble)


def test_ruler_uses_the_worst_feature():
    ruler = model_mod.ZScoreDetector().fit(_toy_history())
    one = ruler.distance_many("toy", [[3.0, 7.0, 0.0]])[0]
    both = ruler.distance_many("toy", [[3.0, 7.0, 8.0]])[0]   # feature 2 is further out
    assert both > one


def test_a_feature_that_never_varied_is_still_measured_not_skipped():
    """Healthy history where one feature is always exactly 7 (like an error
    rate that is always 0). If that feature moves, it must count."""
    ruler = model_mod.ZScoreDetector().fit(_toy_history())
    unchanged = ruler.distance_many("toy", [[0.0, 7.0, 0.0]])[0]
    moved = ruler.distance_many("toy", [[0.0, 8.0, 0.0]])[0]
    assert unchanged == pytest.approx(0.0)
    assert moved > 10


def test_a_flat_zero_feature_still_has_a_scale_so_errors_register():
    rows = [[100.0 + (i % 5), 0.0] for i in range(60)]   # latency wobbles, error rate always 0
    ruler = model_mod.ZScoreDetector().fit({"svc": rows})
    assert ruler.distance_many("svc", [[102.0, 0.0]])[0] < 3
    assert ruler.distance_many("svc", [[102.0, 0.14]])[0] > 50


def test_ruler_score_is_squashed_into_0_to_1_and_invertible(ruler, healthy):
    scores = ruler.score_many(SERVICE, [_abnormal(healthy[SERVICE])])
    assert 0.0 < scores[0] < 1.0
    distance = ruler.distance_many(SERVICE, [_abnormal(healthy[SERVICE])])[0]
    assert model_mod.ZScoreDetector.wobbles_of(float(scores[0])) == pytest.approx(distance)


def test_ruler_scores_an_abnormal_reading_far_above_a_typical_one(ruler, healthy):
    typical = ruler.score(SERVICE, np.mean(healthy[SERVICE], axis=0))
    abnormal = ruler.score(SERVICE, _abnormal(healthy[SERVICE]))
    assert typical < 0.2 and abnormal > 0.6


def test_ruler_rejects_an_unknown_service(ruler, healthy):
    with pytest.raises(model_mod.UnknownServiceError):
        ruler.score("brand-new-service", healthy[SERVICE][0])


# ---------------------------------------------------------------------------
# Both: missing data and too little history
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["forest", "ruler"])
def test_missing_data_scores_zero_and_never_an_alarm(which, forest, ruler, healthy):
    detector = {"forest": forest, "ruler": ruler}[which]
    good = _abnormal(healthy[SERVICE])
    bad = good.copy()
    bad[_index("latency_p95_ms")] = float("nan")
    scores = detector.score_many(SERVICE, [good, bad])
    assert scores[1] == 0.0
    assert scores[0] > 0.0          # the usable row next to it is still scored


@pytest.mark.parametrize("make", [model_mod.IsolationForestDetector, model_mod.ZScoreDetector])
def test_too_little_healthy_history_is_refused(make):
    few = {"new-service": [[1.0] * 8] * (model_mod.MIN_TRAINING_SAMPLES - 1)}
    with pytest.raises(ValueError, match="not enough healthy history"):
        make().fit(few)


@pytest.mark.parametrize("make", [model_mod.IsolationForestDetector, model_mod.ZScoreDetector])
def test_fitting_on_nothing_is_refused(make):
    with pytest.raises(ValueError):
        make().fit({})


# ---------------------------------------------------------------------------
# Choosing the alert line
# ---------------------------------------------------------------------------

def test_cutoff_choice_stays_inside_the_false_alarm_budget(prepared, ruler):
    scores = compare.score_runs(ruler, prepared)
    choice = compare.choose_cutoff(prepared, scores, max_fpr=0.01)
    assert choice.cutoff is not None
    assert choice.summary.snapshot_fpr <= 0.01


def test_a_smaller_budget_never_gives_a_lower_alert_line(prepared, ruler):
    scores = compare.score_runs(ruler, prepared)
    loose = compare.choose_cutoff(prepared, scores, max_fpr=0.05).cutoff
    tight = compare.choose_cutoff(prepared, scores, max_fpr=0.002).cutoff
    assert tight >= loose


def test_cutoff_choice_reports_no_winner_when_nothing_fits(prepared, ruler):
    choice = compare.choose_cutoff(prepared, compare.score_runs(ruler, prepared), max_fpr=-1.0)
    assert choice.cutoff is None and choice.summary is None


def test_misses_are_counted_by_service_and_scenario(prepared):
    results = compare.judge(prepared, [np.zeros(len(p.full)) for p in prepared], cutoff=0.5)
    misses = compare.misses_by_group(prepared, results)
    assert sum(misses.values()) == sum(1 for p in prepared if p.run.anomalous)
    assert all(scenario != "healthy" for _, scenario in misses)


# ---------------------------------------------------------------------------
# The comparison command
# ---------------------------------------------------------------------------

def test_comparison_command_prints_all_four_detectors(capsys):
    compare.main(["--per-cell", "2"])
    out = capsys.readouterr().out
    for name in ("baseline", "z-score per-service", "forest per-service", "forest pooled"):
        assert name in out
    assert "HELD-OUT" in out and "usual wobbles" in out


def test_comparison_command_retunes_the_baseline_for_other_fault_sizes(capsys, monkeypatch):
    tune_mod = m2_loader.module("evaluation.tune")
    small_grid = {
        "latency_p95_ms": [300.0, 800.0], "http_5xx_rate": [0.02, 0.08], "cpu": [0.75, 0.92],
        "request_rate_change": [None, 0.6], "latency_change": [None, 2.0], "alert_score": [0.1, 0.3],
    }
    calls = []

    def small_tune(runs, max_fpr):
        calls.append(max_fpr)
        return tune_mod.tune(runs, max_fpr, grid=small_grid)

    monkeypatch.setattr(compare, "tune", small_tune)
    compare.main(["--per-cell", "1", "--severity", "0.5"])
    assert "re-tuning the baseline" in capsys.readouterr().out
    assert calls == [0.002]


def test_comparison_command_does_not_retune_the_baseline_at_standard_fault_size(capsys, monkeypatch):
    monkeypatch.setattr(compare, "tune", lambda *a, **k: pytest.fail("baseline must not be re-tuned"))
    compare.main(["--per-cell", "1"])
    assert "re-tuning" not in capsys.readouterr().out
