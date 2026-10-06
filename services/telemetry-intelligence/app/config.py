from dataclasses import dataclass
import os
from pathlib import Path

from shared.config import RuntimeSettings, get_runtime_settings


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
    )
