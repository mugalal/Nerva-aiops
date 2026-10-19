"""The recovery deadline scales with the size of a scale-out."""
import pytest

from app import config
from app.orchestration.orchestrator import _recovery_budget


def _incident(action, replicas, before):
    return {"_execution_intent": {"action": action, "replicas": replicas},
            "_expected_state": {"replicas": before, "version": "v1"}}


def test_scale_out_adds_time_per_added_replica():
    budget = _recovery_budget(_incident("SCALE", 7, 1))
    assert budget == pytest.approx(config.RECOVERY_TIMEOUT_SECONDS + 6 * config.RECOVERY_SECONDS_PER_ADDED_REPLICA)


@pytest.mark.parametrize("incident", [
    _incident("ROLLBACK", None, 1),
    _incident("SCALE", 1, 3),          # a scale-down adds no pods to wait for
    {"_execution_intent": {"action": "SCALE", "replicas": 4}},  # no captured state
])
def test_other_actions_keep_the_base_deadline(incident):
    assert _recovery_budget(incident) == pytest.approx(config.RECOVERY_TIMEOUT_SECONDS)


def test_deadline_is_capped_and_setting_is_validated(monkeypatch):
    monkeypatch.setattr(config, "RECOVERY_SECONDS_PER_ADDED_REPLICA", 300)
    assert config.recovery_timeout_seconds(50) == 3600
    for bad in ("-1", "301", "nan", "x"):
        with pytest.raises(ValueError):
            config.load_recovery_seconds_per_replica(bad)
