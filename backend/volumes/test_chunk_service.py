"""Phase 12 — chunk reads, authorization, signed tokens, limits and metrics.

The gate is **authz + metrics**, so those get the most attention: every way a
token can be wrong is a separate test, and the metrics are asserted to actually
move rather than merely exist.

Isolated temporary MITO_DATA_ROOT throughout; nothing here touches real data.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from core.choices import UserRole
from projects.models import Dataset, Project
from volumes.chunks import core as chunk_core
from volumes.chunks import service, tokens
from volumes.chunks.metrics import METRICS
from volumes.models import Volume
from volumes.pyramid import service as pyramid_service
from volumes.pyramid import store

User = get_user_model()

PYRAMIDS = dict(FEATURE_VOLUME_PYRAMIDS=True)
ON = dict(FEATURE_VOLUME_PYRAMIDS=True, FEATURE_CHUNK_SERVICE=True)
# "Pyramids on, chunk service off" stated explicitly. The FlagGating tests used
# to express this as `**PYRAMIDS` alone and let FEATURE_CHUNK_SERVICE fall
# through to the settings default — which is off in the `legacy` profile these
# tests were written under, but **on** in `production_integrated_v1`, where the
# chunk service is the primary read path. Under the deployed profile they
# therefore asserted the disabled behaviour of an enabled service and failed.
OFF = dict(FEATURE_VOLUME_PYRAMIDS=True, FEATURE_CHUNK_SERVICE=False)
SHAPE = (4, 256, 256)


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover
        return False


class ChunkTestCase(TestCase):
    def setUp(self):
        if not _zarr_available():  # pragma: no cover
            self.skipTest("zarr is an optional dependency and is not installed")
        METRICS.reset()
        chunk_core.HANDLES.clear()

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        external = Path(self.tmp.name) / "src"
        external.mkdir()

        rng = np.random.default_rng(23)
        self.source = rng.integers(0, 5000, size=SHAPE, dtype=np.uint16)
        self.image = external / "cortex.tif"
        tifffile.imwrite(str(self.image), self.source)
        self.image_bytes = self.image.read_bytes()

        self.owner = User.objects.create_user(username="owner", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        self.manager = User.objects.create_user(username="mgr", password="x")
        UserProfile.objects.update_or_create(
            user=self.manager, defaults={"role": UserRole.MANAGER}
        )
        # A UserProfile is auto-created with the default annotator role, and the
        # instance caches it — so update_or_create changes the row while this
        # object still reports "annotator". Refetch, or the fixture silently
        # tests the wrong role.
        self.manager = User.objects.get(pk=self.manager.pk)

        self.project = Project.objects.create(title="Proj", created_by=self.owner)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )

    def build(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **PYRAMIDS):
            pyramid_service.build_pyramid(self.volume)
        self.volume.refresh_from_db()

    def url(self, name, *args):
        return reverse(name, args=args)


class FlagGating(ChunkTestCase):
    def test_disabled_service_refuses_even_with_a_pyramid_present(self):
        """Endpoints must not appear merely because files exist on disk."""
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            self.assertFalse(service.chunk_service_enabled())
            with self.assertRaises(service.Disabled):
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner,
                )

    def test_endpoint_returns_503_not_404_when_disabled(self):
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            resp = self.client.get(
                self.url("api-volume-chunk-capabilities", self.volume.pk)
            )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["reason"], "disabled")

    def test_chunk_service_alone_without_pyramids_is_disabled(self):
        with override_settings(
            MITO_DATA_ROOT=self.root.resolve(),
            FEATURE_CHUNK_SERVICE=True, FEATURE_VOLUME_PYRAMIDS=False,
        ):
            self.assertFalse(service.chunk_service_enabled())


class ChunkReads(ChunkTestCase):
    def test_a_valid_chunk_matches_the_derivative_exactly(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            expected = store.read_plane(self.volume, "1", 0)[:256, :256]
        block = np.frombuffer(served.result.data, dtype=served.result.dtype).reshape(
            served.result.shape
        )
        np.testing.assert_array_equal(block[0], expected[: block.shape[1], : block.shape[2]])

    def test_dtype_and_shape_are_exact(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
        self.assertEqual(served.result.dtype, "uint16")
        self.assertEqual(served.result.shape[0], 1)  # slice-oriented chunks

    def test_reads_are_deterministic(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            a = service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            b = service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
        self.assertEqual(a.result.etag, b.result.etag)
        self.assertEqual(a.result.data, b.result.data)

    def test_an_edge_chunk_is_clipped_not_padded(self):
        """A client must be able to tell real data from filler."""
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = service.capabilities(volume_id=self.volume.pk, user=self.owner)
            mag2 = next(m for m in caps["mags"] if m["mag"] == "2")
            last = [g - 1 for g in mag2["grid"]]
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="2",
                cz=last[0], cy=last[1], cx=last[2], user=self.owner,
            )
        for axis in range(3):
            self.assertLessEqual(served.result.shape[axis], mag2["chunks"][axis])
        self.assertGreater(served.result.nbytes, 0)

    def test_an_out_of_range_chunk_is_404(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=9999, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "chunk_out_of_range")
        self.assertEqual(ctx.exception.status, 404)

    def test_an_unknown_mag_is_404(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="9999", cz=0, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "unknown_mag")

    def test_a_negative_index_is_refused(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=-1, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "index_out_of_range")

    def test_a_volume_with_no_pyramid_is_404(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "no_pyramid")

    def test_an_unknown_volume_is_404(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=999999, mag="1", cz=0, cy=0, cx=0, user=self.owner
                )
        self.assertEqual(ctx.exception.reason, "unknown_volume")

    def test_reading_never_modifies_the_source_tiff(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            for cz in range(2):
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=cz, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(self.image.read_bytes(), self.image_bytes)


class Capabilities(ChunkTestCase):
    def test_capabilities_describe_the_grid_and_leak_no_paths(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = service.capabilities(volume_id=self.volume.pk, user=self.owner)
        self.assertIn("mags", caps)
        self.assertTrue(all("grid" in m for m in caps["mags"]))
        blob = str(caps)
        self.assertNotIn(str(self.root), blob)
        self.assertNotIn(".zarr", blob)
        self.assertNotIn(str(self.image), blob)
        self.assertEqual(caps["build_identity"], self.volume.pyramid_metadata["built_at"])


class Permissions(ChunkTestCase):
    def test_unauthenticated_endpoint_is_rejected(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-read", self.volume.pk, "1", 0, 0, 0)
            )
        self.assertIn(resp.status_code, (401, 403))

    def test_a_stranger_is_denied(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.PermissionDenied):
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.stranger,
                )

    def test_a_manager_may_read(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.manager
            )
        self.assertGreater(served.result.nbytes, 0)

    def test_a_stranger_may_not_issue_a_token(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.PermissionDenied):
                service.issue_token(volume_id=self.volume.pk, user=self.stranger)


class SignedTokens(ChunkTestCase):
    def _deployment(self):
        from core.deployment import fingerprint

        return fingerprint()

    def test_a_valid_token_reads(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(volume_id=self.volume.pk, user=self.owner)
            served = service.read_chunk_with_token(
                token=issued["token"], mag="1", cz=0, cy=0, cx=0
            )
        self.assertGreater(served.result.nbytes, 0)

    def test_a_token_is_read_only_by_construction(self):
        """There is no write action in the schema to mint."""
        self.assertEqual(tokens.ACTION_READ, "r")
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"], deployment=self._deployment()
            )
            claims = tokens.verify(raw, deployment=self._deployment())
        self.assertEqual(claims.action, "r")

    def test_an_expired_token_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            past = int(time.time()) - 10_000
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"],
                deployment=self._deployment(), ttl_seconds=1, now=past,
            )
            with self.assertRaises(tokens.TokenError) as ctx:
                tokens.verify(raw, deployment=self._deployment())
        self.assertEqual(ctx.exception.reason, "expired")

    def test_a_token_from_the_future_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            future = int(time.time()) + 10_000
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"],
                deployment=self._deployment(), now=future,
            )
            with self.assertRaises(tokens.TokenError) as ctx:
                tokens.verify(raw, deployment=self._deployment())
        self.assertEqual(ctx.exception.reason, "not_yet_valid")

    def test_clock_skew_leeway_is_honoured_at_the_boundary(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            now = int(time.time())
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"],
                deployment=self._deployment(), ttl_seconds=1, now=now,
            )
            # One second past expiry: rejected without leeway, accepted with.
            with self.assertRaises(tokens.TokenError):
                tokens.verify(raw, deployment=self._deployment(), now=now + 2)
            claims = tokens.verify(
                raw, deployment=self._deployment(), now=now + 2, leeway_seconds=5
            )
        self.assertEqual(claims.volume_id, 1)

    def test_a_malformed_token_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            for bad in ("", "not-a-token", "a.b.c", "x" * 5000):
                with self.assertRaises(tokens.TokenError):
                    tokens.verify(bad, deployment=self._deployment())

    def test_an_altered_payload_fails_the_signature(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"], deployment=self._deployment()
            )
            altered = raw[:-4] + ("aaaa" if not raw.endswith("aaaa") else "bbbb")
            with self.assertRaises(tokens.TokenError) as ctx:
                tokens.verify(altered, deployment=self._deployment())
        self.assertEqual(ctx.exception.reason, "bad_signature")

    def test_a_token_for_another_deployment_is_rejected(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"], deployment="some-other-instance"
            )
            with self.assertRaises(tokens.TokenError) as ctx:
                tokens.verify(raw, deployment=self._deployment())
        self.assertEqual(ctx.exception.reason, "wrong_deployment")

    def test_key_rotation_invalidates_outstanding_tokens(self):
        """The only immediate global revocation this design offers."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"], deployment=self._deployment()
            )
            tokens.verify(raw, deployment=self._deployment())  # fine now
            with override_settings(MITO_CHUNK_TOKEN_KEY_VERSION="2"):
                with self.assertRaises(tokens.TokenError):
                    tokens.verify(raw, deployment=self._deployment())

    def test_a_token_cannot_read_another_volume(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=self.owner.pk, volume_id=self.volume.pk + 500,
                mags=["1"], deployment=self._deployment(),
            )
            claims = tokens.verify(raw, deployment=self._deployment())
            with self.assertRaises(tokens.TokenError) as ctx:
                tokens.authorize(
                    claims, volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0
                )
        self.assertEqual(ctx.exception.reason, "wrong_volume")

    def test_a_token_cannot_read_a_mag_it_was_not_granted(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(
                volume_id=self.volume.pk, user=self.owner, mags=["2"]
            )
            with self.assertRaises(service.TokenRejected):
                service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=0, cy=0, cx=0
                )

    def test_a_scoped_token_cannot_read_outside_its_scope(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            scope = tokens.ChunkScope(0, 0, 0, 1, 1, 1)
            issued = service.issue_token(
                volume_id=self.volume.pk, user=self.owner, mags=["1"], scope=scope
            )
            service.read_chunk_with_token(
                token=issued["token"], mag="1", cz=0, cy=0, cx=0
            )  # inside
            with self.assertRaises(service.TokenRejected):
                service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=1, cy=0, cx=0
                )  # outside

    def test_a_token_is_reusable_within_its_lifetime(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(volume_id=self.volume.pk, user=self.owner)
            for _ in range(3):
                service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=0, cy=0, cx=0
                )

    def test_a_token_carries_no_secret(self):
        from django.conf import settings as dj

        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            raw = tokens.issue(
                user_id=1, volume_id=1, mags=["1"], deployment=self._deployment()
            )
            claims = tokens.verify(raw, deployment=self._deployment())
        self.assertNotIn(dj.SECRET_KEY, raw)
        blob = str(claims.audit()).lower()
        for banned in ("password", "secret", "cookie", "session"):
            self.assertNotIn(banned, blob)

    def test_the_http_token_path_rejects_coarsely(self):
        """A verifier that says exactly why a forgery failed is an oracle."""
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-chunk-signed-read", "1", 0, 0, 0) + "?t=forged"
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["reason"], "token_rejected")


