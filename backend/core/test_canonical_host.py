"""Tests for retiring an old hostname after the deployment is renamed."""

from django.test import SimpleTestCase, override_settings

CANONICAL = "mito-data-studio.seg.bio"
LEGACY = "mito-data-agent.seg.bio"

_HOSTS = [CANONICAL, LEGACY, "127.0.0.1", "localhost", "testserver"]


@override_settings(
    ALLOWED_HOSTS=_HOSTS,
    MITO_CANONICAL_HOST=CANONICAL,
    MITO_LEGACY_HOSTS=[LEGACY],
    SECURE_SSL_REDIRECT=False,
)
class CanonicalHostRedirectTests(SimpleTestCase):
    def test_the_old_hostname_redirects_permanently_to_the_new_one(self):
        response = self.client.get("/", HTTP_HOST=LEGACY, secure=True)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], f"https://{CANONICAL}/")

    def test_the_path_and_query_string_survive_the_redirect(self):
        response = self.client.get(
            "/projects/7/tasks/", {"status": "in_review", "page": "2"},
            HTTP_HOST=LEGACY, secure=True,
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"],
            f"https://{CANONICAL}/projects/7/tasks/?status=in_review&page=2",
        )

    def test_a_post_keeps_its_method_via_308_instead_of_becoming_a_get(self):
        # A 301 would let the client re-issue this as a GET and silently drop
        # the body — a save turning into a read is worse than a hard failure.
        response = self.client.post(
            "/api/tasks/1/timing/heartbeat/", HTTP_HOST=LEGACY, secure=True
        )
        self.assertEqual(response.status_code, 308)
        self.assertEqual(
            response["Location"], f"https://{CANONICAL}/api/tasks/1/timing/heartbeat/"
        )

    def test_a_port_on_the_legacy_host_still_matches(self):
        response = self.client.get("/", HTTP_HOST=f"{LEGACY}:443", secure=True)
        self.assertEqual(response.status_code, 301)

    def test_the_canonical_host_is_served_and_never_redirected(self):
        response = self.client.get("/healthz", HTTP_HOST=CANONICAL, secure=True)
        self.assertNotIn(response.status_code, (301, 302, 307, 308))

    def test_loopback_health_checks_are_not_redirected(self):
        # The deployment runbook curls these over plain loopback HTTP. If they
        # started answering 301 the whole post-deploy checklist would break.
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                response = self.client.get("/healthz", HTTP_HOST=host)
                self.assertNotIn(response.status_code, (301, 302, 307, 308))

    def test_a_legacy_host_that_equals_the_canonical_host_cannot_loop(self):
        with override_settings(MITO_LEGACY_HOSTS=[CANONICAL, LEGACY]):
            response = self.client.get("/healthz", HTTP_HOST=CANONICAL, secure=True)
            self.assertNotIn(response.status_code, (301, 302, 307, 308))


@override_settings(
    ALLOWED_HOSTS=_HOSTS, MITO_CANONICAL_HOST="", MITO_LEGACY_HOSTS=[],
    SECURE_SSL_REDIRECT=False,
)
class CanonicalHostDisabledTests(SimpleTestCase):
    def test_unconfigured_deployments_redirect_nothing(self):
        for host in (LEGACY, CANONICAL, "127.0.0.1"):
            with self.subTest(host=host):
                response = self.client.get("/healthz", HTTP_HOST=host)
                self.assertNotIn(response.status_code, (301, 302, 307, 308))

    def test_a_canonical_host_without_legacy_hosts_stays_inert(self):
        with override_settings(MITO_CANONICAL_HOST=CANONICAL):
            response = self.client.get("/healthz", HTTP_HOST=LEGACY, secure=True)
            self.assertNotIn(response.status_code, (301, 302, 307, 308))
