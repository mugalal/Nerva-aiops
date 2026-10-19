"""M1 measures any registered service's business route, configured, never assumed to be /pay."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.config import parse_pairs
from app.errors import TelemetryMissing
from app.telemetry import PrometheusTelemetryProvider
from tests.m1.support import make_settings


class ServiceRouteTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = make_settings(Path(directory.name))

    def test_payment_queries_are_unchanged_and_other_services_use_their_route(self):
        payment = PrometheusTelemetryProvider(None, self.settings).queries("payment-service")
        self.assertIn('service_name="payment-service",route="/pay"', payment["request_rate"])
        cart = PrometheusTelemetryProvider(None, replace(self.settings, service_routes={"cart-service": "/cart"}))
        self.assertIn('service_name="cart-service",route="/cart"', cart.queries("cart-service")["latency_p95_ms"])
        self.assertIn('route="/cart"', cart.source_queries("cart-service")["source_latency"])

    def test_a_service_without_a_configured_route_is_not_measured(self):
        with self.assertRaises(TelemetryMissing):
            PrometheusTelemetryProvider(None, self.settings).queries("cart-service")

    def test_pairs_parse_and_reject_malformed_entries(self):
        self.assertEqual(parse_pairs("cart-service=/cart, auth-service=/login", "X"),
                         {"cart-service": "/cart", "auth-service": "/login"})
        self.assertEqual(parse_pairs("", "X"), {})
        for bad in ("cart-service", "=/cart", "cart-service="):
            with self.assertRaises(ValueError):
                parse_pairs(bad, "X")
