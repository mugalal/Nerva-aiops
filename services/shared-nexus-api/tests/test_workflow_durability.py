from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event, Thread
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.incidents import find_incident, incidents, persist_incidents
from app.api.anomalies import anomalies
from app.remediation.audit import audit_records, add_audit_record
from app.db import workflow_store
from app.orchestration.lifecycle import restore_state
from app.orchestration.archive import enqueue_archive, deliver_archive
from app.orchestration import orchestrator

client = TestClient(app)


@pytest.fixture
def durable_state(monkeypatch, tmp_path):
    monkeypatch.setenv("M4_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("M4_SQLITE_PATH", str(tmp_path / "workflow.sqlite"))
    workflow_store.initialize()


def create(identifier="INC-DURABLE"):
    response = client.post("/api/incidents/", json={"incident_id": identifier, "severity": "high",
                                                  "affected_services": ["payment-service"]})
    assert response.status_code == 200
    return find_incident(identifier)


def decide(incident):
    assert client.post("/internal/decisions/build/" + incident["incident_id"]).status_code == 200
    return incident


def restart():
    incidents.clear()
    anomalies.clear()
    audit_records.clear()
    restore_state()


def test_restart_preserves_linked_anomaly_proposal_preconditions_and_audit(durable_state):
    incident = create()
    event = {"anomaly_id": "A-RESTART", "timestamp": datetime.now(timezone.utc).isoformat(),
             "service": "payment-service", "score": 0.93, "severity": "high", "model": "controlled",
             "features": {"latency_p95_ms": 900}}
    response = client.post("/internal/anomalies?incident_id=INC-DURABLE", json=event)
    assert response.status_code == 200
    event = response.json()
    decide(incident)
    proposal = dict(incident["_proposal"])
    expected = dict(incident["_expected_state"])
    restart()
    restored = find_incident("INC-DURABLE")
    assert restored["status"] == "AWAITING_APPROVAL"
    assert restored["_proposal"] == proposal and restored["_expected_state"] == expected
    assert restored["anomaly_ids"] == ["A-RESTART"]
    assert anomalies["A-RESTART"] == event
    assert any(row["event_type"] == "DECISION_PROPOSED" for row in audit_records)
    context = client.get("/api/incidents/INC-DURABLE/context").json()
    assert context["anomaly"] == event and context["decision"] == proposal and context["source"] == "mock"


def test_restart_retains_completed_execution_without_repeating_side_effect(durable_state, monkeypatch):
    incident = decide(create())
    response = client.post("/api/incidents/INC-DURABLE/approve")
    assert response.status_code == 200
    deadline = incident["recovery_deadline_at"]
    action = response.json()
    restart()
    restored = find_incident("INC-DURABLE")
    assert restored["status"] == "VALIDATING" and restored["recovery_deadline_at"] == deadline
    execute = Mock(side_effect=AssertionError("Action repeated after restart"))
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    duplicate = client.post("/api/incidents/INC-DURABLE/approve")
    assert duplicate.status_code == 200 and duplicate.json() == action
    execute.assert_not_called()


def test_crash_between_intent_and_result_is_escalated_and_never_reexecuted(durable_state, monkeypatch):
    incident = decide(create())
    incident.update(status="EXECUTING", _execution_intent={"action_id": "ACT-AMBIGUOUS", "incident_id": "INC-DURABLE",
        "action": "ROLLBACK", "started_at": datetime.now(timezone.utc).isoformat(), "status": "INTENT"})
    persist_incidents()
    restart()
    restored = find_incident("INC-DURABLE")
    assert restored["status"] == "ESCALATED" and restored["_execution_ambiguous"] is True
    execute = Mock()
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 409
    execute.assert_not_called()
    assert any(row["event_type"] == "ACTION_OUTCOME_UNKNOWN" for row in audit_records)
    enqueue_archive(restored)
    assert "_archive_outbox" not in restored and "Ambiguous" in restored["_archive_blocked_reason"]


def test_failed_durable_intent_never_executes_and_returns_explicit_unavailable(monkeypatch):
    decide(create())
    execute = Mock()
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    def unavailable(items):
        raise workflow_store.StorageError("controlled unavailable database")
    monkeypatch.setattr(workflow_store, "save_incidents", unavailable)
    response = client.post("/api/incidents/INC-DURABLE/approve")
    assert response.status_code == 503
    execute.assert_not_called()


def test_archive_retry_survives_restart_and_requires_verified_acknowledgement(durable_state, monkeypatch):
    incident = decide(create())
    incident["status"] = "ESCALATED"
    enqueue_archive(incident)
    first_payload = incident["_archive_outbox"]["payload"]
    post = Mock(return_value=SimpleNamespace(status_code=503))
    monkeypatch.setattr("app.orchestration.archive.requests.post", post)
    assert deliver_archive("INC-DURABLE") is False
    restart()
    restored = find_incident("INC-DURABLE")
    job = restored["_archive_outbox"]
    assert job["attempts"] == 1 and job["status"] == "PENDING" and job["payload"] == first_payload
    post.return_value = SimpleNamespace(status_code=200, json=lambda: {"record": job["payload"]})
    assert deliver_archive("INC-DURABLE", now=datetime.fromisoformat(job["next_attempt_at"]) + timedelta(seconds=1)) is True
    assert job["status"] == "DELIVERED" and job["attempts"] == 2
    assert post.call_args_list[0].kwargs["json"] == post.call_args_list[1].kwargs["json"]
    assert post.call_args.kwargs["json"]["resolved"] is False
    assert post.call_args.kwargs["json"]["source"] == "mock"
    restart()
    assert find_incident("INC-DURABLE")["_archive_outbox"]["status"] == "DELIVERED"
    assert deliver_archive("INC-DURABLE") is False


def test_invalid_acknowledgement_and_4xx_are_retained_not_silently_lost(durable_state, monkeypatch):
    incident = decide(create())
    incident["status"] = "ESCALATED"
    enqueue_archive(incident)
    post = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"ok": True}))
    monkeypatch.setattr("app.orchestration.archive.requests.post", post)
    assert deliver_archive("INC-DURABLE") is False
    job = incident["_archive_outbox"]
    assert job["status"] == "PENDING"
    post.return_value = SimpleNamespace(status_code=422)
    assert deliver_archive("INC-DURABLE", datetime.fromisoformat(job["next_attempt_at"])) is False
    assert job["status"] == "BLOCKED" and job["last_error"] == "M5 returned HTTP 422"


