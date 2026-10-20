"""Scale-down: capacity added for a spike is given back, measured and approved, never on a guess."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.incidents import find_incident, persist_incidents
from app.decision_engine.models import DecisionAction
from app.main import app
from app.orchestration import lifecycle, orchestrator, rightsizing
from app.orchestration.orchestrator import apply_recovery_result, build_decision, execute_approved_action
from app.providers import evidence_provider, telemetry_provider
from app.providers.errors import ConflictError
from app.remediation import executor
from app.remediation.audit import audit_records
from shared import registry
from test_kubernetes_safety import bound_expected, completed, deployment

client = TestClient(app)
CONFIG = {"check_seconds": 60, "window_seconds": 600, "quiet_seconds": 600, "load_factor": 1.25,
          "max_utilization": 0.6}
BASELINE = {"thresholds": {"latency_p95_ms_max": 25.0, "http_5xx_rate_max": 0.01},
            "replica_count": {"maximum": 1}, "request_rate": {"p95": 10.0}}


def window(replicas=7, rate=10.0, cpu=0.05, latency=9.0, errors=0.0, points=5):
    return {"points": [{"metrics": {"replica_count": replicas, "request_rate": rate, "cpu": cpu,
                                    "latency_p95_ms": latency, "http_5xx_rate": errors}} for _ in range(points)]}


# -- sizing -----------------------------------------------------------------------

def test_quiet_traffic_on_many_replicas_steps_down_by_the_registry_limit():
    target, facts = rightsizing.plan(window(), BASELINE, 7, 1, 3, CONFIG)
    assert target == 4 and facts["needed_replicas"] == 1


def test_the_target_never_goes_below_what_the_measured_load_needs():
    # 30 req/s against 10 req/s per replica (x1.25) needs 3 replicas.
    target, facts = rightsizing.plan(window(replicas=4, rate=30), BASELINE, 4, 1, 3, CONFIG)
    assert target == 3 and facts["replicas_needed_for_rate"] == 3
    # 40% CPU across 4 replicas is 1.6 replicas' worth: at 60% that is 3 replicas.
    target, facts = rightsizing.plan(window(replicas=4, cpu=0.4), BASELINE, 4, 1, 3, CONFIG)
    assert target == 3 and facts["replicas_needed_for_cpu"] == 3


@pytest.mark.parametrize("case,expected", [
    (dict(replicas=7, rate=80), "still needs"),  # 80 / 12.5 needs 7
    (dict(replicas=7, latency=40), "not healthy"),
    (dict(replicas=7, errors=0.05), "not healthy"),
    (dict(replicas=7, points=2), "not enough"),
])
def test_no_scale_down_without_sustained_healthy_spare_capacity(case, expected):
    target, reason = rightsizing.plan(window(**case), BASELINE, 7, 1, 3, CONFIG)
    assert target is None and expected in reason


def test_no_scale_down_while_replicas_move_or_at_the_minimum():
    moving = window()
    moving["points"][2]["metrics"]["replica_count"] = 6
    assert rightsizing.plan(moving, BASELINE, 7, 1, 3, CONFIG)[0] is None
    assert "minimum" in rightsizing.plan(window(replicas=1), BASELINE, 1, 1, 3, CONFIG)[1]


def test_registry_scale_down_limits():
    reg = registry.get_registry()
    assert reg.scale_down_allowed("payment-service", 7, 4) is None
    assert "at most 3" in reg.scale_down_allowed("payment-service", 7, 3)
    assert "at or above 1" in reg.scale_down_allowed("payment-service", 2, 0)
    assert "reduce" in reg.scale_down_allowed("payment-service", 3, 3)


# -- the sweep and the workflow -----------------------------------------------------

@pytest.fixture
def telemetry(monkeypatch):
    # One registered service, so each sweep has exactly one candidate; the fake telemetry serves any service.
    registry.set_registry(registry.default_registry())
    state = {"window": window(), "baseline": BASELINE}
    monkeypatch.setattr(telemetry_provider, "get_window", lambda service, start, end, step_seconds=15: state["window"])
    monkeypatch.setattr(telemetry_provider, "get_baseline", lambda service: state["baseline"])

    def capture(incident_id, service, scenario, incident_started_at=None):
        assert scenario == "scale_down"
        replicas = state["window"]["points"][-1]["metrics"]["replica_count"]
        return {"incident_id": incident_id, "service": service, "scenario": scenario,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "before": {"version": "v1", "metrics": {"replica_count": replicas}}}

    monkeypatch.setattr(evidence_provider, "capture_incident_evidence", capture)
    for name, value in CONFIG.items():
        monkeypatch.setenv("M4_SCALE_DOWN_" + {"check_seconds": "CHECK_SECONDS", "window_seconds": "WINDOW_SECONDS",
                                                "quiet_seconds": "QUIET_SECONDS", "load_factor": "LOAD_FACTOR",
                                                "max_utilization": "MAX_UTILIZATION"}[name], str(value))
    return state


def _only_proposal():
    [incident_id] = rightsizing.sweep()
    return incident_id, find_incident(incident_id)


def test_sweep_proposes_an_approvable_scale_down(telemetry):
    incident_id, incident = _only_proposal()
    assert incident_id.startswith("RIGHTSIZE-") and incident["status"] == "AWAITING_APPROVAL"
    assert incident["_proposal"]["parameters"] == {"replicas": 4, "direction": "down"}
    assert incident["_expected_state"] == {"version": "v1", "replicas": 7, "direction": "down"}
    assert incident["recovery_scenario"] == "scale_down" and incident["root_cause"] == "over_provisioned"
    assert "not an M3 diagnosis" in incident["_rca"]["evidence"][0]
    assert {"SCALE_DOWN_PROPOSED", "DECISION_PROPOSED"} <= {r["event_type"] for r in audit_records}
    # One proposal at a time: the open proposal itself keeps the next sweep quiet.
    assert rightsizing.sweep() == []


def test_approved_scale_down_executes_and_resolves_only_on_measured_recovery(telemetry):
    incident_id, _ = _only_proposal()
    result = execute_approved_action(incident_id, DecisionAction.SCALE, approved=True, replicas=4)
    assert result.success
    assert find_incident(incident_id)["status"] == "VALIDATING"
    apply_recovery_result(incident_id, True)
    assert find_incident(incident_id)["status"] == "RESOLVED"


def test_scale_down_beyond_the_step_limit_is_refused(telemetry):
    incident_id, incident = _only_proposal()
    incident["scale_option"]["replicas"] = 2
    with pytest.raises(ValueError, match="at most 3"):
        execute_approved_action(incident_id, DecisionAction.SCALE, approved=True, replicas=2)


def test_a_real_incident_outranks_and_withdraws_a_pending_scale_down(telemetry, monkeypatch):
    monkeypatch.setenv("M4_COALESCE_INCIDENTS", "true")
    incident_id, _ = _only_proposal()
    response = client.post("/api/incidents/", json={"incident_id": "INC-SPIKE", "severity": "high",
                                                    "affected_services": ["payment-service"]})
    assert response.json()["incident_id"] == "INC-SPIKE"  # never coalesced into the scale-down
    with pytest.raises(ConflictError, match="INC-SPIKE"):
        execute_approved_action(incident_id, DecisionAction.SCALE, approved=True, replicas=4)
    lifecycle.work_once()
    withdrawn = find_incident(incident_id)
    assert withdrawn["status"] == "ESCALATED" and "INC-SPIKE" in withdrawn["_withdrawn"]
    assert "_archive_outbox" not in withdrawn  # nothing happened, nothing to archive


def test_an_expired_scale_down_is_withdrawn_not_rediagnosed(telemetry):
    incident_id, incident = _only_proposal()
    with pytest.raises(ConflictError, match="not diagnosed"):
        build_decision(incident_id)
    incident["_proposal_created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    persist_incidents()
    lifecycle.work_once()
    assert find_incident(incident_id)["_withdrawn"] == "proposal expired before approval"


def test_no_proposal_during_the_quiet_period_after_an_incident(telemetry):
    client.post("/api/incidents/", json={"incident_id": "INC-OLD", "severity": "high",
                                         "affected_services": ["payment-service"]})
    old = find_incident("INC-OLD")
    old["status"] = "RESOLVED"
    persist_incidents()
    assert rightsizing.sweep() == []
    old["_closed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
    persist_incidents()
    assert len(rightsizing.sweep()) == 1


def test_sweep_is_off_without_real_telemetry(monkeypatch):
    monkeypatch.setattr(evidence_provider, "EVIDENCE_PROVIDER", "mock")
    assert rightsizing.enabled() is False
    monkeypatch.setattr(evidence_provider, "EVIDENCE_PROVIDER", "real")
    assert rightsizing.enabled() is True
    monkeypatch.setenv("M4_SCALE_DOWN_ENABLED", "false")
    assert rightsizing.enabled() is False


# -- the actuator ---------------------------------------------------------------------

@pytest.fixture
def kube(monkeypatch):
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "kubernetes")
    monkeypatch.setattr(executor, "KUBECTL_PATH", "fixture-kubectl")
    monkeypatch.setattr(executor, "KUBECTL_CONTEXT", "")
    monkeypatch.setenv("M4_HPA_POLICY", "refuse")
    state = {"deployment": deployment(replicas=7), "hpas": [], "calls": []}

    def run(command, **kwargs):
        state["calls"].append(command)
        if command[1:3] == ["get", "deployment/payment-service"]:
            return completed(json.dumps(state["deployment"]))
        if command[1:3] == ["get", "horizontalpodautoscalers.autoscaling"]:
            return completed(json.dumps({"items": state["hpas"]}))
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)
    return state


def test_actuator_decreases_only_for_a_scale_down_proposal(kube):
    down = dict(bound_expected(deployment(replicas=7)), direction="down")
    executor.execute_remediation(DecisionAction.SCALE, True, 4, expected_state=down)
    scale = next(command for command in kube["calls"] if command[1] == "scale")
    assert "--replicas=4" in scale and "--current-replicas=7" in scale
    with pytest.raises(ValueError, match="must increase"):
        executor.execute_remediation(DecisionAction.SCALE, True, 4, expected_state=bound_expected(deployment(replicas=7)))
    with pytest.raises(ValueError, match="must reduce"):
        executor.execute_remediation(DecisionAction.SCALE, True, 8, expected_state=down)


def test_actuator_never_scales_down_under_an_hpa(kube):
    kube["hpas"] = [{"metadata": {"name": "payment-hpa", "resourceVersion": "1"},
                     "spec": {"scaleTargetRef": {"kind": "Deployment", "name": "payment-service"},
                              "minReplicas": 1, "maxReplicas": 10}}]
    down = dict(bound_expected(deployment(replicas=7)), direction="down")
    with pytest.raises(ValueError, match="owns scale-down"):
        executor.execute_remediation(DecisionAction.SCALE, True, 4, expected_state=down)
    assert not any(command[1] == "scale" for command in kube["calls"])
