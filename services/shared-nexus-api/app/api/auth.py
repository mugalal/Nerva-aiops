"""One operator-auth policy for every approval or rejection entry point."""
import hmac
import os
from fastapi import HTTPException


def verify_approver_auth(authorization=None, token=None, approver=None):
    from app.remediation import executor
    expected = os.getenv("NEXUS_APPROVAL_TOKEN") or os.getenv("M5_SHARED_API_TOKEN")
    if not expected and executor.REMEDIATION_BACKEND != "mock":
        raise HTTPException(status_code=503, detail="Operator approval token is not configured")
    received = token
    if not received and authorization and authorization.startswith("Bearer "):
        received = authorization[7:].strip()
    if expected and (not isinstance(received, str) or not hmac.compare_digest(received, expected)):
        raise HTTPException(status_code=401, detail="Invalid or missing operator approval token")
    identity = (approver or "operator").strip()
    if not identity or len(identity) > 120:
        raise HTTPException(status_code=422, detail="Operator identity must be nonempty and at most 120 characters")
    return identity
