"""Configurable shared API adapter. No diagnosis or decision logic lives here."""
import os
from urllib.parse import quote
import httpx

from .fixtures import demo_records, demo_bundle


class ProviderUnavailable(Exception):
    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


class Provider:
    def __init__(self, mode=None, client=None):
        self.mode = mode or os.getenv("M5_UI_MODE", "live")
        if self.mode not in {"mock", "live"}:
            raise ValueError("M5_UI_MODE must be live or mock")
        self.base = os.getenv("SHARED_NEXUS_API_BASE_URL", "http://localhost:8004").rstrip("/")
        self.client = client

    def request(self, method, path, payload=None, *, allow_list=False, timeout=10):
        if not path.startswith("/") or path.startswith("//"):
            raise ProviderUnavailable("Provider path must be a relative API path")
        headers = {}
        token = os.getenv("M5_SHARED_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        operator = os.getenv("M5_OPERATOR_ID")
        if method != "GET" and operator:
            headers["X-Nexus-Approver"] = operator
        try:
            if self.client:
                response = self.client.request(method, self.base + path, json=payload, headers=headers)
            else:
                with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                    response = client.request(method, self.base + path, json=payload, headers=headers)
            if response.status_code >= 400:
                # Preserve actionable rejection/not-found/conflict states, never a token or body.
                code = response.status_code if response.status_code in {400, 401, 403, 404, 409, 422} else 503
                raise ProviderUnavailable(f"Shared API returned HTTP {response.status_code}", code)
            result = response.json()
            if not isinstance(result, dict) and not (allow_list and isinstance(result, list)):
                raise ProviderUnavailable("Shared API returned an invalid response envelope")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderUnavailable("Shared API unavailable or returned invalid JSON") from exc

    def overview(self):
        if self.mode == "mock":
            return {"source": "mock", "status": "ok", "incidents": [r["context"].get("incident") or {"incident_id": r["memory"]["incident_id"], "status": "RESOLVED", "affected_services": [r["memory"]["service"]]} for r in demo_records()]}
        path = os.getenv("M5_INCIDENTS_PATH") or "/api/incidents/"
        if path == "/api/incidents":
            path += "/"  # M4's list route is slash-terminated; avoid a redirect.
        result = self.request("GET", path, allow_list=True)
        if isinstance(result, list):
            result = {"status": "ok", "incidents": result}
        if not isinstance(result.get("incidents"), list):
            raise ProviderUnavailable("Shared overview must contain an incidents list")
        if any(not isinstance(row, dict) or not isinstance(row.get("incident_id"), str)
               or not isinstance(row.get("affected_services"), list) for row in result["incidents"]):
            raise ProviderUnavailable("Shared overview contains invalid incident records")
        return {"source": "real", **result, "connection_source": "shared_api"}

    def detail(self, incident_id):
        if self.mode == "mock":
            return demo_bundle(incident_id)
        template = os.getenv("M5_INCIDENT_DETAIL_PATH") or "/api/incidents/{incident_id}/context"
        result = self.request("GET", template.replace("{incident_id}", quote(incident_id, safe="")))
        if not isinstance(result.get("incident"), dict) or result["incident"].get("incident_id") != incident_id:
            raise ProviderUnavailable("Shared detail must identify the requested incident")
        if result.get("source") not in {"real", "mock", "mixed"}:
            raise ProviderUnavailable("Shared detail must explicitly identify evidence provenance")
        return {**result, "connection_source": "shared_api"}

    def approval_enabled(self):
        return self.mode == "live" and bool(os.getenv("M5_SHARED_API_TOKEN")) and bool(os.getenv("M5_OPERATOR_ID"))

    def approval(self, incident_id, decision):
        if self.mode == "mock":
            raise ProviderUnavailable("Approval is disabled in mock mode; no infrastructure action was executed")
        if decision not in {"approve", "reject"}:
            raise ProviderUnavailable("Unsupported approval decision", 422)
        if not self.approval_enabled():
            raise ProviderUnavailable("Configure M5_SHARED_API_TOKEN and M5_OPERATOR_ID before approval")
        # Separate endpoints are intentional: Reject must never reach the execution route.
        setting = "M5_APPROVE_PATH" if decision == "approve" else "M5_REJECT_PATH"
        template = os.getenv(setting) or f"/api/incidents/{{incident_id}}/{decision}"
        return self.request("POST", template.replace("{incident_id}", quote(incident_id, safe="")), {}, timeout=300)
