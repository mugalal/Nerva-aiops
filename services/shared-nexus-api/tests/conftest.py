import os
import pytest

for name in ("REMEDIATION_BACKEND", "RCA_PROVIDER", "FINOPS_PROVIDER", "RECOVERY_PROVIDER", "EVIDENCE_PROVIDER"):
    os.environ[name] = "mock"
os.environ["M4_STATE_BACKEND"] = "memory"
os.environ["M4_AUTO_BUILD_DECISIONS"] = "false"
os.environ["M4_AUTO_VALIDATE_RECOVERY"] = "false"


@pytest.fixture(autouse=True)
def isolated_m4_state():
    from app.api.incidents import incidents
    from app.api.anomalies import anomalies, anomaly_owners
    from app.remediation.audit import audit_records
    from app.api.incidents import mark_persisted
    from app.orchestration.orchestrator import clear_pending_executions
    incidents.clear()
    anomalies.clear()
    anomaly_owners.clear()
    audit_records.clear()
    mark_persisted()
    clear_pending_executions()
    yield
    incidents.clear()
    anomalies.clear()
    anomaly_owners.clear()
    audit_records.clear()
    mark_persisted()
    clear_pending_executions()
