from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from app.main import app
from app.api.incidents import find_incident, lock_for
from app.providers import rca_provider, evidence_provider, finops_provider, recovery_provider
from app.remediation import executor
from app.decision_engine.engine import choose_scale_option
from app.orchestration import orchestrator
from app.config import load_recovery_timeout_seconds


class DependencyFixtures:
    """Explicit HTTP doubles; no real dependency or actuator is contacted."""
    def __init__(self, cause="faulty_deployment"):
        self.cause = cause
        self.calls = []
        self.capture_failures = 0
        self.pending = 0
        self.wrong_rca = False
        self.wrong_recovery = False
        self.slo_restored = True
        self.confidence = 0.92
        self.finops_failure = False
        self.rca_version = "v2"
        self.capture_version = "v2"

    @staticmethod
    def response(data, status=200):
        return SimpleNamespace(status_code=status, json=lambda: data)

    def post(self, url, json, timeout):
        self.calls.append((url, dict(json)))
        identifier = json["incident_id"]
        if url.endswith("/internal/rca/analyze"):
            return self.response({"incident_id": "WRONG" if self.wrong_rca else identifier,
                "root_cause": self.cause, "affected_component": "payment-service:" + self.rca_version,
                "confidence": self.confidence, "evidence": ["Measured fixture evidence"]})
        if url.endswith("/internal/evidence/capture"):
            if self.capture_failures:
                self.capture_failures -= 1
                return self.response({"detail": "fixture M1 unavailable"}, 503)
            timestamp = datetime.now(timezone.utc).isoformat()
            measured = datetime.now(timezone.utc) - timedelta(minutes=5)
            baseline_version = ("v1" if self.capture_version == "v2" else "v2") if json["scenario"] == "bad_deployment" else self.capture_version
            baseline = {"service": json["service"], "version": baseline_version,
                        "measured_at": measured.isoformat(), "window_start": (measured - timedelta(minutes=2)).isoformat(),
                        "window_end": measured.isoformat(), "sample_count": 9}
            return self.response({"incident_id": identifier, "service": json["service"],
                "scenario": json["scenario"], "captured_at": timestamp, "baseline": baseline,
                "before": {"service": json["service"], "timestamp": timestamp, "version": self.capture_version,
                           "metrics": {"cpu": 0.9, "memory": 0.5, "request_rate": 180,
                                       "latency_p95_ms": 820, "http_5xx_rate": 0.1, "replica_count": 1}},
                "deployment_event": {"event_id": "fixture-deployment", "service": json["service"],
                                     "old_version": "v1" if self.capture_version == "v2" else "v2",
                                     "new_version": self.capture_version, "commit_sha": "abc123",
                                     "pipeline_id": "fixture-pipeline", "timestamp": timestamp, "status": "SUCCESS"}})
        if url.endswith("/internal/recovery/validate"):
            if self.pending:
                self.pending -= 1
                return self.response({"detail": {"category": "recovery_pending", "retryable": True}}, 409)
            metrics = {"latency_p95_ms": 80, "http_5xx_rate": 0}
            return self.response({"incident_id": "WRONG" if self.wrong_recovery else identifier,
                "recovered": True, "before": metrics, "after": metrics,
                "recovery_time_seconds": 100, "slo_restored": self.slo_restored})
        raise AssertionError(f"Unexpected dependency URL: {url}")

    def get(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if self.finops_failure:
            return self.response({"detail": "fixture M6 unavailable"}, 503)
        return self.response({"service": params["service"], "current_replicas": 1,
            "current_cpu_request_m": 100, "observed_cpu_pct": 70,
            "temporary_scale_options": [
                {"replicas": 2, "estimated_cost_delta": 0.1, "risk": "HIGH"},
                {"replicas": 3, "estimated_cost_delta": 0.3, "risk": "LOW"}]})


@pytest.fixture
def real_data_clients(monkeypatch):
    for module, setting in ((rca_provider, "RCA_PROVIDER"), (evidence_provider, "EVIDENCE_PROVIDER"),
                            (finops_provider, "FINOPS_PROVIDER"), (recovery_provider, "RECOVERY_PROVIDER")):
        monkeypatch.setattr(module, setting, "real")
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "mock")
    fixture = DependencyFixtures()
    monkeypatch.setattr(requests, "post", fixture.post)
    monkeypatch.setattr(requests, "get", fixture.get)
    return TestClient(app), fixture


