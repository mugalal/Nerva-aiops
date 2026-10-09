import asyncio
from datetime import datetime, timezone
from functools import partial
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import anyio
import httpx
import prometheus_client
from prometheus_client.parser import text_string_to_metric_families


def load_payment(**overrides):
    registry = prometheus_client.CollectorRegistry()
    path = Path(__file__).resolve().parents[2] / "apps/demo-microservices/payment-service/app.py"
    spec = importlib.util.spec_from_file_location("payment_probe_concurrency", path)
    payment = importlib.util.module_from_spec(spec)
    environment = {
        "SERVICE_NAME": "payment-probe-test", "VERSION": "v1", "ENVIRONMENT": "test",
        "FAULT_LATENCY_MS": "500", "FAULT_5XX_RATE": "0", "PAYMENT_WORK_ITERATIONS": "0",
        "LOG_LEVEL": "CRITICAL", "LOG_QUEUE_MAX_RECORDS": "1024", "LOG_SHUTDOWN_TIMEOUT_SECONDS": "5",
    }
    environment.update(overrides)
    metrics = {
        name: partial(getattr(prometheus_client, name), registry=registry)
        for name in ("Counter", "Gauge", "Histogram")
    }
    metrics["generate_latest"] = partial(prometheus_client.generate_latest, registry)
    with patch.dict(os.environ, environment), patch.multiple(prometheus_client, **metrics):
        spec.loader.exec_module(payment)
    return payment


class BlockingLogStream:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lines = []

    def write(self, line):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("Test did not release the log sink")
        self.lines.append(line)

    def flush(self):
        pass


class PaymentProbeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_probes_respond_while_the_only_payment_worker_is_busy(self):
        payment = load_payment()

        limiter = anyio.to_thread.current_default_thread_limiter()
        original_tokens = limiter.total_tokens
        limiter.total_tokens = 1
        payment_started = asyncio.Event()
        loop = asyncio.get_running_loop()
        original_sleep = time.sleep

        def blocking_payment(seconds):
            loop.call_soon_threadsafe(payment_started.set)
            original_sleep(seconds)

        sleep_patch = patch.object(payment.time, "sleep", side_effect=blocking_payment)
        sleep_patch.start()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=payment.app), base_url="http://payment"
            ) as client:
                paying = asyncio.create_task(client.post("/pay"))
                try:
                    await asyncio.wait_for(payment_started.wait(), timeout=1)
                    self.assertEqual(limiter.borrowed_tokens, 1)
                    started = time.perf_counter()
                    responses = await asyncio.wait_for(
                        asyncio.gather(*(client.get(route) for route in ("/health", "/ready", "/metrics", "/version"))),
                        timeout=0.2,
                    )
                    self.assertLess(time.perf_counter() - started, 0.2)
                    self.assertFalse(paying.done(), "Probes must finish before the busy payment worker")
                    self.assertTrue(all(response.status_code == 200 for response in responses))
                    health, ready, metrics_response, version = responses
                    self.assertEqual(health.json(), {
                        "status": "ok", "service": "payment-probe-test", "version": "v1",
                        "environment": "test", "payment_work_iterations": 0,
                    })
                    self.assertEqual(ready.json(), health.json())
                    self.assertIn("nexus_service_info", metrics_response.text)
                    self.assertIn("nexus_resource_limit_cpu_cores", metrics_response.text)
                    self.assertEqual(version.json(), {"service": "payment-probe-test", "version": "v1"})
                finally:
                    paid = await paying
                self.assertEqual(paid.status_code, 200)
                self.assertEqual(paid.json(), {"ok": True, "version": "v1"})
        finally:
            sleep_patch.stop()
            limiter.total_tokens = original_tokens

    async def test_blocked_log_sink_does_not_block_probes_and_shutdown_preserves_fields(self):
        payment = load_payment(LOG_LEVEL="INFO", FAULT_LATENCY_MS="0")
        stream = BlockingLogStream()
        payment.log_sink.setStream(stream)
        release_timer = threading.Timer(0.5, stream.release.set)
        try:
            async with payment.app.router.lifespan_context(payment.app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=payment.app), base_url="http://payment"
                ) as client:
                    paid = await asyncio.wait_for(client.post("/pay"), timeout=0.2)
                    self.assertEqual(paid.status_code, 200)
                    self.assertTrue(await asyncio.to_thread(stream.entered.wait, 1))
                    release_timer.start()
                    probes = await asyncio.wait_for(
                        asyncio.gather(*(client.get(route) for route in ("/health", "/ready", "/metrics", "/version"))),
                        timeout=0.2,
                    )
                    self.assertTrue(all(response.status_code == 200 for response in probes))
                    self.assertFalse(stream.release.is_set(), "Probes must finish while output is still blocked")
                    with patch.object(payment, "FAULT_5XX_RATE", 1):
                        failed = await asyncio.wait_for(client.post("/pay"), timeout=0.2)
                    self.assertEqual(failed.status_code, 500)
                    requests_finished_at = datetime.now(timezone.utc)
        finally:
            stream.release.set()
            release_timer.cancel()

        records = [json.loads(line) for line in stream.lines]
        completions = [record for record in records if record["message"] == "request completed"]
        self.assertEqual(len(completions), 5)
        for record in completions:
            for field in ("timestamp", "level", "service", "version", "environment", "message",
                          "method", "route", "status_code", "duration_ms", "pod", "namespace"):
                self.assertIn(field, record)
            self.assertEqual(record["level"], "INFO")
            self.assertGreaterEqual(record["duration_ms"], 0)
            self.assertLessEqual(datetime.fromisoformat(record["timestamp"]), requests_finished_at,
                                 "Queued logs must retain request time instead of delayed emission time")
        self.assertTrue(any(record["route"] == "/pay" and record["status_code"] == 200 for record in completions))
        self.assertTrue(any(record["route"] == "/pay" and record["status_code"] == 500 for record in completions))
        failures = [record for record in records if record["message"] == "simulated payment failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["level"], "ERROR")
        self.assertEqual(failures[0]["service"], "payment-probe-test")
        self.assertIsNone(payment.log_listener._thread)

    async def test_full_log_queue_reports_drops_and_drains_at_shutdown(self):
        payment = load_payment(LOG_LEVEL="INFO", FAULT_LATENCY_MS="0", LOG_QUEUE_MAX_RECORDS="2")
        stream = BlockingLogStream()
        payment.log_sink.setStream(stream)
        lifecycle = payment.app.router.lifespan_context(payment.app)
        await lifecycle.__aenter__()
        shutdown = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=payment.app), base_url="http://payment"
            ) as client:
                self.assertEqual((await client.post("/pay")).status_code, 200)
                self.assertTrue(await asyncio.to_thread(stream.entered.wait, 1))
                probes = await asyncio.wait_for(
                    asyncio.gather(*(client.get("/health") for _ in range(6))), timeout=0.2
                )
                self.assertTrue(all(response.status_code == 200 for response in probes))
                self.assertEqual(payment.log_queue.qsize(), 2)
                metrics_response = await client.get("/metrics")
                dropped = [
                    sample for family in text_string_to_metric_families(metrics_response.text)
                    for sample in family.samples
                    if sample.name == "nexus_log_records_dropped_total" and sample.labels["level"] == "INFO"
                ]
                self.assertEqual(len(dropped), 1)
                self.assertEqual(dropped[0].value, 4)
            shutdown = asyncio.create_task(lifecycle.__aexit__(None, None, None))
            await asyncio.sleep(0.01)
            self.assertFalse(shutdown.done(), "Shutdown must drain the full queue instead of dropping its records")
        finally:
            stream.release.set()
            if shutdown is None:
                shutdown = asyncio.create_task(lifecycle.__aexit__(None, None, None))
            await asyncio.wait_for(shutdown, timeout=1)
        self.assertEqual(len(stream.lines), 3)
        self.assertEqual(payment.log_queue.qsize(), 0)
        self.assertIsNone(payment.log_listener._thread)

    async def test_permanently_blocked_sink_cannot_hold_shutdown_or_start_a_second_listener(self):
        payment = load_payment(LOG_LEVEL="INFO", FAULT_LATENCY_MS="0", LOG_QUEUE_MAX_RECORDS="2",
                               LOG_SHUTDOWN_TIMEOUT_SECONDS="0.05")
        stream = BlockingLogStream()
        payment.log_sink.setStream(stream)
        lifecycle = payment.app.router.lifespan_context(payment.app)
        await lifecycle.__aenter__()
        listener_thread = payment.log_listener._thread
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=payment.app), base_url="http://payment"
            ) as client:
                self.assertEqual((await client.post("/pay")).status_code, 200)
                self.assertTrue(await asyncio.to_thread(stream.entered.wait, 1))
                self.assertEqual((await client.get("/health")).status_code, 200)
                self.assertEqual((await client.get("/version")).status_code, 200)
                self.assertEqual(payment.log_queue.qsize(), 2)
                started = time.perf_counter()
                await asyncio.wait_for(lifecycle.__aexit__(None, None, None), timeout=0.2)
                self.assertLess(time.perf_counter() - started, 0.2)
                self.assertTrue(listener_thread.is_alive())
                self.assertTrue(listener_thread.daemon)
                self.assertEqual(payment.LOG_RECORDS_DROPPED.labels(*payment.COMMON_LABEL_VALUES, "INFO")._value.get(), 2)
                with self.assertRaisesRegex(RuntimeError, "Previous log listener"):
                    await payment.app.router.lifespan_context(payment.app).__aenter__()
                self.assertIs(payment.log_listener._thread, listener_thread)
        finally:
            stream.release.set()
            await asyncio.to_thread(listener_thread.join, 1)
            if not payment.log_listener._stop_requested:
                await lifecycle.__aexit__(None, None, None)
        self.assertFalse(listener_thread.is_alive())
        self.assertEqual(len(stream.lines), 1)
        self.assertEqual(payment.log_queue.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
