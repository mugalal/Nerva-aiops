from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging

from .baseline import build_healthy_baseline
from .config import M1Settings, duration_seconds
from .errors import BaselineNotFound, M1Error, ProviderUnavailable
from .models import (
    CaptureEvidenceRequest,
    DependencyHealth,
    HealthResponse,
    HealthyBaseline,
    EvidencePreview,
    IncidentEvidence,
    RecoveryMetrics,
    RecoveryEvidenceRecord,
    RecoveryResult,
    RecoveryValidationRequest,
    TelemetrySnapshot,
    TelemetryWindow,
    TimeWindowRequest,
)
from .providers.health import ServiceHealthClient
from .providers.kubernetes import KubectlKubernetesClient
from .providers.loki import LokiClient
from .storage import EvidenceStore
from .telemetry import TelemetryProvider


logger = logging.getLogger(__name__)


def _select_incident_logs(entries, limit: int = 20) -> list[str]:
    relevant = []
    for entry in entries:
        line = entry.line
        selected = False
        try:
            payload = json.loads(line)
            level = str(payload.get("level", "")).upper()
            status_code = int(payload.get("status_code", 0) or 0)
            selected = level in {"ERROR", "WARNING", "CRITICAL"} or status_code >= 500
        except (TypeError, ValueError, json.JSONDecodeError):
            lowered = line.lower()
            selected = "error" in lowered or "failed" in lowered or " 500 " in lowered
        if selected:
            relevant.append(entry)

    chosen = relevant[-limit:] if relevant else entries[-limit:]
    return [f"{entry.timestamp.isoformat()} {entry.line}" for entry in chosen]