def create(client, identifier="INC-REAL"):
    response = client.post("/api/incidents/", json={"incident_id": identifier,
        "severity": "high", "affected_services": ["payment-service"]})
    assert response.status_code == 200
    return identifier


def propose_and_approve(client, identifier):
    decision = client.post(f"/internal/decisions/build/{identifier}")
    assert decision.status_code == 200
    action = client.post(f"/api/incidents/{identifier}/approve")
    assert action.status_code == 200
    return action.json()


def test_incident_and_linked_actual_anomaly_contracts(real_data_clients):
    client, fixture = real_data_clients
    identifier = create(client)
    public = client.get(f"/api/incidents/{identifier}").json()
    assert set(public) == {"incident_id", "started_at", "status", "severity", "affected_services", "anomaly_ids"}
    assert datetime.fromisoformat(public["started_at"].replace("Z", "+00:00")).tzinfo is not None
    assert client.get("/api/incidents/unknown").status_code == 404
    assert client.get(f"/internal/anomalies?incident_id={identifier}").status_code == 404
    event = {"anomaly_id": "ACTUAL-ANOMALY", "timestamp": datetime.now(timezone.utc).isoformat(),
             "service": "payment-service", "score": 0.94, "severity": "high", "model": "m2-detector",
             "features": {"request_rate": 180, "latency_p95_ms": 820}}
    assert client.post(f"/internal/anomalies?incident_id={identifier}", json=event).status_code == 200
    assert client.get(f"/internal/anomalies?incident_id={identifier}").json()["anomaly_id"] == event["anomaly_id"]
    assert client.get(f"/api/incidents/{identifier}").json()["anomaly_ids"] == [event["anomaly_id"]]
    event["service"] = "other-service"
    assert client.post(f"/internal/anomalies?incident_id={identifier}", json=event).status_code == 409
    assert fixture.calls == []


def test_rca_anomaly_read_does_not_wait_for_incident_decision_lock(real_data_clients):
    client, _ = real_data_clients
    identifier = create(client)
    event = {"anomaly_id": "READ-DURING-RCA", "timestamp": datetime.now(timezone.utc).isoformat(),
             "service": "payment-service", "score": 0.9, "severity": "high", "model": "m2-detector",
             "features": {"request_rate": 180}}
    assert client.post(f"/internal/anomalies?incident_id={identifier}", json=event).status_code == 200
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lock_for(identifier):
            response = pool.submit(client.get, f"/internal/anomalies?incident_id={identifier}").result(timeout=2)
            assert response.status_code == 200 and response.json()["anomaly_id"] == event["anomaly_id"]


@pytest.mark.parametrize("cause, action, scenario", [("faulty_deployment", "ROLLBACK", "bad_deployment"),
                                                   ("traffic_spike", "SCALE", "traffic_spike")])
def test_both_scenario_flows_pending_then_success(real_data_clients, cause, action, scenario):
    client, fixture = real_data_clients
    fixture.cause = cause
    fixture.pending = 1
    identifier = create(client)
    completed = propose_and_approve(client, identifier)
    assert completed["action"] == action and completed["status"] == "SUCCESS"
    assert set(completed) == {"action_id", "incident_id", "action", "status", "started_at", "completed_at"}
    proposal = client.get(f"/api/incidents/{identifier}/proposal").json()
    assert set(proposal) == {"incident_id", "recommended_action", "target", "parameters", "confidence", "risk", "reason", "approval_required"}
    if action == "SCALE":
        assert proposal["parameters"] == {"replicas": 3}
    pending = client.post(f"/internal/recovery/validate/{identifier}")
    assert pending.status_code == 202 and pending.headers["Retry-After"] == "15"
    assert find_incident(identifier)["status"] == "VALIDATING"
    success = client.post(f"/internal/recovery/validate/{identifier}")
    assert success.status_code == 200 and success.json()["status"] == "RESOLVED"
    count = len(fixture.calls)
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "RESOLVED"
    assert len(fixture.calls) == count
    recovery_calls = [payload for url, payload in fixture.calls if url.endswith("/internal/recovery/validate")]
    assert all(payload["action_completed_at"] == completed["completed_at"] and payload["scenario"] == scenario
               and payload["service"] == "payment-service" for payload in recovery_calls)
    assert client.post("/internal/recovery/validate", json={"incident_id": identifier, "success": True}).status_code == 409


