"""Observable demo payment service used by both NEXUS P0 scenarios."""

from datetime import datetime, timezone
import asyncio
from contextlib import asynccontextmanager
import copy
from collections import Counter as RecordCounts
import hashlib
import json
import logging
from logging.handlers import QueueHandler, QueueListener
import os
import math
import queue
import random
import socket
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest


SERVICE_NAME = os.getenv("SERVICE_NAME", "payment-service")
VERSION = os.getenv("VERSION", "v1")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
NAMESPACE = os.getenv("NAMESPACE", "nexus-demo")
POD = os.getenv("POD_NAME", socket.gethostname())
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
FAULT_LATENCY_MS = int(os.getenv("FAULT_LATENCY_MS", "600" if VERSION == "v2" else "0"))
FAULT_5XX_RATE = float(os.getenv("FAULT_5XX_RATE", "0.14" if VERSION == "v2" else "0.0"))
CPU_LIMIT_CORES = float(os.getenv("CPU_LIMIT_CORES", "1"))
MEMORY_LIMIT_BYTES = float(os.getenv("MEMORY_LIMIT_BYTES", "536870912"))
PAYMENT_WORK_ITERATIONS = int(os.getenv("PAYMENT_WORK_ITERATIONS", "0"))
if not 0 <= PAYMENT_WORK_ITERATIONS <= 1_000_000:
    raise ValueError("PAYMENT_WORK_ITERATIONS must be between 0 and 1000000")
LOG_QUEUE_MAX_RECORDS = int(os.getenv("LOG_QUEUE_MAX_RECORDS", "1024"))
if not 1 <= LOG_QUEUE_MAX_RECORDS <= 100_000:
    raise ValueError("LOG_QUEUE_MAX_RECORDS must be between 1 and 100000")
LOG_SHUTDOWN_TIMEOUT_SECONDS = float(os.getenv("LOG_SHUTDOWN_TIMEOUT_SECONDS", "5"))
if not math.isfinite(LOG_SHUTDOWN_TIMEOUT_SECONDS) or not 0 < LOG_SHUTDOWN_TIMEOUT_SECONDS <= 30:
    raise ValueError("LOG_SHUTDOWN_TIMEOUT_SECONDS must be positive and at most 30")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "version": VERSION,
            "environment": ENVIRONMENT,
            "message": record.getMessage(),
        }
        for key in ("method", "route", "status_code", "duration_ms", "pod", "namespace"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, separators=(",", ":"))


class NonblockingLogHandler(QueueHandler):
    accepting = True

    def prepare(self, record):
        # Keep formatting and stream I/O on the listener, away from requests.
        return copy.copy(record)

    def enqueue(self, record):
        if not self.accepting:
            LOG_RECORDS_DROPPED.labels(*COMMON_LABEL_VALUES, record.levelname).inc()
            return
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            LOG_RECORDS_DROPPED.labels(*COMMON_LABEL_VALUES, record.levelname).inc()


class DrainingLogListener(QueueListener):
    _stop_requested = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Previous log listener is still draining; cannot start another listener")
        if self._stop_requested:
            self._discard_pending()
        self._stop_requested = False
        handler.accepting = True
        super().start()

    def _discard_pending(self):
        discarded = RecordCounts()
        for _ in range(self.queue.qsize()):
            try:
                record = self.queue.get_nowait()
            except queue.Empty:
                break
            if record is not self._sentinel:
                discarded[record.levelname] += 1
            self.queue.task_done()
        for level, count in discarded.items():
            LOG_RECORDS_DROPPED.labels(*COMMON_LABEL_VALUES, level).inc(count)

    def stop(self):
        listener_thread = self._thread
        if listener_thread is None:
            return
        deadline = time.monotonic() + LOG_SHUTDOWN_TIMEOUT_SECONDS
        with handler.lock:
            handler.accepting = False
        if not self._stop_requested:
            self._stop_requested = True
            try:
                self.queue.put(self._sentinel, timeout=max(0, deadline - time.monotonic()))
            except queue.Full:
                self._discard_pending()
                self.queue.put_nowait(self._sentinel)
        listener_thread.join(timeout=max(0, deadline - time.monotonic()))
        if listener_thread.is_alive():
            # A blocked sink cannot hold application shutdown indefinitely.
            # Account pending records; the daemon exits if the sink returns.
            self._discard_pending()
            self.queue.put_nowait(self._sentinel)
        else:
            self._thread = None