class M1Service:
    def __init__(
        self,
        settings: M1Settings,
        telemetry: TelemetryProvider,
        loki: LokiClient,
        kubernetes: KubectlKubernetesClient,
        service_health: ServiceHealthClient,
        store: EvidenceStore,
    ):
        self.settings = settings
        self.telemetry = telemetry
        self.loki = loki
        self.kubernetes = kubernetes
        self.service_health = service_health
        self.store = store

    async def health(self) -> HealthResponse:
        dependencies: dict[str, DependencyHealth] = {}
        storage_ok, storage_detail = self.store.ready()
        dependencies["evidence_storage"] = DependencyHealth(
            status="ok" if storage_ok else "unavailable", detail=storage_detail,
        )
        (telemetry_ok, telemetry_detail), (loki_ok, loki_detail), (kubernetes_ok, kubernetes_detail), (payment_ok, payment_detail) = await asyncio.gather(
            self.telemetry.ready(), self.loki.ready(), self.kubernetes.ready(),
            self.service_health.check("payment-service"),
        )
        dependencies["telemetry_provider"] = DependencyHealth(
            status="ok" if telemetry_ok else "unavailable",
            detail=telemetry_detail,
        )

        dependencies["loki"] = DependencyHealth(
            status="ok" if loki_ok else "degraded",
            detail=loki_detail,
        )

        dependencies["kubernetes"] = DependencyHealth(
            status="ok" if kubernetes_ok else "degraded",
            detail=kubernetes_detail,
        )

        dependencies["payment_service"] = DependencyHealth(
            status="ok" if payment_ok else "unavailable", detail=payment_detail,
        )
        data_ok = True
        if self.settings.provider_mode == "real" and telemetry_ok:
            try:
                await self.snapshot("payment-service")
                dependencies["telemetry_data"] = DependencyHealth(status="ok", detail="Fresh payment telemetry is available")
            except M1Error as exc:
                data_ok = False
                dependencies["telemetry_data"] = DependencyHealth(status="degraded", detail=exc.message)

        if self.settings.provider_mode == "mock":
            overall = "degraded"
        elif not telemetry_ok or not payment_ok or not storage_ok:
            overall = "unavailable"
        elif not loki_ok or not kubernetes_ok or not data_ok:
            overall = "degraded"
        else:
            overall = "ok"

        return HealthResponse(
            service=self.settings.runtime.service_name,
            status=overall,
            version=self.settings.runtime.service_version,
            environment=self.settings.runtime.environment,
            provider_mode=self.settings.provider_mode,
            dependencies=dependencies,
        )

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        return await self.telemetry.snapshot(service)

    async def window(self, request: TimeWindowRequest) -> TelemetryWindow:
        if request.end > datetime.now(timezone.utc):
            raise M1Error("invalid_window", "Telemetry window cannot end in the future",
                          status_code=422, retryable=False)
        return await self.telemetry.window(
            request.service,
            request.start,
            request.end,
            request.step_seconds,
        )

    async def measure_baseline(self, request: TimeWindowRequest) -> HealthyBaseline:
        if self.telemetry.mode != "real":
            raise M1Error("real_telemetry_required", "Healthy baselines require real telemetry",
                          status_code=409, retryable=False)
        window = await self.window(request)
        baseline = build_healthy_baseline(window, self.settings)
        self.store.save_baseline(baseline)
        logger.info(
            "healthy baseline measured",
            extra={"service_name": request.service, "provider": self.telemetry.mode},
        )
        return baseline

    async def preview_evidence(self, service: str) -> EvidencePreview:
        """Collect live diagnosis inputs without requiring a classified incident."""
        now = datetime.now(timezone.utc)

        async def optional(fetch):
            try:
                return await fetch(), None
            except M1Error as exc:
                return None, exc.message

        async def resources():
            if not hasattr(self.kubernetes, "resource_config"):
                raise ProviderUnavailable("kubernetes", "Resource configuration is not available")
            return await self.kubernetes.resource_config(service, self.settings.namespace)

        telemetry, logs, context, deployment, configuration = await asyncio.gather(
            self.snapshot(service),
            optional(lambda: self.loki.query_logs(service, start=now - timedelta(minutes=5),
                                                  end=now, limit=self.settings.log_limit)),
            optional(lambda: self.kubernetes.context(service, self.settings.namespace)),
            optional(lambda: self.kubernetes.deployment_event(service, self.settings.namespace)),
            optional(resources),
        )
        try:
            baseline = self.store.get_baseline(service)
        except BaselineNotFound:
            baseline = None
        return EvidencePreview(
            service=service, provider_mode=self.telemetry.mode,
            collected_at=datetime.now(timezone.utc), telemetry=telemetry,
            selected_logs=_select_incident_logs(logs[0]) if logs[0] is not None else [],
            kubernetes=context[0], deployment_event=deployment[0], resource_config=configuration[0],
            provider_errors=[error for _, error in (logs, context, deployment, configuration) if error],
            baseline=baseline,
        )

    async def capture_evidence(
        self, request: CaptureEvidenceRequest
    ) -> IncidentEvidence:
        now = datetime.now(timezone.utc)
        before = await self.snapshot(request.service)
        provider_errors: list[str] = []
        selected_logs: list[str] = []
        kubernetes = None
        deployment_event = None
        try:
            baseline = self.store.get_baseline(request.service)
        except BaselineNotFound:
            baseline = None

        try:
            log_entries = await self.loki.query_logs(
                request.service,
                start=now - timedelta(minutes=5),
                end=now,
                limit=self.settings.log_limit,
            )
            selected_logs = _select_incident_logs(log_entries)
        except M1Error as exc:
            provider_errors.append(exc.message)

        try:
            kubernetes = await self.kubernetes.context(
                request.service, self.settings.namespace
            )
            deployment_event = await self.kubernetes.deployment_event(
                request.service, self.settings.namespace
            )
        except M1Error as exc:
            provider_errors.append(exc.message)

        evidence = IncidentEvidence(
            incident_id=request.incident_id,
            service=request.service,
            scenario=request.scenario,
            captured_at=now,
            before=before,
            selected_logs=selected_logs,
            kubernetes=kubernetes,
            deployment_event=deployment_event,
            provider_errors=provider_errors,
            baseline=baseline,
        )
        self.store.save_evidence(evidence)
        logger.info(
            "incident evidence captured",
            extra={
                "incident_id": request.incident_id,
                "service_name": request.service,
                "scenario": request.scenario,
            },
        )
        return evidence

    async def validate_recovery(
        self, request: RecoveryValidationRequest
    ) -> RecoveryResult:
        now = datetime.now(timezone.utc)
        action_completed_at = request.action_completed_at.astimezone(timezone.utc)
        if action_completed_at > now:
            raise M1Error("invalid_action_time", "Action completion cannot be in the future",
                          status_code=422, retryable=False)
        evidence = self.store.get_evidence(request.incident_id)
        if evidence.service != request.service:
            raise M1Error(
                "evidence_service_mismatch",
                f"Incident evidence is for {evidence.service}, not {request.service}",
                status_code=409,
                retryable=False,
            )
        if request.scenario != evidence.scenario:
            raise M1Error("evidence_scenario_mismatch", "Recovery scenario must match captured evidence",
                          status_code=409, retryable=False)
        if action_completed_at < evidence.captured_at:
            raise M1Error("invalid_action_time", "Action completion must follow evidence capture",
                          status_code=422, retryable=False)
        if self.telemetry.mode != "real":
            raise M1Error("real_telemetry_required", "Recovery validation requires real telemetry",
                          status_code=409, retryable=False)
        baseline = evidence.baseline
        if baseline is None:
            # Preserve safe reads of evidence captured before baseline pinning.
            baseline = self.store.get_baseline(request.service)
        if baseline.measured_at > evidence.captured_at or baseline.window_end > evidence.captured_at:
            raise M1Error("incident_baseline_missing", "Recovery requires a healthy baseline collected before incident capture",
                          status_code=409, retryable=False)
        rollback_version = (evidence.deployment_event.old_version
                            if evidence.deployment_event is not None else baseline.version)
        if request.scenario == "bad_deployment" and baseline.version != rollback_version:
            raise M1Error("baseline_version_mismatch", "Captured baseline does not match the previous deployment version",
                          status_code=409, retryable=False)
        after = await self.snapshot(request.service)
        now = datetime.now(timezone.utc)
        decision_window_seconds = max(duration_seconds(self.settings.request_rate_window),
                                      duration_seconds(self.settings.latency_window))
        earliest_measurement = action_completed_at + timedelta(seconds=decision_window_seconds)
        end = after.timestamp.astimezone(timezone.utc)
        start = end - timedelta(seconds=self.settings.recovery_hold_seconds)
        if end > now or start < earliest_measurement:
            raise M1Error(
                "recovery_pending",
                "Wait for the decision windows to contain post-action data and a complete recovery hold",
                status_code=409, retryable=True,
            )
        window = await self.telemetry.window(request.service, start, end,
                                             self.settings.history_step_seconds)
        timestamps = [point.timestamp for point in window.points]
        expected = int(self.settings.recovery_hold_seconds // self.settings.history_step_seconds) + 1
        if (len(timestamps) < max(expected, self.settings.recovery_min_samples)
                or timestamps != sorted(set(timestamps))
                or not timestamps or (timestamps[0] - start).total_seconds() < -0.001
                or (timestamps[-1] - end).total_seconds() > 0.001
                or (timestamps[0] - start).total_seconds() > 1
                or (end - timestamps[-1]).total_seconds() >= self.settings.history_step_seconds
                or any((right - left).total_seconds() > self.settings.history_step_seconds * 1.5
                       for left, right in zip(timestamps, timestamps[1:]))):
            raise M1Error("recovery_data_incomplete", "Recovery requires a complete post-action measurement window",
                          status_code=503, retryable=True)
        service_ok, service_detail = await self.service_health.check(request.service)

        thresholds = baseline.thresholds
        measurements = [point.metrics for point in window.points] + [after.metrics]
        slo_restored = all(
            metrics.latency_p95_ms <= thresholds.latency_p95_ms_max
            and metrics.http_5xx_rate <= thresholds.http_5xx_rate_max
            for metrics in measurements
        )

        scenario = request.scenario
        if scenario == "traffic_spike":
            minimum_live_traffic = (
                evidence.before.metrics.request_rate
                * self.settings.traffic_continuity_ratio
            )
            scenario_checks_passed = (
                after.version == evidence.before.version
                and window.version == evidence.before.version
                and all(
                metrics.replica_count > evidence.before.metrics.replica_count
                and metrics.request_rate >= max(minimum_live_traffic, self.settings.baseline_min_request_rate)
                and metrics.latency_p95_ms < evidence.before.metrics.latency_p95_ms
                for metrics in measurements
                )
            )
        else:
            scenario_checks_passed = (
                after.version == rollback_version and window.version == rollback_version
                and all(metrics.replica_count > 0
                        and metrics.request_rate >= self.settings.baseline_min_request_rate
                        for metrics in measurements)
            )

        recovered = service_ok and slo_restored and scenario_checks_passed
        now = datetime.now(timezone.utc)
        result = RecoveryResult(
            incident_id=request.incident_id,
            recovered=recovered,
            before=RecoveryMetrics(
                latency_p95_ms=evidence.before.metrics.latency_p95_ms,
                http_5xx_rate=evidence.before.metrics.http_5xx_rate,
            ),
            after=RecoveryMetrics(
                latency_p95_ms=after.metrics.latency_p95_ms,
                http_5xx_rate=after.metrics.http_5xx_rate,
            ),
            recovery_time_seconds=(now - action_completed_at).total_seconds(),
            slo_restored=slo_restored,
        )
        self.store.save_recovery(
            RecoveryEvidenceRecord(
                incident_id=request.incident_id,
                service=request.service,
                scenario=request.scenario,
                action_completed_at=action_completed_at,
                validated_at=now,
                before=evidence.before,
                after=after,
                result=result,
                measurement_window=window,
            )
        )
        logger.info(
            "recovery validation completed",
            extra={
                "incident_id": request.incident_id,
                "service_name": request.service,
                "scenario": request.scenario,
                "provider": self.telemetry.mode,
                "error_category": None if recovered else service_detail,
            },
        )
        return result
