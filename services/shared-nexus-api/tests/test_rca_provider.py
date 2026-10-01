from app.providers.rca_provider import get_rca_result


def test_get_rca_result():
    data = get_rca_result()

    assert data["root_cause"] == "faulty_deployment"
    assert data["confidence"] == 0.92
    assert "evidence" in data