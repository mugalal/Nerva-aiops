"""Scaling a caller while its dependency is the bottleneck only adds load: escalate instead."""
import pytest
from fastapi.testclient import TestClient

from app.api.incidents import find_incident, persist_incidents
from app.decision_engine.models import DecisionAction
from app.main import app
from app.orchestration import orchestrator
from shared import registry

client = TestClient(app)


@pytest.fixture(autouse=True)
def topology(monkeypatch):
    registry.set_registry(registry.ServiceRegistry.model_validate({"version": 1, "services": [
        {"name": "payment-service", "workload": {"name": "payment-service"}, "depends_on": ["cart-service"],
         "remediation": {"actions": ["ROLLBACK", "SCALE"], "min_replicas": 1, "max_replicas": 10, "max_scale_step": 7}},
        {"name": "cart-service", "workload": {"name": "cart-service"},
         "remediation": {"actions": ["SCALE"], "min_replicas": 1, "max_replicas": 10, "max_scale_step": 7}},
    ]}))
    monkeypatch.setattr(orchestrator, "get_rca_result", lambda incident_id: {
        "incident_id": incident_id, "root_cause": "traffic_spike", "affected_component": "payment-service",
        "confidence": 0.95, "evidence": ["request rate tripled"]})


def _create(incident_id, service):
    assert client.post("/api/incidents/", json={"incident_id": incident_id, "severity": "high",
                                                "affected_services": [service]}).status_code == 200


def test_open_dependency_incident_blocks_scaling_the_caller():
    _create("INC-CART", "cart-service")
    _create("INC-PAY", "payment-service")
    result = orchestrator.build_decision("INC-PAY")
    assert result.action == DecisionAction.ESCALATE
    assert "depends on cart-service" in result.reason and "INC-CART" in result.reason
    assert find_incident("INC-PAY")["status"] == "ESCALATED"


def test_a_closed_dependency_incident_does_not_block():
    _create("INC-CART", "cart-service")
    cart = find_incident("INC-CART")
    cart["status"] = "RESOLVED"
    persist_incidents()
    _create("INC-PAY", "payment-service")
    assert orchestrator.build_decision("INC-PAY").action == DecisionAction.SCALE


def test_dependencies_are_directional():
    _create("INC-PAY", "payment-service")
    assert orchestrator.degraded_dependencies("cart-service", "INC-X") == []
    assert orchestrator.degraded_dependencies("payment-service", "INC-X") == []
    _create("INC-CART", "cart-service")
    assert orchestrator.degraded_dependencies("payment-service", "INC-PAY") == [("cart-service", "INC-CART")]


def test_the_dependency_is_named_even_when_finops_has_no_safe_option(monkeypatch):
    """Live drill finding: M6 offered only a HIGH-risk step, so the escalation said 'no safe action'
    and never mentioned that the dependency was the problem. The dependency check now comes first."""
    calls = []
    monkeypatch.setattr(orchestrator, "get_finops_context", lambda service: calls.append(service) or {})
    _create("INC-CART", "cart-service")
    _create("INC-PAY", "payment-service")
    result = orchestrator.build_decision("INC-PAY")
    assert result.action == DecisionAction.ESCALATE and "depends on cart-service" in result.reason
    assert calls == []  # no capacity costing for a service that must not be scaled
