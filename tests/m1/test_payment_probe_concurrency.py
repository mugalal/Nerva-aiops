import asyncio
from functools import partial
import importlib.util
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import anyio
import httpx
import prometheus_client


class PaymentProbeConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_probes_respond_while_the_only_payment_worker_is_busy(self):
        registry = prometheus_client.CollectorRegistry()
        path = Path(__file__).resolve().parents[2] / "apps/demo-microservices/payment-service/app.py"
        spec = importlib.util.spec_from_file_location("payment_probe_concurrency", path)
        payment = importlib.util.module_from_spec(spec)
        environment = {
            "SERVICE_NAME": "payment-probe-test", "VERSION": "v1", "ENVIRONMENT": "test",
            "FAULT_LATENCY_MS": "500", "FAULT_5XX_RATE": "0", "PAYMENT_WORK_ITERATIONS": "0",
            "LOG_LEVEL": "CRITICAL",
        }
        metrics = {
            name: partial(getattr(prometheus_client, name), registry=registry)
            for name in ("Counter", "Gauge", "Histogram")
        }
        metrics["generate_latest"] = partial(prometheus_client.generate_latest, registry)
        with patch.dict(os.environ, environment), patch.multiple(prometheus_client, **metrics):
            spec.loader.exec_module(payment)

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


if __name__ == "__main__":
    unittest.main()
