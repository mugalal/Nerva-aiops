"""Configurable shared API adapter. No diagnosis or decision logic lives here."""
import os
from urllib.parse import quote
import httpx

from .fixtures import demo_records, demo_bundle


class ProviderUnavailable(Exception):
    pass


class Provider:
    def __init__(self, mode=None, client=None):
        self.mode = mode or os.getenv("M5_UI_MODE", "live")
        if self.mode not in {"mock", "live"}:
            raise ValueError("M5_UI_MODE must be live or mock")
        self.base = os.getenv("SHARED_NEXUS_API_BASE_URL", "http://localhost:8004").rstrip("/")
        self.client = client

    def request(self, method, path, payload=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ProviderUnavailable("Provider path must be a relative API path")
        headers = {}
        token = os.getenv("M5_SHARED_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            if self.client:
                response = self.client.request(method, self.base + path, json=payload, headers=headers)
            else:
                with httpx.Client(timeout=5, follow_redirects=False) as client:
                    response = client.request(method, self.base + path, json=payload, headers=headers)
            if response.status_code >= 400:
                raise ProviderUnavailable(f"Shared API returned HTTP {response.status_code}")
            result = response.json()
            if not isinstance(result, dict):
                raise ProviderUnavailable("Shared API returned an invalid response envelope")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("Shared API unavailable or returned invalid JSON") from exc

    def overview(self):
        if self.mode == "mock":
            return {"source": "mock", "status": "ok", "incidents": [r["context"].get("incident") or {"incident_id": r["memory"]["incident_id"], "status": "RESOLVED", "affected_services": [r["memory"]["service"]]} for r in demo_records()]}
        result = self.request("GET", os.getenv("M5_INCIDENTS_PATH") or "/api/incidents")
        if not isinstance(result.get("incidents"), list):
            raise ProviderUnavailable("Shared overview must contain an incidents list")
        return {**result, "source": "real"}

    def detail(self, incident_id):
        if self.mode == "mock":
            return demo_bundle(incident_id)
        template = os.getenv("M5_INCIDENT_DETAIL_PATH") or "/api/incidents/{incident_id}"
        return {**self.request("GET", template.replace("{incident_id}", quote(incident_id, safe=""))), "source": "real"}

    def approval(self, incident_id, decision):
        if self.mode == "mock":
            raise ProviderUnavailable("Approval is disabled in mock mode; no infrastructure action was executed")
        template = os.getenv("M5_APPROVAL_PATH")
        if not template:
            raise ProviderUnavailable("M5_APPROVAL_PATH is not configured; M4 owns the approval contract")
        return self.request("POST", template.replace("{incident_id}", quote(incident_id, safe="")), {"decision": decision})