log_queue = queue.Queue(maxsize=LOG_QUEUE_MAX_RECORDS)
log_sink = logging.StreamHandler()
log_sink.setFormatter(JsonFormatter())
log_listener = DrainingLogListener(log_queue, log_sink)
handler = NonblockingLogHandler(log_queue)
logger = logging.getLogger(SERVICE_NAME)
logger.handlers.clear()
logger.addHandler(handler)
logger.setLevel(LOG_LEVEL)
logger.propagate = False

METRIC_LABELS = ("service_name", "namespace", "pod", "version", "environment")
REQUESTS = Counter(
    "nexus_http_requests_total",
    "HTTP requests handled by a NEXUS service.",
    METRIC_LABELS + ("method", "route", "status_code"),
)
REQUEST_DURATION = Histogram(
    "nexus_http_request_duration_seconds",
    "HTTP request duration for a NEXUS service.",
    METRIC_LABELS + ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0),
)
SERVICE_INFO = Gauge(
    "nexus_service_info",
    "Static service identity used for version and replica discovery.",
    METRIC_LABELS,
)
CPU_LIMIT = Gauge(
    "nexus_resource_limit_cpu_cores",
    "Configured CPU limit in cores.",
    METRIC_LABELS,
)
MEMORY_LIMIT = Gauge(
    "nexus_resource_limit_memory_bytes",
    "Configured memory limit in bytes.",
    METRIC_LABELS,
)
LOG_RECORDS_DROPPED = Counter(
    "nexus_log_records_dropped_total",
    "Log records dropped because the bounded output queue was full or shutdown timed out.",
    METRIC_LABELS + ("level",),
)

COMMON_LABEL_VALUES = (SERVICE_NAME, NAMESPACE, POD, VERSION, ENVIRONMENT)
SERVICE_INFO.labels(*COMMON_LABEL_VALUES).set(1)
CPU_LIMIT.labels(*COMMON_LABEL_VALUES).set(CPU_LIMIT_CORES)
MEMORY_LIMIT.labels(*COMMON_LABEL_VALUES).set(MEMORY_LIMIT_BYTES)


@asynccontextmanager
async def lifespan(_app):
    log_listener.start()
    try:
        yield
    finally:
        await asyncio.to_thread(log_listener.stop)


app = FastAPI(title=SERVICE_NAME, version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def observe_http_request(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_seconds = time.perf_counter() - started
        route = request.url.path
        REQUESTS.labels(
            *COMMON_LABEL_VALUES,
            request.method,
            route,
            str(status_code),
        ).inc()
        REQUEST_DURATION.labels(
            *COMMON_LABEL_VALUES,
            request.method,
            route,
        ).observe(duration_seconds)
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "route": route,
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 2),
                "pod": POD,
                "namespace": NAMESPACE,
            },
        )


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": VERSION,
        "environment": ENVIRONMENT,
        "payment_work_iterations": PAYMENT_WORK_ITERATIONS,
    }


@app.get("/ready")
async def ready() -> dict:
    return await health()


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/pay")
def pay(response: Response) -> dict:
    # Optional, constant payment processing work makes CPU capacity measurable.
    # Keep this setting unchanged before, during, and after a scaling drill.
    if PAYMENT_WORK_ITERATIONS:
        hashlib.pbkdf2_hmac(
            "sha256", b"nexus-demo-payment", b"nexus-demo-fixed-salt",
            PAYMENT_WORK_ITERATIONS,
        )
    if FAULT_LATENCY_MS:
        time.sleep(FAULT_LATENCY_MS / 1000.0)

    if random.random() < FAULT_5XX_RATE:
        response.status_code = 500
        logger.error("simulated payment failure")
        return {"ok": False, "error": "simulated v2 failure", "version": VERSION}

    return {"ok": True, "version": VERSION}


@app.get("/version")
async def version() -> dict:
    return {"service": SERVICE_NAME, "version": VERSION}