class HandleCacheBehaviour(ChunkTestCase):
    def test_a_second_read_hits_the_cached_handle(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            first = METRICS.snapshot()
            # Grid for a 256² volume with (1,512,512) chunks is (4,1,1) — the
            # second chunk is along z, not x.
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=1, cy=0, cx=0, user=self.owner)
            second = METRICS.snapshot()
        self.assertGreater(
            second["chunk_cache_hits_total"], first["chunk_cache_hits_total"]
        )

    def test_a_rebuild_invalidates_the_cache_without_an_explicit_flush(self):
        """Invalidation is derived from build identity, not signalled."""
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            before = METRICS.snapshot()["chunk_cache_misses_total"]

        time.sleep(0.01)
        self.build()  # rebuild changes built_at
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            after = METRICS.snapshot()["chunk_cache_misses_total"]
        self.assertGreater(after, before, "a rebuild did not invalidate the handle")

    def test_the_cache_is_bounded(self):
        cache = chunk_core.HandleCache(max_entries=2)
        for i in range(5):
            cache.get((i, "b"), lambda: object())
        self.assertLessEqual(cache.size(), 2)


class Limits(ChunkTestCase):
    def test_an_oversized_chunk_is_refused(self):
        self.build()
        with override_settings(
            MITO_DATA_ROOT=self.root.resolve(), MITO_CHUNK_MAX_VOXELS=4, **ON
        ):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "chunk_too_large")
        self.assertEqual(ctx.exception.status, 413)

    def test_an_oversized_response_is_refused(self):
        self.build()
        with override_settings(
            MITO_DATA_ROOT=self.root.resolve(), MITO_CHUNK_MAX_BYTES=8, **ON
        ):
            with self.assertRaises(service.ChunkServiceError) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner,
                )
        self.assertEqual(ctx.exception.reason, "response_too_large")

    def test_a_non_integer_index_is_refused(self):
        address = chunk_core.ChunkAddress(volume_id=1, mag="1", cz=True, cy=0, cx=0)
        with self.assertRaises(chunk_core.ChunkError) as ctx:
            address.validate()
        self.assertEqual(ctx.exception.reason, "bad_index")

    def test_a_non_numeric_mag_is_refused(self):
        """There is no string path in a request, so traversal has no surface."""
        for bad in ("../../etc", "1;rm -rf /", "", "a"):
            address = chunk_core.ChunkAddress(volume_id=1, mag=bad, cz=0, cy=0, cx=0)
            with self.assertRaises(chunk_core.ChunkError):
                address.validate()


