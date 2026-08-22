from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from core.observability import REQUEST_METRICS


class ObservabilityEndpointTests(TestCase):
    def setUp(self):
        REQUEST_METRICS.reset()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_liveness_is_public_and_carries_request_id(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    @override_settings(MITO_READY_MIN_FREE_BYTES=0)
    def test_readiness_checks_database_data_root_and_disk(self):
        with override_settings(MITO_DATA_ROOT=Path(self.tmp.name)):
            response = self.client.get("/readyz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ready")
            self.assertTrue(response.json()["checks"]["annotation_locks"])
        self.assertTrue(all(response.json()["checks"].values()))

    def test_metrics_are_hidden_without_configuration(self):
        with override_settings(MITO_METRICS_BEARER_TOKEN=""):
            self.assertEqual(self.client.get("/metrics").status_code, 404)

    @override_settings(MITO_METRICS_BEARER_TOKEN="scrape-test-token")
    def test_metrics_require_bearer_and_expose_bounded_signals(self):
        self.client.get("/healthz")
        self.assertEqual(self.client.get("/metrics").status_code, 404)
        response = self.client.get(
            "/metrics", HTTP_AUTHORIZATION="Bearer scrape-test-token"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("mito_http_requests_total", body)
        self.assertIn("mito_worker_queue_depth", body)
        self.assertIn("mito_chunk_bytes_total", body)