def test_real_resolved_archive_cannot_fabricate_a_recovery(durable_state):
    incident = decide(create())
    incident.update(status="RESOLVED", _provenance={key: "real" for key in orchestrator.provenance()})
    enqueue_archive(incident)
    assert "_archive_outbox" not in incident
    assert "measured M1" in incident["_archive_blocked_reason"]


@pytest.mark.parametrize("path,payload", [("/api/incidents/INC-DURABLE/approve", None),
    ("/api/incidents/INC-DURABLE/reject", None),
    ("/internal/remediation/execute", {"incident_id": "INC-DURABLE", "action": "ROLLBACK", "approved": True})])
def test_all_operator_paths_reject_missing_or_wrong_token(monkeypatch, path, payload):
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "controlled-secret")
    decide(create())
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, json=payload, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    assert find_incident("INC-DURABLE")["status"] == "AWAITING_APPROVAL"


def test_rejection_is_authenticated_recorded_and_cannot_cancel_execution(monkeypatch):
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "controlled-secret")
    decide(create())
    headers = {"Authorization": "Bearer controlled-secret", "X-Nexus-Approver": "reviewer-one"}
    response = client.post("/api/incidents/INC-DURABLE/reject", headers=headers)
    assert response.status_code == 200 and response.json()["approved"] is False
    assert any(row["event_type"] == "MANUAL_APPROVAL_REJECTED" and row["details"]["approver"] == "reviewer-one" for row in audit_records)
    assert client.post("/api/incidents/INC-DURABLE/reject", headers=headers).status_code == 409


