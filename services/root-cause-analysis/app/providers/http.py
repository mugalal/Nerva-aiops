"""Read real, incident-linked evidence without creating scenario-bound records."""

from urllib.parse import quote, urlsplit

import httpx

from .base import ProviderError, ProviderErrorCategory
from ..models import AnomalyEvent, EvidencePreview, Incident


class HttpEvidenceProviders:
    def __init__(self, m1_url: str, shared_url: str, timeout=10.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        for url in (m1_url, shared_url):
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Provider URLs must be HTTP(S) URLs without credentials")
        self.m1_url = m1_url.rstrip("/")
        self.shared_url = shared_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def _get(self, url, model, provider, params=None):
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                raw = response.json()
                return model.model_validate(raw) if model is not None else raw
        except httpx.TimeoutException as exc:
            raise ProviderError(ProviderErrorCategory.TIMEOUT, "Provider request timed out", provider, True) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            category = (ProviderErrorCategory.NOT_FOUND if status == 404 else
                        ProviderErrorCategory.UNAVAILABLE if status >= 500 else
                        ProviderErrorCategory.INVALID_RESPONSE)
            raise ProviderError(category, f"Provider returned HTTP {status}", provider, status >= 500) from exc
        except httpx.RequestError as exc:
            raise ProviderError(ProviderErrorCategory.UNAVAILABLE, "Provider could not be reached", provider, True) from exc
        except ValueError as exc:
            raise ProviderError(ProviderErrorCategory.INVALID_RESPONSE, "Provider returned malformed evidence", provider) from exc

    async def incident(self, incident_id):
        return await self._get(
            f"{self.shared_url}/api/incidents/{quote(incident_id, safe='')}", Incident, "incident")

    async def anomaly(self, incident_id):
        return await self._get(f"{self.shared_url}/internal/anomalies", AnomalyEvent,
                               "anomaly", {"incident_id": incident_id})

    async def preview(self, service):
        return await self._get(f"{self.m1_url}/internal/evidence/preview", EvidencePreview,
                               "telemetry", {"service": service})

    async def incident_list(self):
        result = await self._get(f"{self.shared_url}/api/incidents/", None, "incident")
        if not isinstance(result, list):
            raise ProviderError(ProviderErrorCategory.INVALID_RESPONSE,
                                "Incident list must be a list", "incident")
        return result