def test_wrong_rca_identity_is_rejected_before_any_action(real_data_clients):
    client, fixture = real_data_clients
    fixture.wrong_rca = True
    identifier = create(client)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 502
    assert find_incident(identifier)["status"] == "DIAGNOSING"
    assert all(not url.endswith("/internal/evidence/capture") for url, _ in fixture.calls)


def test_wrong_recovery_identity_cannot_resolve_and_can_be_retried(real_data_clients):
    client, fixture = real_data_clients
    identifier = create(client)
    propose_and_approve(client, identifier)
    fixture.wrong_recovery = True
    assert client.post(f"/internal/recovery/validate/{identifier}").status_code == 502
    assert find_incident(identifier)["status"] == "VALIDATING"
    fixture.wrong_recovery = False
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "RESOLVED"


def test_negative_recovery_stabilizes_until_a_strict_pass(real_data_clients, monkeypatch):
    client, fixture = real_data_clients
    identifier = create(client)
    action = propose_and_approve(client, identifier)
    completed = datetime.fromisoformat(action["completed_at"].replace("Z", "+00:00"))
    now = completed + timedelta(seconds=120)
    monkeypatch.setattr(orchestrator, "recovery_now", lambda: now)
    fixture.slo_restored = False
    for _ in range(2):
        response = client.post(f"/internal/recovery/validate/{identifier}")
        assert response.status_code == 202 and response.headers["Retry-After"] == "15"
        assert find_incident(identifier)["status"] == "VALIDATING"
        assert "_recovery" not in find_incident(identifier)
    assert find_incident(identifier)["_first_negative_recovery"]["details"]["slo_restored"] is False
    fixture.slo_restored = True
    now = completed + timedelta(seconds=278)
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "RESOLVED"


def test_negative_recovery_deadline_escalates_once_and_caches_terminal_result(real_data_clients, monkeypatch):
    client, fixture = real_data_clients
    identifier = create(client)
    action = propose_and_approve(client, identifier)
    incident = find_incident(identifier)
    deadline = datetime.fromisoformat(incident["recovery_deadline_at"])
    now = deadline - timedelta(seconds=1)
    monkeypatch.setattr(orchestrator, "recovery_now", lambda: now)
    fixture.slo_restored = False
    assert client.post(f"/internal/recovery/validate/{identifier}").status_code == 202
    count = len(fixture.calls)
    now = deadline
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "ESCALATED"
    assert incident["_recovery"]["timed_out"] is True and incident["_recovery"]["details"]["slo_restored"] is False
    fixture.slo_restored = True
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "ESCALATED"
    assert len(fixture.calls) == count
    records = client.get("/internal/remediation/audit").json()
    assert sum(record["event_type"] == "RECOVERY_TIMEOUT" for record in records) == 1
    assert sum(record["event_type"] == "RECOVERY_STABILIZING" for record in records) == 1


@pytest.mark.parametrize("dependency", ["pending", "unavailable"])
def test_dependency_wait_crossing_recovery_deadline_cannot_extend_budget(real_data_clients, monkeypatch, dependency):
    client, fixture = real_data_clients
    identifier = create(client)
    propose_and_approve(client, identifier)
    incident = find_incident(identifier)
    deadline = datetime.fromisoformat(incident["recovery_deadline_at"])
    now = deadline - timedelta(seconds=1)
    monkeypatch.setattr(orchestrator, "recovery_now", lambda: now)
    original_post = fixture.post
    fixture.pending = 1

    def delayed_post(url, json, timeout):
        nonlocal now
        if url.endswith("/internal/recovery/validate"):
            now = deadline
            if dependency == "unavailable":
                raise requests.ConnectionError("fixture dependency still unavailable")
        return original_post(url, json, timeout)

    monkeypatch.setattr(requests, "post", delayed_post)
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "ESCALATED"
    assert incident["_recovery"]["timed_out"] is True
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: pytest.fail("Terminal recovery must not requery M1"))
    assert client.post(f"/internal/recovery/validate/{identifier}").json()["status"] == "ESCALATED"


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "3601", "not-a-number"])
def test_recovery_timeout_configuration_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="RECOVERY_TIMEOUT_SECONDS"):
        load_recovery_timeout_seconds(value)


