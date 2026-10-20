"""checkout-service: the second workload. It runs the demo app and calls payment-service."""
import unittest

from fastapi.testclient import TestClient

from tests.m1.test_payment_probe_concurrency import load_payment


class CheckoutTests(unittest.TestCase):
    def app(self, **env):
        module = load_payment(SERVICE_NAME="checkout-service", PAYMENT_WORK_ITERATIONS="0",
                              FAULT_LATENCY_MS="0", **env)
        return module, TestClient(module.app)

    def test_checkout_succeeds_only_when_payment_succeeds(self):
        module, client = self.app(DOWNSTREAM_PAY_URL="http://payment-service/pay")
        for downstream, expected in ((200, 200), (500, 502), (0, 502)):
            with self.subTest(downstream=downstream):
                module._call_payment = lambda status=downstream: status
                response = client.post("/checkout")
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.json()["ok"], expected == 200)

    def test_checkout_is_off_on_payment_service(self):
        _, client = self.app(DOWNSTREAM_PAY_URL="")
        self.assertEqual(client.post("/checkout").status_code, 404)

    def test_checkout_requests_are_measured_on_their_own_route(self):
        module, client = self.app(DOWNSTREAM_PAY_URL="http://payment-service/pay")
        module._call_payment = lambda: 200
        client.post("/checkout")
        metrics = client.get("/metrics").text
        self.assertIn('service_name="checkout-service"', metrics)
        self.assertIn('route="/checkout"', metrics)
