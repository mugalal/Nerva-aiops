"""Normalize the observed application container's Kubernetes resources."""

from decimal import Decimal, DecimalException
import re

from ..errors import ProviderInvalidResponse
from ..models import ResourceConfiguration


_QUANTITY = re.compile(r"([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)")
_MEMORY_UNITS = {"": Decimal(1), "m": Decimal("0.001"), "k": Decimal(1000)}
_MEMORY_UNITS.update({unit: Decimal(1000) ** power for power, unit in enumerate("MGTPE", 2)})
_MEMORY_UNITS.update({unit + "i": Decimal(1024) ** power for power, unit in enumerate("KMGTPE", 1)})


def _quantity(value, resource: str) -> Decimal:
    match = _QUANTITY.fullmatch(str(value))
    if match is None:
        raise ValueError(f"Invalid {resource} quantity")
    amount = Decimal(match[1])
    suffix = match[2]
    if resource == "cpu":
        if suffix not in {"", "m"}:
            raise ValueError("Unsupported CPU quantity suffix")
        amount *= Decimal(1) if suffix == "m" else Decimal(1000)
        if amount != amount.to_integral_value():
            raise ValueError("CPU precision must be at least one millicore")
    else:
        if suffix not in _MEMORY_UNITS:
            raise ValueError("Unsupported memory quantity suffix")
        amount = amount * _MEMORY_UNITS[suffix] / Decimal(1024 ** 2)
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{resource} resources must be positive and finite")
    return amount


def application_resources(deployment: dict, service: str) -> ResourceConfiguration:
    """Use the named application container; never sum sidecar limits into usage."""
    try:
        containers = deployment["spec"]["template"]["spec"]["containers"]
        if not isinstance(containers, list) or not containers or any(not isinstance(item, dict) for item in containers):
            raise ValueError("Application containers are missing or invalid")
        matches = [container for container in containers if container.get("name") == service]
        if len(matches) == 1:
            container = matches[0]
        elif len(containers) == 1:
            container = containers[0]
        else:
            raise ValueError("Application container is ambiguous")
        resources = container["resources"]
        requests, limits = resources["requests"], resources["limits"]
        return ResourceConfiguration(
            cpu_request_m=float(_quantity(requests["cpu"], "cpu")),
            cpu_limit_m=float(_quantity(limits["cpu"], "cpu")),
            memory_request_mb=float(_quantity(requests["memory"], "memory")),
            memory_limit_mb=float(_quantity(limits["memory"], "memory")),
        )
    except (KeyError, TypeError, ValueError, DecimalException, OverflowError) as exc:
        raise ProviderInvalidResponse("kubernetes", f"Invalid application resources: {exc}") from exc
