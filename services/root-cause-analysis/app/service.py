"""M3 diagnoses only the requested incident's actual evidence."""

import asyncio
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path

from .models import AnalyzeRequest, EvidenceBundle, RCAResult
from .providers.base import ProviderError, ProviderErrorCategory
from .providers.http import HttpEvidenceProviders
from .providers.mock import (
    MockAnomalyProvider, MockDeploymentProvider, MockIncidentProvider, MockTelemetryProvider,
)
from .scoring import determine_root_cause


class RCAService:
    def __init__(self, mocks_dir: Path | None = None, mode: str | None = None,
                 providers: HttpEvidenceProviders | None = None, clock=None):
        self.mode = (mode or os.getenv("M3_PROVIDER_MODE", "real")).lower()
        if self.mode not in {"real", "mock"}:
            raise ValueError("M3_PROVIDER_MODE must be real or mock")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_age = float(os.getenv("M3_EVIDENCE_MAX_AGE_SECONDS", "90"))
        if not math.isfinite(self.max_age) or not 0 < self.max_age <= 3600:
            raise ValueError("M3_EVIDENCE_MAX_AGE_SECONDS must be between 0 and 3600")
        if self.mode == "real":
            self.providers = providers or HttpEvidenceProviders(
                os.getenv("M1_TELEMETRY_BASE_URL", "http://localhost:8001"),
                os.getenv("SHARED_NEXUS_API_BASE_URL",
                          os.getenv("M4_DECISION_BASE_URL", "http://localhost:8004")),
            )
        else:
            mocks_dir = mocks_dir or Path(__file__).resolve().parents[3] / "mocks"
            self.incident_provider = MockIncidentProvider(mocks_dir)
            self.telemetry_provider = MockTelemetryProvider(mocks_dir)
            self.anomaly_provider = MockAnomalyProvider(mocks_dir)
            self.deployment_provider = MockDeploymentProvider(mocks_dir)

    @staticmethod
    def invalid(message, provider):
        raise ProviderError(ProviderErrorCategory.INVALID_RESPONSE, message, provider)

    def validate_incident(self, incident, requested_id):
        if incident.incident_id != requested_id:
            self.invalid("Incident response does not match the requested incident_id", "incident")
        if any(not service.strip() for service in incident.affected_services):
            self.invalid("Incident has an empty affected service", "incident")
        if self.mode == "real" and incident.started_at > self.clock() + timedelta(seconds=5):
            self.invalid("Incident started_at is in the future", "incident")

    def validate_anomaly(self, anomaly, incident):
        if anomaly.anomaly_id not in incident.anomaly_ids:
            self.invalid("Anomaly is not linked to the requested incident", "anomaly")
        if anomaly.service not in incident.affected_services:
            self.invalid("Anomaly service is not affected by this incident", "anomaly")
        if self.mode == "real":
            now = self.clock()
            if (anomaly.timestamp > now + timedelta(seconds=5)
                    or (now - anomaly.timestamp).total_seconds() > 300
                    or anomaly.timestamp < incident.started_at - timedelta(seconds=60)):
                self.invalid("Anomaly timestamp is future, stale, or precedes this incident", "anomaly")

    def validate_preview(self, preview, service, incident=None):
        now = self.clock()
        if preview.provider_mode != "real":
            self.invalid("Real RCA requires real M1 evidence; mock evidence is not accepted", "telemetry")
        if preview.service != service or preview.telemetry.service != service:
            self.invalid("M1 preview contains another service's telemetry", "telemetry")
        if (preview.collected_at > now + timedelta(seconds=5)
                or (now - preview.collected_at).total_seconds() > self.max_age
                or preview.telemetry.timestamp > preview.collected_at + timedelta(seconds=5)
                or (preview.collected_at - preview.telemetry.timestamp).total_seconds() > self.max_age):
            self.invalid("M1 preview contains future or stale telemetry timestamps", "telemetry")
        deployment = preview.deployment_event
        if deployment is not None:
            if (deployment.service != service
                    or deployment.new_version != preview.telemetry.version
                    or deployment.timestamp > preview.collected_at + timedelta(seconds=5)):
                self.invalid("Deployment evidence does not match the observed service/version/time", "deployment")
        baseline = preview.baseline
        if baseline is not None:
            if (baseline.service != service
                    or baseline.window_end > baseline.measured_at
                    or baseline.measured_at > now + timedelta(seconds=5)
                    or (incident is not None and baseline.measured_at > incident.started_at + timedelta(seconds=5))
                    or (baseline.window_end - baseline.window_start).total_seconds() < 60
                    or baseline.sample_count < 5
                    or baseline.request_rate.average < 0.1):
                self.invalid("Baseline identity, timing, or coverage is invalid", "baseline")
            for name in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu", "memory", "replica_count"):
                statistic = getattr(baseline, name)
                # fmean/percentile arithmetic may differ by a few floating-point
                # bits from identical source samples. Keep finite model checks
                # and reject material inconsistencies without rejecting that noise.
                tolerance = max(1.0, statistic.maximum, statistic.minimum) * 1e-9
                if not (statistic.minimum - tolerance <= statistic.average <= statistic.maximum + tolerance
                        and statistic.minimum - tolerance <= statistic.p95 <= statistic.maximum + tolerance):
                    self.invalid("Baseline statistics are inconsistent", "baseline")

    async def health(self):
        runtime = {
            "service": os.getenv("SERVICE_NAME", "root-cause-analysis"),
            "version": os.getenv("SERVICE_VERSION", "0.1.0"),
            "environment": os.getenv("ENVIRONMENT", "development"),
            "provider_mode": self.mode,
        }
        if self.mode == "mock":
            return {**runtime, "status": "degraded", "dependencies": {
                "evidence": {"status": "degraded", "detail": "Explicit mock mode"}}}
        results = await asyncio.gather(self.providers.incident_list(),
                                       self.providers.preview("payment-service"), return_exceptions=True)
        dependencies = {}
        for name, result in zip(("incident", "telemetry"), results):
            if isinstance(result, Exception):
                dependencies[name] = {"status": "unavailable", "detail": str(result)}
            else:
                try:
                    if name == "telemetry":
                        self.validate_preview(result, "payment-service")
                    source_errors = list(result.provider_errors) if name == "telemetry" else []
                    if name == "telemetry" and result.baseline is None:
                        source_errors.append("Measured healthy baseline is missing")
                    dependencies[name] = {"status": "degraded" if source_errors else "ok",
                                          "detail": "; ".join(source_errors) if source_errors else "Real provider available"}
                except ProviderError as exc:
                    dependencies[name] = {"status": "unavailable", "detail": str(exc)}
        statuses = [value["status"] for value in dependencies.values()]
        status = "unavailable" if "unavailable" in statuses else "degraded" if "degraded" in statuses else "ok"
        return {**runtime, "status": status, "dependencies": dependencies}

    async def analyze(self, request: AnalyzeRequest) -> RCAResult:
        errors = []
        if self.mode == "real":
            incident = await self.providers.incident(request.incident_id)
            self.validate_incident(incident, request.incident_id)
            anomaly = None
            try:
                anomaly = await self.providers.anomaly(request.incident_id)
                self.validate_anomaly(anomaly, incident)
            except ProviderError as exc:
                if exc.category == ProviderErrorCategory.INVALID_RESPONSE:
                    raise
                errors.append(str(exc))
            service = anomaly.service if anomaly is not None else incident.affected_services[0]
            preview = await self.providers.preview(service)
            self.validate_preview(preview, service, incident)
            errors.extend(preview.provider_errors)
            if preview.baseline is None:
                errors.append("Measured healthy baseline is missing; collect it before incident diagnosis")
            bundle = EvidenceBundle(incident=incident, telemetry=preview.telemetry, anomaly=anomaly,
                                    deployment=preview.deployment_event, baseline=preview.baseline,
                                    provider_errors=errors)
        else:
            incident = (await self.incident_provider.fetch(request.incident_id)).data
            self.validate_incident(incident, request.incident_id)
            evidence = {}
            for name in ("telemetry", "anomaly", "deployment"):
                try:
                    evidence[name] = (await getattr(self, f"{name}_provider").fetch(request.incident_id)).data
                except ProviderError as exc:
                    errors.append(str(exc))
                    evidence[name] = None
            if evidence["anomaly"] is not None:
                self.validate_anomaly(evidence["anomaly"], incident)
            if evidence["telemetry"] is not None and evidence["telemetry"].service not in incident.affected_services:
                self.invalid("Mock telemetry belongs to another service", "telemetry")
            bundle = EvidenceBundle(incident=incident, provider_errors=errors, **evidence)
        result = determine_root_cause(bundle)
        component = result.affected_component
        if (self.mode == "real" and result.root_cause in {"faulty_deployment", "traffic_spike"}
                and bundle.telemetry is not None):
            # Bind an executable diagnosis to the exact version observed by M3.
            # M4 must reject a later capture of another version before acting.
            component = f"{bundle.telemetry.service}:{bundle.telemetry.version}"
        return RCAResult(incident_id=request.incident_id, root_cause=result.root_cause,
                         affected_component=component, confidence=result.confidence,
                         evidence=result.evidence + [f"Evidence unavailable: {error}" for error in errors])