def test_recovery_timeout_configuration_is_configurable_and_defaults_to_360(monkeypatch):
    monkeypatch.delenv("RECOVERY_TIMEOUT_SECONDS", raising=False)
    assert load_recovery_timeout_seconds() == 360
    monkeypatch.setenv("RECOVERY_TIMEOUT_SECONDS", "300")
    assert load_recovery_timeout_seconds() == 300


def test_capture_failure_retries_without_restart_or_duplicate_rca(real_data_clients):
    client, fixture = real_data_clients
    fixture.capture_failures = 1
    identifier = create(client)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 503
    assert find_incident(identifier)["status"] == "DIAGNOSED"
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 200
    count = len(fixture.calls)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 200
    assert len(fixture.calls) == count
    assert sum(url.endswith("/internal/rca/analyze") for url, _ in fixture.calls) == 1
    assert sum(url.endswith("/internal/evidence/capture") for url, _ in fixture.calls) == 2


@pytest.mark.parametrize("malformation", ["missing_metrics", "wrong_version", "unknown_old_version"])
def test_invalid_capture_cannot_be_cached_or_proposed(real_data_clients, monkeypatch, malformation):
    client, fixture = real_data_clients
    identifier = create(client)
    original_post = fixture.post

    def malformed_post(url, json, timeout):
        response = original_post(url, json, timeout)
        if url.endswith("/internal/evidence/capture"):
            payload = response.json()
            if malformation == "missing_metrics":
                del payload["before"]["metrics"]
            elif malformation == "wrong_version":
                payload["deployment_event"]["new_version"] = "another-version"
            else:
                payload["deployment_event"]["old_version"] = "unknown"
        return response

    monkeypatch.setattr(requests, "post", malformed_post)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 502
    assert find_incident(identifier)["status"] == "DIAGNOSED"
    assert "_evidence" not in find_incident(identifier) and "_proposal" not in find_incident(identifier)
    monkeypatch.setattr(requests, "post", original_post)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 200


@pytest.mark.parametrize("cause", ["faulty_deployment", "traffic_spike"])
def test_rollout_between_rca_and_capture_requires_fresh_diagnosis(real_data_clients, cause):
    client, fixture = real_data_clients
    identifier = create(client)
    fixture.cause = cause
    fixture.capture_version = "v3"
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 409
    incident = find_incident(identifier)
    assert incident["status"] == "DIAGNOSED"
    assert all(key not in incident for key in ("_rca", "_evidence", "_expected_state", "_proposal"))
    fixture.rca_version = "v3"
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 200
    assert incident["_proposal"]["parameters"] == ({"from_version": "v3", "to_version": "v2"}
                                                 if cause == "faulty_deployment" else {"replicas": 3})
    assert incident["_expected_state"] == {"version": "v3", "replicas": 1}
    assert sum(url.endswith("/internal/rca/analyze") for url, _ in fixture.calls) == 2


