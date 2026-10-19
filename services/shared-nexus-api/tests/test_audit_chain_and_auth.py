"""Tamper-evident audit export, personal approver tokens and authenticated reads."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.api.auth import verify_approver_auth
from app.main import app, validate_startup
from app.remediation.audit import add_audit_record, audit_records, verify_chain

client = TestClient(app)
ALICE = "alice-personal-secret-0001"
BOB = "bob-personal-secret-00002"


def test_records_form_a_verifiable_hash_chain():
    first = add_audit_record("INC-1", "DECISION_PROPOSED", {"action": "SCALE"})
    second = add_audit_record("INC-1", "MANUAL_APPROVAL_GRANTED", {"approver": "alice", "action": "SCALE"})
    assert second["prev_hash"] == first["hash"]
    assert verify_chain()["valid"] is True


def test_an_edited_record_is_detected():
    add_audit_record("INC-1", "DECISION_PROPOSED", {"action": "SCALE"})
    add_audit_record("INC-1", "MANUAL_APPROVAL_GRANTED", {"approver": "alice"})
    audit_records[1]["details"]["approver"] = "mallory"
    result = verify_chain()
    assert result["valid"] is False
    assert "hash" in result["problems"][0]["problem"]


def test_a_removed_record_is_detected():
    for step in range(3):
        add_audit_record("INC-1", "STEP", {"step": step})
    del audit_records[1]
    result = verify_chain()
    assert result["valid"] is False
    assert "previous record" in result["problems"][0]["problem"]


def test_keyed_chain_cannot_be_recomputed_without_the_key(monkeypatch):
    monkeypatch.setenv("NEXUS_AUDIT_HMAC_KEY", "audit-key")
    record = add_audit_record("INC-1", "STEP", {})
    assert record["hash_alg"] == "hmac-sha256" and verify_chain()["valid"] is True
    monkeypatch.setenv("NEXUS_AUDIT_HMAC_KEY", "wrong-key")
    assert verify_chain()["valid"] is False


def test_legacy_unchained_records_are_reported_not_rejected():
    audit_records.append({"event_id": "AUD-OLD", "incident_id": "INC-0", "event_type": "OLD", "details": {}})
    add_audit_record("INC-1", "STEP", {})
    result = verify_chain()
    assert result["valid"] is True and result["legacy_unchained"] == 1


def test_export_json_and_csv_for_one_incident():
    add_audit_record("INC-1", "MANUAL_APPROVAL_GRANTED", {"approver": "alice", "action": "SCALE"})
    add_audit_record("INC-2", "DECISION_PROPOSED", {"action": "ROLLBACK"})
    response = client.get("/internal/remediation/audit/export", params={"incident_id": "INC-1"})
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert [r["incident_id"] for r in body["records"]] == ["INC-1"]
    assert body["verification"]["valid"] is True

    response = client.get("/internal/remediation/audit/export", params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert response.headers["x-audit-chain-valid"] == "true"
    assert rows[0]["actor"] == "alice" and rows[0]["action"] == "SCALE"
    assert len(rows) == 2
    assert client.get("/internal/remediation/audit/export", params={"format": "xml"}).status_code == 422


def test_personal_token_names_the_approver(monkeypatch):
    monkeypatch.setenv("NEXUS_APPROVER_TOKENS", f"alice:{ALICE},bob:{BOB}")
    assert verify_approver_auth(f"Bearer {ALICE}") == "alice"
    assert verify_approver_auth(None, BOB, "operator") == "bob"
    with pytest.raises(Exception) as caught:
        verify_approver_auth(f"Bearer {ALICE}", None, "bob")  # cannot claim to be someone else
    assert caught.value.status_code == 403
    with pytest.raises(Exception) as caught:
        verify_approver_auth("Bearer not-a-real-token")
    assert caught.value.status_code == 401


def test_shared_token_can_be_refused_once_personal_tokens_are_required(monkeypatch):
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "shared-operator-token-123")
    monkeypatch.setenv("NEXUS_APPROVER_TOKENS", f"alice:{ALICE}")
    assert verify_approver_auth("Bearer shared-operator-token-123", None, "carol") == "carol"
    monkeypatch.setenv("NEXUS_REQUIRE_NAMED_APPROVERS", "true")
    with pytest.raises(Exception) as caught:
        verify_approver_auth("Bearer shared-operator-token-123", None, "carol")
    assert caught.value.status_code == 403
    assert verify_approver_auth(f"Bearer {ALICE}") == "alice"


@pytest.mark.parametrize("value", ["alice", "alice:short", "bad name!:" + "x" * 20])
def test_malformed_personal_tokens_fail_startup(monkeypatch, value):
    monkeypatch.setenv("NEXUS_APPROVER_TOKENS", value)
    with pytest.raises(RuntimeError):
        validate_startup()


def test_reads_require_a_token_only_when_enabled(monkeypatch):
    assert client.get("/api/incidents/").status_code == 200
    monkeypatch.setenv("NEXUS_REQUIRE_READ_AUTH", "true")
    monkeypatch.setenv("NEXUS_INGEST_TOKEN", "ingest-token-0000001")
    monkeypatch.setenv("NEXUS_READ_TOKEN", "read-token-000000001")
    assert client.get("/api/incidents/").status_code == 401
    assert client.get("/internal/remediation/audit/export").status_code == 401
    assert client.get("/api/incidents/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    for token in ("read-token-000000001", "ingest-token-0000001"):
        assert client.get("/api/incidents/", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/live").status_code == 200  # probes stay open
