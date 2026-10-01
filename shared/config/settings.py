import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSettings:
    service_name: str
    service_version: str
    environment: str
    log_level: str
    database_url: str | None
    shared_nexus_api_base_url: str
    m1_telemetry_base_url: str
    m2_anomaly_base_url: str
    m3_rca_base_url: str
    m4_decision_base_url: str
    m5_memory_base_url: str
    m6_finops_base_url: str


def get_runtime_settings(default_service_name: str) -> RuntimeSettings:
    return RuntimeSettings(
        service_name=os.getenv("SERVICE_NAME", default_service_name),
        service_version=os.getenv("SERVICE_VERSION", "0.1.0"),
        environment=os.getenv("ENVIRONMENT", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        database_url=os.getenv("DATABASE_URL"),
        shared_nexus_api_base_url=os.getenv("SHARED_NEXUS_API_BASE_URL", "http://localhost:8000"),
        m1_telemetry_base_url=os.getenv("M1_TELEMETRY_BASE_URL", "http://localhost:8001"),
        m2_anomaly_base_url=os.getenv("M2_ANOMALY_BASE_URL", "http://localhost:8002"),
        m3_rca_base_url=os.getenv("M3_RCA_BASE_URL", "http://localhost:8003"),
        m4_decision_base_url=os.getenv("M4_DECISION_BASE_URL", "http://localhost:8004"),
        m5_memory_base_url=os.getenv("M5_MEMORY_BASE_URL", "http://localhost:8005"),
        m6_finops_base_url=os.getenv("M6_FINOPS_BASE_URL", "http://localhost:8006"),
    )

