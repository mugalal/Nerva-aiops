
from app.providers.rca_provider import get_rca_result


def test_get_rca_result(monkeypatch):
    monkeypatch.setattr(
        "app.providers.rca_provider.RCA_PROVIDER",
        "mock"
    )

    data = get_rca_result()

    assert data["root_cause"] in {
        "faulty_deployment",
        "traffic_spike"
    }

    assert isinstance(data["confidence"], (int, float))
    assert 0 <= data["confidence"] <= 1

    assert "evidence" in data
    assert isinstance(data["evidence"], list)
