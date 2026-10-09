import os
import pytest

for name in ("REMEDIATION_BACKEND", "RCA_PROVIDER", "FINOPS_PROVIDER", "RECOVERY_PROVIDER", "EVIDENCE_PROVIDER"):
    os.environ[name] = "mock"


@pytest.fixture(autouse=True)
def isolated_m4_state():
    from app.api.incidents import incidents
    from app.api.anomalies import anomalies
    from app.remediation.audit import audit_records
    incidents.clear()
    anomalies.clear()
    audit_records.clear()
    yield
    incidents.clear()
    anomalies.clear()
    audit_records.clear()