def test_real_execution_without_auth_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("NEXUS_APPROVAL_TOKEN", raising=False)
    monkeypatch.delenv("M5_SHARED_API_TOKEN", raising=False)
    monkeypatch.setattr("app.remediation.executor.REMEDIATION_BACKEND", "kubernetes")
    assert client.post("/internal/remediation/execute", json={"incident_id": "missing", "action": "ROLLBACK", "approved": True}).status_code == 503


def test_experiment_does_not_invent_injection_timestamp_and_rejects_future_observation(durable_state):
    incident = create()
    path = "/api/incidents/INC-DURABLE/experiment"
    future = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
    assert client.post(path, json={"run_id": "RUN-1", "scenario": "bad_deployment", "injection_time": future,
                                  "injection_evidence": "controlled observer"}).status_code == 422
    response = client.post(path, json={"run_id": "RUN-1", "scenario": "bad_deployment"})
    assert response.status_code == 200 and response.json()["injection_time"] is None
    assert response.json()["rca_correct"] is None
    restart()
    assert find_incident("INC-DURABLE")["_experiment"]["injection_time"] is None
    assert datetime.fromisoformat(incident["detected_at"]).tzinfo is not None


def test_anomaly_association_is_immutable_after_restart(durable_state):
    create("FIRST")
    create("SECOND")
    event = {"anomaly_id": "ONE-OWNER", "timestamp": datetime.now(timezone.utc).isoformat(),
             "service": "payment-service", "score": 0.92, "severity": "high", "model": "controlled", "features": {}}
    assert client.post("/internal/anomalies?incident_id=FIRST", json=event).status_code == 200
    restart()
    assert client.post("/internal/anomalies?incident_id=SECOND", json=event).status_code == 409
    assert find_incident("SECOND")["anomaly_ids"] == []


def test_negative_recovery_measurement_and_deadline_survive_restart(durable_state, monkeypatch):
    incident = decide(create())
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 200
    measured = {"incident_id": "INC-DURABLE", "success": False, "source": "mock"}
    monkeypatch.setattr(orchestrator, "validate_recovery", lambda *args, **kwargs: measured)
    deadline = incident["recovery_deadline_at"]
    assert client.post("/internal/recovery/validate/INC-DURABLE").status_code == 202
    restart()
    restored = find_incident("INC-DURABLE")
    assert restored["_first_negative_recovery"] == measured and restored["_last_recovery_measurement"] == measured
    assert restored["recovery_deadline_at"] == deadline
    monkeypatch.setattr(orchestrator, "recovery_now", lambda: datetime.fromisoformat(deadline) + timedelta(seconds=1))
    assert client.post("/internal/recovery/validate/INC-DURABLE").json()["status"] == "ESCALATED"
    restart()
    restored = find_incident("INC-DURABLE")
    assert restored["_recovery"]["timed_out"] is True
    assert restored["_archive_outbox"]["payload"]["resolved"] is False


def test_archive_retry_requires_auth_and_reuses_the_retained_payload(monkeypatch):
    incident = decide(create())
    incident["status"] = "ESCALATED"
    enqueue_archive(incident)
    job = incident["_archive_outbox"]
    payload = job["payload"]
    job.update(status="BLOCKED", last_error="M5 returned HTTP 422")
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "controlled-secret")
    path = "/api/incidents/INC-DURABLE/archive/retry"
    assert client.post(path).status_code == 401
    response = client.post(path, headers={"X-Nexus-Approver-Token": "controlled-secret"})
    assert response.status_code == 200 and job["status"] == "PENDING"
    assert job["payload"] == payload


def test_failed_create_rolls_back_and_same_id_can_be_retried(durable_state, monkeypatch):
    payload = {"incident_id": "FAILED-CREATE", "severity": "high", "affected_services": ["payment-service"]}
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_incidents", Mock(side_effect=workflow_store.StorageError("outage")))
        assert client.post("/api/incidents/", json=payload).status_code == 503
    assert find_incident("FAILED-CREATE") is None
    assert client.get("/api/incidents/FAILED-CREATE").status_code == 404
    assert client.post("/api/incidents/", json=payload).status_code == 200
    restart()
    assert find_incident("FAILED-CREATE") is not None


