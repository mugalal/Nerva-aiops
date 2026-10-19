"""NERVA never fights a HorizontalPodAutoscaler over spec.replicas."""
import json
from types import SimpleNamespace

import pytest

from app.decision_engine.models import DecisionAction
from app.remediation import executor
from test_kubernetes_safety import bound_expected, completed, deployment


def hpa(name="payment-hpa", target="payment-service", kind="Deployment", version="77", minimum=1, maximum=5):
    return {"metadata": {"name": name, "resourceVersion": version},
            "spec": {"scaleTargetRef": {"kind": kind, "name": target}, "minReplicas": minimum, "maxReplicas": maximum}}


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "kubernetes")
    monkeypatch.setattr(executor, "KUBECTL_PATH", "fixture-kubectl")
    monkeypatch.setattr(executor, "KUBECTL_CONTEXT", "")
    monkeypatch.setenv("M4_HPA_POLICY", "refuse")
    state = SimpleNamespace(deployment=deployment(), hpas=[], calls=[])

    def run(command, **kwargs):
        state.calls.append(command)
        if command[1:3] == ["get", "deployment/payment-service"]:
            return completed(json.dumps(state.deployment))
        if command[1:3] == ["get", "horizontalpodautoscalers.autoscaling"]:
            return completed(json.dumps({"items": state.hpas}))
        return completed()

    monkeypatch.setattr(executor.subprocess, "run", run)
    return state


def test_only_an_hpa_targeting_the_workload_counts(cluster):
    cluster.hpas = [hpa(name="other", target="cart-service"), hpa(name="rs", kind="ReplicaSet")]
    assert executor.find_hpa("payment-service") is None
    cluster.hpas.append(hpa())
    assert executor.find_hpa("payment-service")["name"] == "payment-hpa"
    cluster.hpas.append(hpa(name="second"))
    with pytest.raises(ValueError, match="refusing to guess"):
        executor.find_hpa("payment-service")


def test_ignore_policy_and_mock_backend_never_query(cluster, monkeypatch):
    monkeypatch.setenv("M4_HPA_POLICY", "ignore")
    assert executor.find_hpa("payment-service") is None
    monkeypatch.setenv("M4_HPA_POLICY", "refuse")
    monkeypatch.setattr(executor, "REMEDIATION_BACKEND", "mock")
    assert executor.find_hpa("payment-service") is None
    assert cluster.calls == []
    monkeypatch.setenv("M4_HPA_POLICY", "sometimes")
    with pytest.raises(ValueError):
        executor.hpa_policy()


def test_an_hpa_appearing_after_approval_blocks_kubectl_scale(cluster):
    cluster.hpas = [hpa()]
    with pytest.raises(ValueError, match="now manages"):
        executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=bound_expected())
    assert not any(command[1] == "scale" for command in cluster.calls)


def test_raise_min_patches_the_hpa_instead_of_the_deployment(cluster, monkeypatch):
    monkeypatch.setenv("M4_HPA_POLICY", "raise-min")
    cluster.hpas = [hpa(maximum=2)]
    expected = dict(bound_expected(), hpa=executor.find_hpa("payment-service"))
    result = executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=expected)
    assert result.success and "minReplicas 1 -> 3" in result.message
    assert not any(command[1] == "scale" for command in cluster.calls)
    patch_command = next(command for command in cluster.calls if command[1] == "patch")
    assert patch_command[2] == "horizontalpodautoscalers.autoscaling/payment-hpa"
    operations = json.loads(patch_command[patch_command.index("--patch") + 1])
    assert operations[0] == {"op": "test", "path": "/metadata/resourceVersion", "value": "77"}
    assert {"op": "replace", "path": "/spec/minReplicas", "value": 3} in operations
    assert {"op": "replace", "path": "/spec/maxReplicas", "value": 3} in operations


def test_raise_min_refuses_an_hpa_changed_since_approval(cluster, monkeypatch):
    monkeypatch.setenv("M4_HPA_POLICY", "raise-min")
    cluster.hpas = [hpa()]
    expected = dict(bound_expected(), hpa=executor.find_hpa("payment-service"))
    cluster.hpas = [hpa(version="78")]
    with pytest.raises(ValueError, match="changed since"):
        executor.execute_remediation(DecisionAction.SCALE, True, 3, expected_state=expected)
    assert not any(command[1] == "patch" for command in cluster.calls)


def test_diagnosis_escalates_a_scale_that_an_hpa_owns(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.orchestration import orchestrator
    client = TestClient(app)
    client.post("/api/incidents/", json={"incident_id": "INC-HPA", "severity": "high",
                                         "affected_services": ["payment-service"]})
    monkeypatch.setattr(orchestrator, "get_rca_result", lambda incident_id: {
        "incident_id": incident_id, "root_cause": "traffic_spike", "affected_component": "payment-service",
        "confidence": 0.95, "evidence": ["request rate tripled"]})
    monkeypatch.setattr(executor, "find_hpa", lambda service: {"name": "payment-hpa", "resource_version": "1",
                                                               "min_replicas": 1, "max_replicas": 4})
    monkeypatch.setenv("M4_HPA_POLICY", "refuse")
    result = orchestrator.build_decision("INC-HPA")
    assert result.action == DecisionAction.ESCALATE
    assert "HorizontalPodAutoscaler payment-hpa" in result.reason
    assert client.get("/api/incidents/INC-HPA").json()["status"] == "ESCALATED"
