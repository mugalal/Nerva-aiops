import os
from typing import Any

import httpx
from pydantic import BaseModel

from shared_nexus_providers import (
    BaseProvider,
    ProviderError,
    ProviderErrorCategory,
    ProviderResponse,
)

from ..models import (
    AnomalyEvent,
    DeploymentEvent,
    Incident,
    TelemetrySnapshot,
)


class HttpJsonProvider(BaseProvider):
    """
    Base HTTP provider for M3.

    The provider follows the shared NEXUS provider contract:
    - same fetch() interface
    - explicit provider errors
    - timeout handling
    - response validation
    """

    def __init__(
        self,
        base_url: str,
        endpoint: str,
        model: type[BaseModel],
        provider_name: str,
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.model = model
        self.name = provider_name
        self.mode = "integration"
        self.timeout = timeout

    async def fetch(
        self,
        incident_id: str,
    ) -> ProviderResponse:
        url = f"{self.base_url}{self.endpoint}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout
            ) as client:

                response = await client.get(
                    url,
                    params={"incident_id": incident_id},
                )

                response.raise_for_status()

                raw_data: dict[str, Any] = response.json()

                data = self.model.model_validate(raw_data)

                return ProviderResponse(
                    data=data,
                    provider=self.name,
                    mode=self.mode,
                    source=url,
                    metadata={
                        "incident_id": incident_id,
                        "status_code": response.status_code,
                    },
                )

        except httpx.TimeoutException as exc:
            raise ProviderError(
                category=ProviderErrorCategory.TIMEOUT,
                message=f"Provider timed out: {url}",
                provider=self.name,
                retryable=True,
            ) from exc

        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                category=ProviderErrorCategory.UNAVAILABLE,
                message=(
                    f"HTTP {exc.response.status_code} "
                    f"from provider"
                ),
                provider=self.name,
                retryable=exc.response.status_code >= 500,
            ) from exc

        except httpx.RequestError as exc:
            raise ProviderError(
                category=ProviderErrorCategory.UNAVAILABLE,
                message=f"Provider unavailable: {exc}",
                provider=self.name,
                retryable=True,
            ) from exc

        except ValueError as exc:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_RESPONSE,
                message=f"Invalid provider response: {exc}",
                provider=self.name,
            ) from exc

        except Exception as exc:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_RESPONSE,
                message=str(exc),
                provider=self.name,
            ) from exc


class HttpIncidentProvider(HttpJsonProvider):
    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            endpoint="/internal/incidents",
            model=Incident,
            provider_name="http_incident",
        )


class HttpTelemetryProvider(HttpJsonProvider):
    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            endpoint="/internal/telemetry",
            model=TelemetrySnapshot,
            provider_name="http_telemetry",
        )


class HttpAnomalyProvider(HttpJsonProvider):
    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            endpoint="/internal/anomalies",
            model=AnomalyEvent,
            provider_name="http_anomaly",
        )


class HttpDeploymentProvider(HttpJsonProvider):
    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            endpoint="/internal/deployments",
            model=DeploymentEvent,
            provider_name="http_deployment",
        )


def create_http_providers() -> dict[str, HttpJsonProvider]:
    """
    Create integration providers using environment variables.

    These defaults are placeholders for the M1/M2 integration
    layer and can be changed without modifying provider code.
    """

    telemetry_url = os.getenv(
        "NEXUS_TELEMETRY_URL",
        "http://localhost:8001",
    )

    anomaly_url = os.getenv(
        "NEXUS_ANOMALY_URL",
        "http://localhost:8002",
    )

    deployment_url = os.getenv(
        "NEXUS_DEPLOYMENT_URL",
        "http://localhost:8001",
    )

    incident_url = os.getenv(
        "NEXUS_INCIDENT_URL",
        "http://localhost:8001",
    )

    return {
        "incident": HttpIncidentProvider(incident_url),
        "telemetry": HttpTelemetryProvider(telemetry_url),
        "anomaly": HttpAnomalyProvider(anomaly_url),
        "deployment": HttpDeploymentProvider(deployment_url),
    }