def test_failed_proposal_commit_removes_cached_decision_and_retry_finishes(durable_state, monkeypatch):
    incident = create()
    save = workflow_store.save_incidents
    def fail_proposal(items):
        if any(item.get("_decision") for item in items):
            raise workflow_store.StorageError("proposal commit unavailable")
        save(items)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_incidents", fail_proposal)
        assert client.post("/internal/decisions/build/INC-DURABLE").status_code == 503
    assert incident["status"] == "DIAGNOSED" and "_decision" not in incident
    decide(incident)
    assert incident["status"] == "AWAITING_APPROVAL"
    restart()
    assert find_incident("INC-DURABLE")["status"] == "AWAITING_APPROVAL"


@pytest.mark.parametrize("failing_method", ["save_incidents", "save_audit"])
def test_failed_pre_actuation_commit_can_retry_without_phantom_intent(durable_state, monkeypatch, failing_method):
    incident = decide(create())
    execute = Mock(wraps=orchestrator.execute_remediation)
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, failing_method, Mock(side_effect=workflow_store.StorageError("outage")))
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
    assert incident["status"] == "AWAITING_APPROVAL" and "_execution_intent" not in incident
    execute.assert_not_called()
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 200
    assert execute.call_count == 1 and incident["status"] == "VALIDATING"


@pytest.mark.parametrize("retry_via_worker", [False, True])
def test_failed_result_commit_is_hidden_and_retried_without_actuation(durable_state, monkeypatch, retry_via_worker):
    incident = decide(create())
    save = workflow_store.save_incidents
    def fail_result(items):
        if any(item.get("_execution_result") for item in items):
            raise workflow_store.StorageError("result commit unavailable")
        save(items)
    execute = Mock(wraps=orchestrator.execute_remediation)
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_incidents", fail_result)
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
        assert client.get("/api/incidents/INC-DURABLE/actions/latest").status_code == 404
        assert client.get("/api/incidents/INC-DURABLE/context").json()["action_result"] is None
    assert incident["status"] == "EXECUTING" and "_execution_result" not in incident
    if retry_via_worker:
        from app.orchestration.lifecycle import work_once
        work_once()
    response = client.post("/api/incidents/INC-DURABLE/approve")
    assert response.status_code == 200 and execute.call_count == 1
    assert incident["status"] == "VALIDATING"
    restart()
    assert find_incident("INC-DURABLE")["status"] == "VALIDATING"
    assert client.post("/api/incidents/INC-DURABLE/approve").json() == response.json()
    assert execute.call_count == 1


def test_failed_result_commit_then_process_loss_retains_ambiguous_intent(durable_state, monkeypatch):
    decide(create())
    save = workflow_store.save_incidents
    def fail_result(items):
        if any(item.get("_execution_result") for item in items):
            raise workflow_store.StorageError("outage")
        save(items)
    execute = Mock(wraps=orchestrator.execute_remediation)
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_incidents", fail_result)
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
    restart()
    incident = find_incident("INC-DURABLE")
    assert incident["status"] == "ESCALATED" and incident["_execution_ambiguous"] is True
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 409
    assert execute.call_count == 1


