from django.test import TestCase, override_settings


class MaintenanceWriteFreezeTests(TestCase):
    @override_settings(MITO_MAINTENANCE_MODE=True)
    def test_blocks_business_mutations_but_keeps_reads_and_reset_auth_flow(self):
        blocked = self.client.post("/api/projects/", {}, content_type="application/json")
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked["Retry-After"], "60")
        self.assertNotEqual(self.client.get("/healthz").status_code, 503)
        self.assertNotEqual(
            self.client.post("/api/auth/login/", {}, content_type="application/json").status_code,
            503,
        )
        self.assertNotEqual(
            self.client.post("/api/admin/reset/execute/", {}, content_type="application/json").status_code,
            503,
        )

    @override_settings(MITO_MAINTENANCE_MODE=False)
    def test_normal_mode_does_not_intercept_mutations(self):
        self.assertNotEqual(
            self.client.post("/api/projects/", {}, content_type="application/json").status_code,
            503,
        )
