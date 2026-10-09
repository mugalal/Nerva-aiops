"""TEST ONLY: M1 with controlled evidence behind a real loopback HTTP server.

No Prometheus, Loki, Kubernetes, or payment workload is contacted. Values are
explicit fixtures. The real-mode interface exercises production validation
and storage code and is not a claim about live provider data.
"""

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
from typing import Literal
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services/telemetry-intelligence"))
sys.path.insert(1, str(REPO))

from app.config import load_m1_settings
from app.main import create_app
from app.models import (DeploymentEvent, KubernetesContext, ResourceConfiguration,
                        TelemetryMetrics, TelemetrySnapshot, TelemetryWindow, TelemetryWindowPoint)
from app.providers.loki import LogEntry
from app.service import M1Service
from app.storage import EvidenceStore


def now():
    return datetime.now(timezone.utc)


class State:
    def __init__(self):
        self.events = [(datetime(2000, 1, 1, tzinfo=timezone.utc), "baseline", 1)]

    def update(self, phase, replicas=1):
        self.events.append((now(), phase, replicas))

    def at(self, timestamp):
        return next(event for event in reversed(self.events) if event[0] <= timestamp)

    def snapshot_at(self, service, timestamp):
        _, phase, replicas = self.at(timestamp)
        spike = phase in {"spike", "scaled"}
        faulty = phase == "fault"
        metrics = TelemetryMetrics(cpu=0.9 if phase == "spike" else 0.7 if faulty else 0.1,
            memory=0.2, request_rate=90 if spike else 30,
            latency_p95_ms=578 if faulty else 35 if phase == "spike" else 10,
            http_5xx_rate=0.14 if faulty else 0, replica_count=replicas)
        return TelemetrySnapshot(timestamp=timestamp, service=service,
                                 version="v2" if faulty else "v1", metrics=metrics)


state = State()


class ControlledTelemetry:
    mode = "real"

    async def ready(self):
        return True, "Controlled telemetry fixture; no live Prometheus"

    async def snapshot(self, service):
        return state.snapshot_at(service, now())

    async def window(self, service, start, end, step_seconds):
        points = [state.snapshot_at(service, start + timedelta(seconds=index * step_seconds))
                  for index in range(math.floor((end - start).total_seconds() / step_seconds) + 1)]
        versions = {point.version for point in points}
        if len(versions) != 1:
            from app.errors import TelemetryMissing
            raise TelemetryMissing("Controlled window spans different fixture versions")
        return TelemetryWindow(service=service, version=versions.pop(), start=start, end=end,
            step_seconds=step_seconds,
            points=[TelemetryWindowPoint(timestamp=point.timestamp, metrics=point.metrics) for point in points])


class ControlledLoki:
    async def ready(self):
        return True, "Controlled log fixture; no live Loki"

    async def query_logs(self, service, **kwargs):
        return [LogEntry(timestamp=now(), labels={"service_name": service},
            line='{"level":"INFO","message":"controlled fixture","route":"/pay","status_code":200}')]


class ControlledKubernetes:
    async def ready(self):
        return True, "Controlled deployment fixture; no live Kubernetes"

    async def context(self, service, namespace):
        current = state.snapshot_at(service, now())
        return KubernetesContext(service=service, namespace=namespace,
            desired_replicas=current.metrics.replica_count, ready_replicas=current.metrics.replica_count,
            pod_restarts=0, version=current.version, service_health="ok", events=["Controlled fixture"])

    async def deployment_event(self, service, namespace):
        changed_at, phase, _ = state.at(now())
        timestamp = changed_at if phase != "baseline" else now() - timedelta(minutes=10)
        return DeploymentEvent(event_id="DEP-CONTROLLED", service=service,
            old_version="v1", new_version="v2" if phase == "fault" else "v1",
            commit_sha="controlled-fixture", pipeline_id="controlled-http-test",
            timestamp=timestamp, status="SUCCESS")

    async def resource_config(self, service, namespace):
        return ResourceConfiguration(cpu_request_m=100, cpu_limit_m=500,
                                     memory_request_mb=128, memory_limit_mb=512)


class ControlledHealth:
    async def check(self, service):
        return True, "Controlled payment-health fixture; no live payment service"


settings = load_m1_settings()
service = M1Service(settings, ControlledTelemetry(), ControlledLoki(), ControlledKubernetes(),
                    ControlledHealth(), EvidenceStore(settings.data_dir))
app = create_app(settings, service)


class FixtureUpdate(BaseModel):
    phase: Literal["baseline", "fault", "spike", "rolled_back", "scaled"]
    replicas: int = Field(default=1, ge=1, le=10)


@app.post("/__test/state")
async def update_fixture(request: FixtureUpdate):
    state.update(request.phase, request.replicas)
    return {"fixture_only": True, "phase": request.phase, "replicas": request.replicas}