def test_failed_anomaly_commit_discards_association_before_retry(durable_state, monkeypatch):
    incident = create()
    event = {"anomaly_id": "FAILED-ANOMALY", "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "payment-service", "score": 0.9, "severity": "high", "model": "controlled", "features": {}}
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_anomaly", Mock(side_effect=workflow_store.StorageError("outage")))
        assert client.post("/internal/anomalies?incident_id=INC-DURABLE", json=event).status_code == 503
    assert incident["anomaly_ids"] == [] and "_diagnosis_pending" not in incident
    assert client.post("/internal/anomalies?incident_id=INC-DURABLE", json=event).status_code == 200
    restart()
    assert find_incident("INC-DURABLE")["anomaly_ids"] == ["FAILED-ANOMALY"]


def test_approve_after_rejection_is_a_conflict_not_upstream_failure():
    decide(create())
    assert client.post("/api/incidents/INC-DURABLE/reject").status_code == 200
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 409
    assert client.post("/internal/remediation/execute", json={"incident_id": "INC-DURABLE",
        "action": "ROLLBACK", "approved": True}).status_code == 409


def test_archive_uses_existing_approval_token_for_authenticated_store(monkeypatch):
    incident = decide(create())
    incident["status"] = "ESCALATED"
    enqueue_archive(incident)
    job = incident["_archive_outbox"]
    monkeypatch.delenv("M5_SHARED_API_TOKEN", raising=False)
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "controlled-secret")
    post = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {"record": job["payload"]}))
    monkeypatch.setattr("app.orchestration.archive.requests.post", post)
    assert deliver_archive("INC-DURABLE") is True
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer controlled-secret"


def test_other_incident_cannot_publish_a_failed_mutation(durable_state, monkeypatch):
    first_entered, release_first, second_started, second_saved = Event(), Event(), Event(), Event()
    save = workflow_store.save_incidents
    responses = {}
    def paused_save(items):
        if any(item["incident_id"] == "FIRST-FAILS" for item in items):
            first_entered.set()
            assert release_first.wait(3)
            raise workflow_store.StorageError("first commit failed")
        second_saved.set()
        save(items)
    monkeypatch.setattr(workflow_store, "save_incidents", paused_save)
    def post(identifier):
        if identifier == "SECOND-SUCCEEDS":
            second_started.set()
        responses[identifier] = client.post("/api/incidents/", json={"incident_id": identifier,
            "severity": "high", "affected_services": ["payment-service"]}).status_code
    first = Thread(target=post, args=("FIRST-FAILS",))
    second = Thread(target=post, args=("SECOND-SUCCEEDS",))
    first.start()
    try:
        assert first_entered.wait(3)
        second.start()
        assert second_started.wait(3)
        assert not second_saved.wait(0.1)
    finally:
        release_first.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert responses == {"FIRST-FAILS": 503, "SECOND-SUCCEEDS": 200}
    restart()
    assert find_incident("FIRST-FAILS") is None
    assert find_incident("SECOND-SUCCEEDS") is not None


def test_failed_post_actuation_audit_retries_only_audit(durable_state, monkeypatch):
    incident = decide(create())
    save = workflow_store.save_audit
    def fail_completed_audit(record):
        if record["event_type"] == "ACTION_EXECUTED":
            raise workflow_store.StorageError("audit unavailable")
        save(record)
    execute = Mock(wraps=orchestrator.execute_remediation)
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_audit", fail_completed_audit)
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
    assert incident["status"] == "VALIDATING"
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 200
    assert execute.call_count == 1
    restart()
    assert find_incident("INC-DURABLE")["status"] == "VALIDATING"
    assert any(record["event_type"] == "ACTION_EXECUTED" for record in audit_records)


def test_failed_actuator_outcome_commit_retries_without_repeating_action(durable_state, monkeypatch):
    incident = decide(create())
    save = workflow_store.save_incidents
    def fail_outcome(items):
        if any(item.get("_action_result") for item in items):
            raise workflow_store.StorageError("outcome unavailable")
        save(items)
    execute = Mock(side_effect=ValueError("controlled actuator failure"))
    monkeypatch.setattr(orchestrator, "execute_remediation", execute)
    with monkeypatch.context() as failing:
        failing.setattr(workflow_store, "save_incidents", fail_outcome)
        assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 503
    assert incident["status"] == "EXECUTING" and "_action_result" not in incident
    from app.orchestration.lifecycle import work_once
    work_once()
    assert incident["status"] == "ESCALATED" and incident["_action_result"]["status"] == "FAILED"
    assert client.post("/api/incidents/INC-DURABLE/approve").status_code == 409
    assert execute.call_count == 1
    restart()
    assert find_incident("INC-DURABLE")["_action_result"]["status"] == "FAILED"
