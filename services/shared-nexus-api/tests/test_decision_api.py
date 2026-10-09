from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_evaluate_decision_returns_rollback():
    response = client.post(
        "/internal/decisions/evaluate",
        json={
            "rca": {
                "root_cause": "faulty_deployment",
                "confidence": 0.92
            }
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "ROLLBACK"
    assert data["approval_required"] is True
    assert data["confidence"] == 0.92

def test_evaluate_decision_returns_scale():
    response = client.post(
        "/internal/decisions/evaluate",
        json={
            "rca": {
                "root_cause": "traffic_spike",
                "confidence": 0.88
            },
            "finops": {
                "temporary_scale_options": [
                    {
                        "replicas": 3,
                        "estimated_cost_delta": 5.0
                    }
                ]
            }
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "SCALE"
    assert data["scale_option"]["replicas"] == 3
    
def test_evaluate_decision_returns_escalate():
    response = client.post(
        "/internal/decisions/evaluate",
        json={
            "rca": {
                "root_cause": "unknown",
                "confidence": 0.40
            }
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "ESCALATE"
    assert data["approval_required"] is False
    
def test_evaluate_scale_without_options_escalates():
    response = client.post(
        "/internal/decisions/evaluate",
        json={
            "rca": {
                "root_cause": "traffic_spike",
                "confidence": 0.88
            },
            "finops": {
                "temporary_scale_options": []
            }
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "ESCALATE"