import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import pytest

_repo_root = Path(__file__).resolve().parents[3]
if _repo_root.exists() and (_repo_root / "shared").is_dir() and str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

for name in ("REMEDIATION_BACKEND", "RCA_PROVIDER", "FINOPS_PROVIDER", "RECOVERY_PROVIDER", "EVIDENCE_PROVIDER"):
    os.environ[name] = "mock"
os.environ["M4_STATE_BACKEND"] = "memory"
os.environ["M4_AUTO_BUILD_DECISIONS"] = "false"
os.environ["M4_AUTO_VALIDATE_RECOVERY"] = "false"
# Most tests drive several independent incidents for one service; coalescing
# is exercised explicitly in test_incident_coalescing.py.
os.environ["M4_COALESCE_INCIDENTS"] = "false"
# The kubectl fakes assert exact command sequences; the HPA lookup is covered
# in test_hpa_guard.py.
os.environ["M4_HPA_POLICY"] = "ignore"
for name in ("NEXUS_SERVICES_CONFIG", "NEXUS_INGEST_TOKEN", "NEXUS_SERVICE_TOKEN", "NEXUS_APPROVAL_TOKEN",
             "M5_SHARED_API_TOKEN"):
    os.environ.pop(name, None)

MULTI_SERVICE_EXAMPLE = _repo_root / "config" / "services.multi-example.yaml"


def stamp_proposal(incident, action, replicas=None, created_at=None):
    """A complete, timestamped proposal for tests that drive approval directly."""
    parameters = {"replicas": replicas} if action == "SCALE" else {}
    incident["_proposal"] = {"incident_id": incident["incident_id"], "recommended_action": action,
                             "target": incident["affected_services"][0], "parameters": parameters,
                             "confidence": 0.9, "risk": "LOW", "reason": "controlled test proposal",
                             "approval_required": True}
    incident["_decision"] = {"action": action, "reason": "controlled test proposal", "approval_required": True,
                             "confidence": 0.9, "scale_option": {"replicas": replicas} if replicas else None}
    incident["proposed_action"] = action
    incident["scale_option"] = {"replicas": replicas} if action == "SCALE" else None
    incident["_proposal_created_at"] = (created_at or datetime.now(timezone.utc)).isoformat()
    return incident


@pytest.fixture(autouse=True)
def isolated_m4_state():
    from app.api.incidents import incidents
    from app.api.anomalies import anomalies, anomaly_owners
    from app.remediation.audit import audit_records
    from app.api.incidents import mark_persisted
    from app.orchestration.orchestrator import clear_pending_executions
    from app.orchestration import archive
    from app.providers import resilient
    from app.db import workflow_store
    from shared import registry
    incidents.clear()
    anomalies.clear()
    anomaly_owners.clear()
    audit_records.clear()
    mark_persisted()
    clear_pending_executions()
    registry.set_registry(None)
    yield
    incidents.clear()
    anomalies.clear()
    anomaly_owners.clear()
    audit_records.clear()
    mark_persisted()
    clear_pending_executions()
    registry.set_registry(None)
    archive.set_transport(None)
    resilient.set_transport(None)
    workflow_store.reset_lease_state()