class HttpContract(ChunkTestCase):
    def test_the_authenticated_endpoint_returns_bytes_and_headers(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-read", self.volume.pk, "1", 0, 0, 0)
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/octet-stream")
        self.assertEqual(resp["X-Mito-Dtype"], "uint16")
        self.assertEqual(resp["X-Mito-Byte-Order"], "little")
        self.assertEqual(
            resp["X-Mito-Build-Identity"],
            self.volume.pyramid_metadata["built_at"],
        )
        self.assertTrue(resp["ETag"])
        self.assertIn("immutable", resp["Cache-Control"])
        self.assertGreater(len(resp.content), 0)

    def test_no_response_header_leaks_a_filesystem_path(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-read", self.volume.pk, "1", 0, 0, 0)
            )
        joined = " ".join(f"{k}:{v}" for k, v in resp.items())
        self.assertNotIn(str(self.root), joined)
        self.assertNotIn(".zarr", joined)

    def test_the_signed_endpoint_serves_with_a_token(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(volume_id=self.volume.pk, user=self.owner)
            resp = self.client.get(
                self.url("api-chunk-signed-read", "1", 0, 0, 0)
                + f"?t={issued['token']}"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.content), 0)

    def test_the_token_endpoint_requires_authentication(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.post(
                self.url("api-volume-chunk-token", self.volume.pk), {},
                content_type="application/json",
            )
        self.assertIn(resp.status_code, (401, 403))


class Metrics(ChunkTestCase):
    """The other half of the gate: signals must actually move."""

    def test_a_read_records_fetch_duration_and_bytes(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            snap = METRICS.snapshot()
        self.assertGreaterEqual(snap["chunk_fetch_seconds"]["count"], 1)
        self.assertIsNotNone(snap["chunk_fetch_seconds"]["p95"])
        self.assertGreater(snap["chunk_bytes_total"], 0)

    def test_a_rejection_is_counted_by_reason(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            try:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=9999, cy=0, cx=0,
                    user=self.owner,
                )
            except service.ChunkServiceError:
                pass
            snap = METRICS.snapshot()
        self.assertIn("chunk_out_of_range", snap["chunk_rejected_total"])

    def test_token_verification_is_timed(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(volume_id=self.volume.pk, user=self.owner)
            service.read_chunk_with_token(token=issued["token"], mag="1", cz=0, cy=0, cx=0)
            snap = METRICS.snapshot()
        self.assertGreaterEqual(snap["chunk_token_verify_seconds"]["count"], 1)

    def test_the_metrics_endpoint_exposes_no_identifiers_or_secrets(self):
        from django.conf import settings as dj

        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner)
            resp = self.client.get(self.url("api-chunk-metrics"))
        self.assertEqual(resp.status_code, 200)
        blob = resp.content.decode().lower()
        self.assertNotIn(dj.SECRET_KEY.lower(), blob)
        for banned in ("password", "token\":\"", "/home/", ".zarr"):
            self.assertNotIn(banned, blob)
        self.assertIn("chunk_fetch_seconds", blob)

    def test_the_metrics_endpoint_requires_authentication(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(self.url("api-chunk-metrics"))
        self.assertIn(resp.status_code, (401, 403))


class _RecordingArray:
    """Passes through to a real zarr array, recording what was asked of it."""

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __getitem__(self, key):
        self._log.append(key)
        return self._inner[key]


class _RecordingGroup:
    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def __getitem__(self, mag):
        return _RecordingArray(self._inner[mag], self._log)


class StoreAccessShape(ChunkTestCase):
    """The "no full-volume load" guarantee, tested rather than assumed.

    It is structurally true — the slice is built from the array's own chunk
    shape and clipped — but "structurally true" is what regressions are made of.
    This asserts on the slice the store actually receives, so a future change
    that reads the array and subsets in memory fails here.
    """

    def test_a_read_asks_the_store_for_one_chunk_not_the_whole_array(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            group = store.open_pyramid(self.volume)
            shape = tuple(int(s) for s in group["1"].shape)
            chunks = tuple(int(c) for c in group["1"].chunks)

            log: list = []
            result = chunk_core.read_chunk(
                address=chunk_core.ChunkAddress(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0
                ),
                group_factory=lambda: _RecordingGroup(group, log),
                build_identity="probe",
                cache=chunk_core.HandleCache(max_entries=2),
            )

        self.assertEqual(len(log), 1, "one chunk read should touch the store once")
        requested = log[0]
        self.assertEqual(len(requested), 3, "the store is indexed on (z, y, x)")

        extent = [s.stop - s.start for s in requested]
        for axis in range(3):
            self.assertLessEqual(
                extent[axis], chunks[axis],
                f"axis {axis} requested {extent[axis]} voxels from a "
                f"{chunks[axis]}-voxel chunk — more than one chunk was loaded",
            )
        requested_voxels = extent[0] * extent[1] * extent[2]
        volume_voxels = shape[0] * shape[1] * shape[2]
        self.assertLess(
            requested_voxels, volume_voxels,
            "the read covered the entire array — that is a full-volume load",
        )
        # And what came back is exactly what was asked for, not a subset of more.
        self.assertEqual(tuple(extent), tuple(result.shape))
        self.assertEqual(
            len(result.data),
            requested_voxels * np.dtype(result.dtype).itemsize,
        )


class PublicShareChunkAccess(ChunkTestCase):
    """A revocable public share can stream the volume it already exposes.

    Every one of these voxels is already readable through the share's own
    ``slice`` endpoint. What is under test is that moving them onto the chunk
    transport does not widen the share by one volume, survive its revocation,
    or require the recipient to have an account.
    """

    def setUp(self):
        super().setUp()
        from projects.models import PublicShare

        self.build()
        self.other = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="not-shared",
            image_path=str(self.image),
        )
        self.share = PublicShare.objects.create(
            scope="volume", project=self.project, dataset=self.dataset,
            volume=self.volume, created_by=self.owner,
        )
        self.anon = self.client_class()

    def caps_url(self, volume=None, token=None):
        return reverse(
            "api-public-share-chunk-capabilities",
            args=[token or self.share.token, (volume or self.volume).pk],
        )

    def token_url(self, volume=None, token=None):
        return reverse(
            "api-public-share-chunk-token",
            args=[token or self.share.token, (volume or self.volume).pk],
        )

    def test_an_anonymous_recipient_streams_the_shared_volume(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = self.anon.get(self.caps_url())
            self.assertEqual(caps.status_code, 200, caps.content)
            self.assertEqual(caps.json()["volume_id"], self.volume.pk)

            minted = self.anon.post(self.token_url(), {}, format="json")
            self.assertEqual(minted.status_code, 200, minted.content)
            token = minted.json()["token"]

            served = service.read_chunk_with_token(
                token=token, mag="1", cz=0, cy=0, cx=0
            )
        self.assertEqual(served.volume_id, self.volume.pk)
        self.assertGreater(served.result.nbytes, 0)

    def test_a_share_minted_token_names_only_the_shared_volume(self):
        """The whole containment argument: it cannot be replayed elsewhere."""
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            minted = self.anon.post(self.token_url(), {}, format="json")
            claims = tokens.verify(
                minted.json()["token"], deployment=service._deployment()
            )
        self.assertEqual(claims.volume_id, self.volume.pk)
        self.assertNotEqual(claims.volume_id, self.other.pk)
        # No account is embedded, so nothing downstream resolves it to a user.
        self.assertEqual(claims.user_id, service.SHARE_ISSUER_ID)

    def test_a_volume_outside_the_share_is_not_reachable_through_it(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.assertEqual(
                self.anon.get(self.caps_url(volume=self.other)).status_code, 404
            )
            self.assertEqual(
                self.anon.post(
                    self.token_url(volume=self.other), {}, format="json"
                ).status_code,
                404,
            )

    def test_revoking_the_share_closes_the_streaming_routes_too(self):
        from django.utils import timezone

        self.share.revoked_at = timezone.now()
        self.share.save(update_fields=["revoked_at"])
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.assertEqual(self.anon.get(self.caps_url()).status_code, 410)
            self.assertEqual(
                self.anon.post(self.token_url(), {}, format="json").status_code, 410
            )

    def test_an_unknown_token_reveals_nothing(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            self.assertEqual(self.anon.get(self.caps_url(token="nope")).status_code, 404)

    def test_the_share_routes_stay_off_when_the_chunk_service_is_off(self):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **OFF):
            self.assertEqual(self.anon.get(self.caps_url()).status_code, 503)
            self.assertEqual(
                self.anon.post(self.token_url(), {}, format="json").status_code, 503
            )
