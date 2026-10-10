from dataclasses import dataclass
import os
import math
from pathlib import Path
import re

from shared.config import RuntimeSettings, get_runtime_settings


def duration_seconds(value: str) -> float:
    """Parse a Prometheus duration without accepting arbitrary PromQL."""
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
    parts = re.findall(r"(\d+)(ms|s|m|h|d|w|y)", value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise ValueError(f"Invalid Prometheus duration: {value}")
    seconds = sum(int(number) * units[unit] for number, unit in parts)
    if seconds <= 0:
        raise ValueError("Prometheus durations must be positive")
    return seconds


@dataclass(frozen=True)
class M1Settings:
    runtime: RuntimeSettings
    provider_mode: str
    prometheus_base_url: str
    loki_base_url: str
    payment_service_base_url: str
    data_dir: Path
    mock_metrics_path: Path
    namespace: str
    kubectl_path: str
    kubernetes_api_url: str
    kubernetes_token_path: Path
    kubernetes_ca_path: Path
    provider_timeout_seconds: float
    stale_after_seconds: float
    history_step_seconds: int
    request_rate_window: str
    latency_window: str
    baseline_latency_multiplier: float
    baseline_error_multiplier: float
    baseline_error_floor: float
    traffic_continuity_ratio: float
    log_limit: int
    baseline_min_samples: int = 5
    baseline_min_duration_seconds: float = 60
    baseline_min_coverage_ratio: float = 0.9
    baseline_min_request_rate: float = 0.1
    baseline_max_error_rate: float = 0.01
    recovery_hold_seconds: float = 30
    recovery_min_samples: int = 3
    baseline_max_latency_ms: float = 500
    baseline_max_utilization_ratio: float = 0.85

    def __post_init__(self):
        numeric_settings = (
            self.provider_timeout_seconds, self.stale_after_seconds,
            self.baseline_latency_multiplier, self.baseline_error_multiplier,
            self.baseline_error_floor, self.traffic_continuity_ratio,
            self.baseline_min_duration_seconds, self.baseline_min_coverage_ratio,
            self.baseline_min_request_rate, self.baseline_max_error_rate,
            self.recovery_hold_seconds, self.baseline_max_latency_ms,
            self.baseline_max_utilization_ratio,
        )
        if not all(math.isfinite(value) for value in numeric_settings):
            raise ValueError("Measurement settings must be finite")
        duration_seconds(self.request_rate_window)
        duration_seconds(self.latency_window)
        if self.baseline_min_samples < 2 or self.recovery_min_samples < 2:
            raise ValueError("Baseline and recovery require multiple samples")
        if not 0 < self.baseline_min_coverage_ratio <= 1:
            raise ValueError("Baseline coverage must be in (0, 1]")
        if not 0 <= self.baseline_max_error_rate <= 1 or not 0 <= self.baseline_error_floor <= 1:
            raise ValueError("Baseline error limits must be ratios in [0, 1]")
        if self.baseline_latency_multiplier < 1 or self.baseline_error_multiplier < 1:
            raise ValueError("Baseline threshold multipliers must be at least 1")
        if not 5 <= self.history_step_seconds <= 300:
            raise ValueError("History step must be between 5 and 300 seconds")
        if min(self.baseline_min_duration_seconds, self.baseline_min_request_rate,
               self.recovery_hold_seconds, self.stale_after_seconds, self.baseline_max_latency_ms) <= 0:
            raise ValueError("Measurement durations, live traffic floor, and freshness limit must be positive")
        if not 0 < self.baseline_max_utilization_ratio <= 1:
            raise ValueError("Healthy baseline utilization ceiling must be in (0, 1]")
        if not 0 < self.traffic_continuity_ratio <= 1 or self.provider_timeout_seconds <= 0:
            raise ValueError("Traffic continuity must be in (0, 1] and provider timeout positive")
        if self.recovery_hold_seconds < self.history_step_seconds * (self.recovery_min_samples - 1):
            raise ValueError("Recovery hold must fit the minimum number of samples")


def load_m1_settings() -> M1Settings:
    service_root = Path(__file__).resolve().parents[1]
    project_root = Path(__file__).resolve().parents[3]
    kubernetes_host = os.getenv("KUBERNETES_SERVICE_HOST", "")
    kubernetes_port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    kubernetes_api_url = os.getenv(
        "KUBERNETES_API_URL",
        f"https://{kubernetes_host}:{kubernetes_port}" if kubernetes_host else "",
    )
    return M1Settings(
        runtime=get_runtime_settings("telemetry-intelligence"),
        provider_mode=os.getenv("M1_PROVIDER_MODE", "real").lower(),
        prometheus_base_url=os.getenv("PROMETHEUS_BASE_URL", "http://localhost:9090"),
        loki_base_url=os.getenv("LOKI_BASE_URL", "http://localhost:3100"),
        payment_service_base_url=os.getenv(
            "PAYMENT_SERVICE_BASE_URL", "http://localhost:8000"
        ),
        data_dir=Path(os.getenv("M1_DATA_DIR", str(service_root / "data"))),
        mock_metrics_path=Path(
            os.getenv("M1_MOCK_METRICS_PATH", str(project_root / "mocks" / "mock_metrics.json"))
        ),
        namespace=os.getenv("M1_KUBERNETES_NAMESPACE", "nexus-demo"),
        kubectl_path=os.getenv("KUBECTL_PATH", "kubectl"),
        kubernetes_api_url=kubernetes_api_url,
        kubernetes_token_path=Path(
            os.getenv(
                "KUBERNETES_TOKEN_PATH",
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            )
        ),
        kubernetes_ca_path=Path(
            os.getenv(
                "KUBERNETES_CA_PATH",
                "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
            )
        ),
        provider_timeout_seconds=float(os.getenv("M1_PROVIDER_TIMEOUT_SECONDS", "5")),
        stale_after_seconds=float(os.getenv("M1_STALE_AFTER_SECONDS", "60")),
        history_step_seconds=int(os.getenv("M1_HISTORY_STEP_SECONDS", "15")),
        request_rate_window=os.getenv("M1_REQUEST_RATE_WINDOW", "1m"),
        latency_window=os.getenv("M1_LATENCY_WINDOW", "1m"),
        baseline_latency_multiplier=float(
            os.getenv("M1_BASELINE_LATENCY_MULTIPLIER", "1.25")
        ),
        baseline_error_multiplier=float(
            os.getenv("M1_BASELINE_ERROR_MULTIPLIER", "1.5")
        ),
        baseline_error_floor=float(os.getenv("M1_BASELINE_ERROR_FLOOR", "0.01")),
        traffic_continuity_ratio=float(
            os.getenv("M1_TRAFFIC_CONTINUITY_RATIO", "0.5")
        ),
        log_limit=int(os.getenv("M1_LOG_LIMIT", "100")),
        baseline_min_samples=int(os.getenv("M1_BASELINE_MIN_SAMPLES", "5")),
        baseline_min_duration_seconds=float(os.getenv("M1_BASELINE_MIN_DURATION_SECONDS", "60")),
        baseline_min_coverage_ratio=float(os.getenv("M1_BASELINE_MIN_COVERAGE_RATIO", "0.9")),
        baseline_min_request_rate=float(os.getenv("M1_BASELINE_MIN_REQUEST_RATE", "0.1")),
        baseline_max_error_rate=float(os.getenv("M1_BASELINE_MAX_ERROR_RATE", "0.01")),
        recovery_hold_seconds=float(os.getenv("M1_RECOVERY_HOLD_SECONDS", "30")),
        recovery_min_samples=int(os.getenv("M1_RECOVERY_MIN_SAMPLES", "3")),
        baseline_max_latency_ms=float(os.getenv("M1_BASELINE_MAX_LATENCY_MS", "500")),
        baseline_max_utilization_ratio=float(os.getenv("M1_BASELINE_MAX_UTILIZATION_RATIO", "0.85")),
    )
