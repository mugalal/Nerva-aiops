import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from app.errors import ProviderInvalidResponse, ProviderUnavailable
from app.main import create_app
from app.providers.prometheus import PrometheusClient
from tests.m1.support import make_service, make_snapshot


class PrometheusClientPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_ready_vector_and_range_queries_reuse_one_owned_client(self):
        arrived = asyncio.Event()
        release = asyncio.Event()
        requests = []

        async def respond(request):
            requests.append(request)
            if len(requests) == 4:
                arrived.set()
            await release.wait()
            if request.url.path == "/-/ready":
                return httpx.Response(200, text="ready")
            if request.url.path == "/api/v1/query_range":
                result_type = "matrix"
                result = [{"metric": {}, "values": [[100, "1"], [115, "2"]]}]
            else:
                result_type = "vector"
                result = [{"metric": {"version": "v1"}, "value": [100, "1"]}]
            return httpx.Response(200, json={"status": "success", "data": {
                "resultType": result_type, "result": result,
            }})

        provider = PrometheusClient("http://prometheus", timeout=2, transport=httpx.MockTransport(respond))
        try:
            with patch("app.providers.prometheus.httpx.AsyncClient", wraps=httpx.AsyncClient) as factory:
                queries = asyncio.gather(
                    provider.ready(), provider.query_scalar("cpu", evaluation_time=100),
                    provider.query_vector("version", evaluation_time=100),
                    provider.query_range_scalar("cpu", start=100, end=115, step_seconds=15),
                )
                try:
                    await asyncio.wait_for(arrived.wait(), timeout=1)
                    self.assertEqual(factory.call_count, 1)
                finally:
                    release.set()
                ready, scalar, vector, history = await queries
                self.assertEqual(ready, (True, "ready"))
                self.assertEqual(scalar.value, 1)
                self.assertEqual(vector[0].labels["version"], "v1")
                self.assertEqual(history, [(100, 1), (115, 2)])
                self.assertEqual(requests[1].url.params["time"], "100")
                self.assertEqual(requests[3].url.params["step"], "15")
                first_client = provider._http_client
                self.assertEqual(await provider.ready(), (True, "ready"))
                self.assertIs(provider._http_client, first_client)
                self.assertEqual(factory.call_count, 1)
                await provider.aclose()
                self.assertTrue(first_client.is_closed)
                await provider.aclose()
                self.assertEqual(await provider.ready(), (True, "ready"))
                self.assertEqual(factory.call_count, 2)
                self.assertIsNot(provider._http_client, first_client)
        finally:
            await provider.aclose()

    async def test_pool_preserves_timeout_and_http_error_categories_and_stays_usable(self):
        def respond(request):
            query = request.url.params.get("query")
            if query == "timeout":
                raise httpx.ReadTimeout("test timeout", request=request)
            if query == "unavailable":
                return httpx.Response(503)
            if query == "invalid":
                return httpx.Response(400)
            return httpx.Response(200, text="ready")

        provider = PrometheusClient("http://prometheus", transport=httpx.MockTransport(respond))
        try:
            with patch("app.providers.prometheus.httpx.AsyncClient", wraps=httpx.AsyncClient) as factory:
                for query, error in (("timeout", ProviderUnavailable), ("unavailable", ProviderUnavailable),
                                     ("invalid", ProviderInvalidResponse)):
                    with self.subTest(query=query), self.assertRaises(error):
                        await provider.query_scalar(query)
                self.assertEqual(await provider.ready(), (True, "ready"))
                self.assertEqual(factory.call_count, 1)
        finally:
            await provider.aclose()

    async def test_app_shutdown_closes_pool_and_subsequent_lifespan_recreates_it(self):
        provider = PrometheusClient("http://prometheus", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="ready")
        ))
        with TemporaryDirectory() as directory:
            service, _, _ = make_service(Path(directory), make_snapshot())
            # A fake telemetry provider can expose an owned real HTTP client.
            service.telemetry.client = provider
            application = create_app(service.settings, service)
            async with application.router.lifespan_context(application):
                self.assertEqual(await provider.ready(), (True, "ready"))
                first_client = provider._http_client
            self.assertTrue(first_client.is_closed)
            self.assertIsNone(provider._http_client)
            async with application.router.lifespan_context(application):
                self.assertEqual(await provider.ready(), (True, "ready"))
                second_client = provider._http_client
                self.assertIsNot(first_client, second_client)
            self.assertTrue(second_client.is_closed)

    async def test_fake_provider_without_lifecycle_method_still_starts_and_stops(self):
        with TemporaryDirectory() as directory:
            service, _, _ = make_service(Path(directory), make_snapshot())
            application = create_app(service.settings, service)
            async with application.router.lifespan_context(application):
                self.assertEqual((await service.snapshot("payment-service")).version, "v1")


if __name__ == "__main__":
    unittest.main()