@pytest.mark.parametrize("case", ["missing", "wrong_service", "wrong_version", "short_window", "insufficient_samples", "after_incident"])
def test_real_capture_requires_preincident_baseline_for_recovery(real_data_clients, monkeypatch, case):
    client, fixture = real_data_clients
    identifier = create(client)
    original_post = fixture.post
    if case == "after_incident":
        find_incident(identifier)["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    def malformed_post(url, json, timeout):
        response = original_post(url, json, timeout)
        if url.endswith("/internal/evidence/capture"):
            payload = response.json()
            if case == "missing":
                payload.pop("baseline")
            elif case == "wrong_service":
                payload["baseline"]["service"] = "other-service"
            elif case == "wrong_version":
                payload["baseline"]["version"] = "v99"
            elif case == "short_window":
                payload["baseline"]["window_start"] = payload["baseline"]["window_end"]
            elif case == "insufficient_samples":
                payload["baseline"]["sample_count"] = 4
        return response

    monkeypatch.setattr(requests, "post", malformed_post)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 502
    assert "_proposal" not in find_incident(identifier) and "_evidence" not in find_incident(identifier)


def test_direct_remediation_has_completion_audit_and_duplicate_is_not_executed(real_data_clients):
    client, fixture = real_data_clients
    identifier = create(client)
    assert client.post(f"/internal/decisions/build/{identifier}").status_code == 200
    response = client.post("/internal/remediation/execute", json={"incident_id": identifier, "action": "ROLLBACK", "approved": True})
    assert response.status_code == 200 and response.json()["success"] is True
    record = client.get(f"/api/incidents/{identifier}/actions/latest").json()
    assert record["completed_at"] == find_incident(identifier)["action_completed_at"]
    assert client.post(f"/api/incidents/{identifier}/approve").json()["action_id"] == record["action_id"]
    audits = client.get("/internal/remediation/audit").json()
    assert sum(audit["event_type"] == "ACTION_EXECUTED" for audit in audits) == 1


def test_runtime_execution_failure_never_leaves_executing(real_data_clients, monkeypatch):
    client, fixture = real_data_clients
    identifier = create(client)
    client.post(f"/internal/decisions/build/{identifier}")
    def fail(*args, **kwargs):
        raise RuntimeError("fixture actuator failure")
    monkeypatch.setattr("app.orchestration.orchestrator.execute_remediation", fail)
    assert client.post(f"/api/incidents/{identifier}/approve").status_code == 400
    assert find_incident(identifier)["status"] == "ESCALATED"
    assert client.get(f"/api/incidents/{identifier}/actions/latest").json()["status"] == "FAILED"


@pytest.mark.parametrize("case", ["low_confidence", "finops_unavailable"])
def test_unsafe_or_unavailable_scaling_escalates_without_capture(real_data_clients, case):
    client, fixture = real_data_clients
    fixture.cause = "traffic_spike"
    fixture.confidence = 0.2 if case == "low_confidence" else 0.92
    fixture.finops_failure = case == "finops_unavailable"
    identifier = create(client)
    decision = client.post(f"/internal/decisions/build/{identifier}")
    assert decision.status_code == 200 and decision.json()["action"] == "ESCALATE"
    assert find_incident(identifier)["status"] == "ESCALATED"
    assert all(not url.endswith("/internal/evidence/capture") for url, _ in fixture.calls)


def test_scaling_rejects_high_risk_nonincrease_bool_and_over_guardrail():
    options = [{"replicas": replicas, "risk": risk, "estimated_cost_delta": cost}
               for replicas, risk, cost in ((2, "HIGH", 0), (1, "LOW", 0), (11, "LOW", 0),
                                             (True, "LOW", 0), (3, "LOW", 1))]
    assert choose_scale_option({"current_replicas": 1, "temporary_scale_options": options})["replicas"] == 3
    assert choose_scale_option({"current_replicas": 1, "temporary_scale_options": options[:-1]}) is None


def test_kubernetes_completion_waits_for_rollout_and_uses_runtime_path(monkeypatch):
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "kubernetes")
    monkeypatch.setattr(executor, "KUBECTL_PATH", "fixture-kubectl")
    monkeypatch.setattr(executor, "KUBECTL_CONTEXT", "fixture-cluster")
    deployment = {"metadata": {"uid": "fixture", "resourceVersion": "10", "generation": 1},
                  "spec": {"replicas": 1, "template": {"metadata": {"labels": {"version": "v2"}}}},
                  "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1,
                             "updatedReplicas": 1, "availableReplicas": 1}}
    run = Mock(side_effect=[SimpleNamespace(returncode=0, stdout=json.dumps(deployment), stderr=""),
                           SimpleNamespace(returncode=0, stdout="scaled", stderr=""),
                           SimpleNamespace(returncode=0, stdout="completed", stderr="")])
    monkeypatch.setattr(executor.subprocess, "run", run)
    from app.decision_engine.models import DecisionAction
    assert executor.execute_remediation(DecisionAction.SCALE, True, 3,
                                       expected_state={"version": "v2", "replicas": 1, "uid": "fixture",
                                                       "template_fingerprint": executor._template_fingerprint(deployment["spec"]["template"])}).success
    assert run.call_count == 3
    assert run.call_args_list[1].args[0][:4] == ["fixture-kubectl", "--context", "fixture-cluster", "scale"]
    assert run.call_args_list[2].args[0][3:5] == ["rollout", "status"]
    assert all(call.kwargs["timeout"] > 0 for call in run.call_args_list)
