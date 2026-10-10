import pytest
from pathlib import Path
from shared.registry import load, get_registry, default_registry, ServiceRegistry


def test_default_registry_has_payment_service():
    reg = default_registry()
    assert len(reg.services) == 1
    assert reg.get("payment-service") is not None
    assert "payment-service" in reg.allowed_services()
    assert "nexus-demo" in reg.allowed_namespaces()
    assert reg.target_for("payment-service").workload.name == "payment-service"


def test_load_services_yaml():
    reg = get_registry()
    assert len(reg.services) >= 3
    assert {"payment-service", "cart-service", "frontend"}.issubset(reg.allowed_services())

    # cart-service check
    cart = reg.target_for("cart-service")
    assert cart.namespace == "nexus-demo"
    assert cart.workload.name == "cart-service"
    assert cart.remediation.actions == ["SCALE"]
    assert cart.remediation.max_replicas == 5

    # frontend dependencies
    frontend = reg.target_for("frontend")
    assert "payment-service" in frontend.depends_on
    assert "cart-service" in frontend.depends_on

    # unknown service raises ValueError
    with pytest.raises(ValueError, match="not allowed for remediation"):
        reg.target_for("non-existent-service")
