import sys
from datetime import datetime, timezone
from pathlib import Path

# tests -> finops-engine -> services
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared-nexus-api" / "app"))

from db.export_experiments import build_rows  # noqa: E402


def t(minute, second):
    return datetime(2026, 10, 7, 10, minute, second, tzinfo=timezone.utc)


def test_mttd_and_mttr_come_from_timestamps():
    rows = build_rows([{
        "scenario": "bad_deployment", "run_id": "RUN-1", "incident_id": "INC-1",
        "injection_time": t(0, 0), "detection_time": t(0, 42),
        "rca_correct": True, "action_time": t(1, 0), "recovery_time": t(1, 29),
        "cost_slo_effect": {"slo_restored": True},
    }])
    assert rows[0]["mttd_seconds"] == 42
    assert rows[0]["mttr_seconds"] == 47
    assert rows[0]["total_impact_seconds"] == 89


def test_missing_timestamps_give_none_not_fake_numbers():
    rows = build_rows([{"scenario": "traffic_spike", "run_id": "RUN-2",
                        "injection_time": t(0, 0)}])
    assert rows[0]["mttd_seconds"] is None
    assert rows[0]["mttr_seconds"] is None