import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models import (
    AnomalyEvent,
    DeploymentEvent,
    Incident,
    TelemetrySnapshot,
)

from .base import (
    BaseProvider,
    ProviderError,
    ProviderErrorCategory,
    ProviderResponse,
)


# ============================================================
# Mock File Provider
# ============================================================

class MockJsonProvider(BaseProvider):
    """
    Base provider for loading canonical NEXUS mock JSON files.
    """

    def __init__(
        self,
        file_path: str | Path,
        model: type[BaseModel],
        provider_name: str,
    ):
        self.file_path = Path(file_path)
        self.model = model
        self.name = provider_name
        self.mode = "mock"

    async def fetch(self, incident_id: str) -> ProviderResponse:
        try:
            if not self.file_path.exists():
                raise ProviderError(
                    category=ProviderErrorCategory.UNAVAILABLE,
                    message=f"Mock file not found: {self.file_path}",
                    provider=self.name,
                )

            with self.file_path.open(
                "r",
                encoding="utf-8",
            ) as file:
                raw_data: dict[str, Any] = json.load(file)

            data = self.model.model_validate(raw_data)

            if isinstance(data, Incident) and data.incident_id != incident_id:
                raise ProviderError(
                    category=ProviderErrorCategory.NOT_FOUND,
                    message=f"Incident {incident_id} is not present in the mock fixture",
                    provider=self.name,
                )

            return ProviderResponse(
                data=data,
                provider=self.name,
                mode="mock",
                source=str(self.file_path),
                metadata={
                    "incident_id": incident_id,
                },
            )

        except ProviderError:
            raise

        except json.JSONDecodeError as exc:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_RESPONSE,
                message=f"Invalid JSON: {exc}",
                provider=self.name,
            ) from exc

        except Exception as exc:
            raise ProviderError(
                category=ProviderErrorCategory.INVALID_RESPONSE,
                message=str(exc),
                provider=self.name,
            ) from exc


# ============================================================
# Individual Evidence Providers
# ============================================================

class MockIncidentProvider(MockJsonProvider):
    def __init__(self, mocks_dir: Path):
        super().__init__(
            file_path=mocks_dir / "mock_incident.json",
            model=Incident,
            provider_name="mock_incident",
        )


class MockTelemetryProvider(MockJsonProvider):
    def __init__(self, mocks_dir: Path):
        super().__init__(
            file_path=mocks_dir / "mock_metrics.json",
            model=TelemetrySnapshot,
            provider_name="mock_telemetry",
        )


class MockAnomalyProvider(MockJsonProvider):
    def __init__(self, mocks_dir: Path):
        super().__init__(
            file_path=mocks_dir / "mock_anomaly_event.json",
            model=AnomalyEvent,
            provider_name="mock_anomaly",
        )


class MockDeploymentProvider(MockJsonProvider):
    def __init__(self, mocks_dir: Path):
        super().__init__(
            file_path=mocks_dir / "mock_deployment_event.json",
            model=DeploymentEvent,
            provider_name="mock_deployment",
        )
