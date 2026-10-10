"""Verified per-pod resource quantities, in millicores and MiB."""
from dataclasses import dataclass
import math
import os


def finite_number(value, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a finite {qualifier} number")
    return number


@dataclass(frozen=True)
class ResourceConfiguration:
    cpu_request_m: float
    cpu_limit_m: float
    memory_request_mb: float
    memory_limit_mb: float
    source: str

    @classmethod
    def from_mapping(cls, data: dict, source: str):
        if not isinstance(data, dict) or data.get("memory_unit") != "MiB":
            raise ValueError("Resource configuration must declare memory_unit=MiB")
        names = ("cpu_request_m", "cpu_limit_m", "memory_request_mb", "memory_limit_mb")
        values = {name: finite_number(data.get(name), name, positive=True) for name in names}
        if values["cpu_request_m"] > values["cpu_limit_m"]:
            raise ValueError("CPU request must not exceed CPU limit")
        if values["memory_request_mb"] > values["memory_limit_mb"]:
            raise ValueError("Memory request must not exceed memory limit")
        return cls(**values, source=source)

    @classmethod
    def from_environment(cls):
        names = {
            "cpu_request_m": "FINOPS_CPU_REQUEST_M",
            "cpu_limit_m": "FINOPS_CPU_LIMIT_M",
            "memory_request_mb": "FINOPS_MEMORY_REQUEST_MB",
            "memory_limit_mb": "FINOPS_MEMORY_LIMIT_MB",
        }
        missing = [variable for variable in names.values() if not os.getenv(variable)]
        if missing:
            raise ValueError("Explicit resource configuration is required: " + ", ".join(missing))
        data = {field: os.environ[variable] for field, variable in names.items()}
        data["memory_unit"] = "MiB"
        return cls.from_mapping(data, "explicit_environment")
