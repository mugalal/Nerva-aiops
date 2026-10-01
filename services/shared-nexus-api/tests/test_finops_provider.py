from app.providers.finops_provider import get_finops_context


def test_get_finops_context():
    data = get_finops_context()

    assert data["service"] == "payment-service"
    assert "temporary_scale_options" in data
    assert len(data["temporary_scale_options"]) > 0