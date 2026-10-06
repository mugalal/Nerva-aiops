from pathlib import Path

from .models import AnalyzeRequest, EvidenceBundle, RCAResult
from .providers.mock import (
    MockAnomalyProvider,
    MockDeploymentProvider,
    MockIncidentProvider,
    MockTelemetryProvider,
)
from .scoring import determine_root_cause


class RCAService:
    """
    Main M3 Root Cause Analysis service.

    Responsibilities:
    1. Collect incident evidence from providers.
    2. Build a complete EvidenceBundle.
    3. Run deterministic RCA scoring.
    4. Return the frozen public RCAResult contract.
    """

    def __init__(self, mocks_dir: Path):
        self.incident_provider = MockIncidentProvider(mocks_dir)
        self.telemetry_provider = MockTelemetryProvider(mocks_dir)
        self.anomaly_provider = MockAnomalyProvider(mocks_dir)
        self.deployment_provider = MockDeploymentProvider(mocks_dir)

    async def analyze(self, request: AnalyzeRequest) -> RCAResult:
        provider_errors: list[str] = []

        # ---------------------------------------------------------
        # 1. Collect incident
        # ---------------------------------------------------------
        incident_response = await self.incident_provider.fetch(
            request.incident_id
        )

        incident = incident_response.data

        if incident is None:
            raise ValueError(
                f"Incident provider returned no data for "
                f"{request.incident_id}"
            )

        # ---------------------------------------------------------
        # 2. Collect telemetry
        # ---------------------------------------------------------
        telemetry = None

        try:
            response = await self.telemetry_provider.fetch(
                request.incident_id
            )
            telemetry = response.data
        except Exception as exc:
            provider_errors.append(f"telemetry: {exc}")

        # ---------------------------------------------------------
        # 3. Collect anomaly
        # ---------------------------------------------------------
        anomaly = None

        try:
            response = await self.anomaly_provider.fetch(
                request.incident_id
            )
            anomaly = response.data
        except Exception as exc:
            provider_errors.append(f"anomaly: {exc}")

        # ---------------------------------------------------------
        # 4. Collect deployment
        # ---------------------------------------------------------
        deployment = None

        try:
            response = await self.deployment_provider.fetch(
                request.incident_id
            )
            deployment = response.data
        except Exception as exc:
            provider_errors.append(f"deployment: {exc}")

        # ---------------------------------------------------------
        # 5. Build the evidence bundle
        # ---------------------------------------------------------
        evidence_bundle = EvidenceBundle(
            incident=incident,
            telemetry=telemetry,
            anomaly=anomaly,
            deployment=deployment,
            provider_errors=provider_errors,
        )

        # ---------------------------------------------------------
        # 6. Deterministic root-cause analysis
        # ---------------------------------------------------------
        scoring_result = determine_root_cause(
            evidence_bundle
        )

        # ---------------------------------------------------------
        # 7. Return the frozen public RCAResult
        # ---------------------------------------------------------
        return RCAResult(
            incident_id=incident.incident_id,
            root_cause=scoring_result.root_cause,
            affected_component=scoring_result.affected_component,
            confidence=scoring_result.confidence,
            evidence=scoring_result.evidence,
        )