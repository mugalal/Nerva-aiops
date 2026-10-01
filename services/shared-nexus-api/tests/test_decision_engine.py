from app.decision_engine.engine import decide_action, DecisionAction
from app.providers.rca_provider import get_rca_result
from app.decision_engine.engine import (
    decide_action,
    decide_from_rca,
    choose_scale_option,
)


def test_faulty_deployment_returns_rollback():
    action = decide_action("faulty_deployment")

    assert action == DecisionAction.ROLLBACK
    
def test_unknown_root_cause_returns_escalate():
    action = decide_action("unknown")

    assert action == DecisionAction.ESCALATE

def test_traffic_spike_returns_scale():
    action = decide_action("traffic_spike")

    assert action == DecisionAction.SCALE



def test_decision_using_rca_provider():
    rca = get_rca_result()

    action = decide_action(rca["root_cause"])

    assert action == DecisionAction.ROLLBACK
    



def test_decide_from_rca():
    rca = {
        "root_cause": "faulty_deployment",
        "confidence": 0.92
    }

    result = decide_from_rca(rca)

    assert result.action == DecisionAction.ROLLBACK
    assert result.approval_required is True
    assert result.confidence == 0.92
    
def test_decide_from_rca_accepts_finops_context():
    rca = {
        "root_cause": "traffic_spike",
        "confidence": 0.88
    }

    finops = {
        "temporary_scale_options": [
            {
                "replicas": 3,
                "estimated_cost": 5.0
            }
        ]
    }

    result = decide_from_rca(rca, finops)

    assert result.action == DecisionAction.SCALE
    
def test_choose_scale_option_returns_cheapest():
    finops = {
        "temporary_scale_options": [
            {"replicas": 3, "estimated_cost": 5.0},
            {"replicas": 4, "estimated_cost": 8.0},
        ]
    }

    option = choose_scale_option(finops)

    assert option["replicas"] == 3
    assert option["estimated_cost"] == 5.0
    
def test_choose_scale_option_returns_none_when_no_options():
    finops = {
        "temporary_scale_options": []
    }

    option = choose_scale_option(finops)

    assert option is None
    
def test_scale_without_finops_options_escalates():
    rca = {
        "root_cause": "traffic_spike",
        "confidence": 0.88
    }

    finops = {
        "temporary_scale_options": []
    }

    result = decide_from_rca(rca, finops)

    assert result.action == DecisionAction.ESCALATE
    
def test_scale_with_finops_option_stays_scale():
    rca = {
        "root_cause": "traffic_spike",
        "confidence": 0.88
    }

    finops = {
        "temporary_scale_options": [
            {
                "replicas": 3,
                "estimated_cost": 5.0
            }
        ]
    }

    result = decide_from_rca(rca, finops)

    assert result.action == DecisionAction.SCALE
    
def test_scale_decision_returns_selected_option():
    rca = {
        "root_cause": "traffic_spike",
        "confidence": 0.88
    }

    finops = {
        "temporary_scale_options": [
            {"replicas": 3, "estimated_cost": 5.0},
            {"replicas": 4, "estimated_cost": 8.0}
        ]
    }

    result = decide_from_rca(rca, finops)

    assert result.action == DecisionAction.SCALE
    assert result.scale_option["replicas"] == 3
    assert result.scale_option["estimated_cost"] == 5.0
    
def test_rollback_decision_has_reason():
    rca = {
        "root_cause": "faulty_deployment",
        "confidence": 0.92
    }

    result = decide_from_rca(rca)

    assert result.reason == "Faulty deployment detected"
    
def test_escalate_does_not_require_approval():
    rca = {
        "root_cause": "unknown",
        "confidence": 0.40
    }

    result = decide_from_rca(rca)

    assert result.action == DecisionAction.ESCALATE
    assert result.approval_required is False