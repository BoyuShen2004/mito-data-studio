"""Release checks for the TLS-terminated Cloudflare production profile."""

from django.core import checks
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.middleware.security import SecurityMiddleware


PRODUCTION_TLS = {
    "DEBUG": False,
    "ALLOWED_HOSTS": ["mito-data-studio.seg.bio"],
    "CSRF_TRUSTED_ORIGINS": ["https://mito-data-studio.seg.bio"],
    "SECRET_KEY": "release-profile-test-only-not-a-real-secret-0123456789abcdef",
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_SSL_REDIRECT": True,
    "SESSION_COOKIE_SECURE": True,
    "CSRF_COOKIE_SECURE": True,
    "SECURE_HSTS_SECONDS": 300,
    "SECURE_HSTS_INCLUDE_SUBDOMAINS": False,
    "SECURE_HSTS_PRELOAD": False,
    "USE_X_FORWARDED_HOST": False,
}


@override_settings(**PRODUCTION_TLS)
class ProductionTlsProfileTests(SimpleTestCase):
    def test_deploy_check_has_only_deliberately_deferred_hsts_findings(self):
        issues = checks.run_checks(include_deployment_checks=True)
        security_ids = {issue.id for issue in issues if issue.id.startswith("security.")}
        self.assertEqual(security_ids, {"security.W005", "security.W021"})
        self.assertFalse([issue for issue in issues if isinstance(issue, checks.Error)])

    def test_cloudflare_https_header_is_trusted_without_forwarded_host(self):
        request = RequestFactory().get(
            "/login",
            HTTP_HOST="mito-data-studio.seg.bio",
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_FORWARDED_HOST="attacker.invalid",
        )
        self.assertTrue(request.is_secure())
        self.assertEqual(request.get_host(), "mito-data-studio.seg.bio")
        response = SecurityMiddleware(lambda _request: HttpResponse("ok"))(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Strict-Transport-Security"], "max-age=300")

    def test_plain_http_redirects_once_to_the_canonical_https_host(self):
        request = RequestFactory().get(
            "/login",
            HTTP_HOST="mito-data-studio.seg.bio",
            HTTP_X_FORWARDED_PROTO="http",
        )
        response = SecurityMiddleware(lambda _request: HttpResponse("ok"))(request)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://mito-data-studio.seg.bio/login")

    def test_cookie_and_hsts_scope_is_intentionally_conservative(self):
        from django.conf import settings

        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)
        self.assertFalse(settings.SECURE_HSTS_PRELOAD)
