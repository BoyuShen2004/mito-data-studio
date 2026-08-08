"""Phase 12, second layer — the chunk service serves the region mask.

The chunk path was image-only; a layer is now part of the address. The risks
worth testing are the ones a plausible-but-wrong implementation would fail:
serving one layer's voxels under the other's name, a token minted for the image
being replayed against the ROI, and the two layers' store handles evicting each
other. Authorization must be *identical* to the image layer — a region chunk is
the same volume's data, so it can be neither wider nor narrower.
"""

from __future__ import annotations

import tempfile
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
SHAPE = (4, 256, 256)


def _zarr_available() -> bool:
    try:
        store.require_zarr()
        return True
    except Exception:  # pragma: no cover
        return False


class RegionChunkTestCase(TestCase):
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

        rng = np.random.default_rng(29)
        self.source = rng.integers(0, 5000, size=SHAPE, dtype=np.uint16)
        self.image = external / "cortex.tif"
        tifffile.imwrite(str(self.image), self.source)

        self.mask = np.zeros(SHAPE, dtype=np.uint8)
        self.mask[:, 32:200, 32:200] = 1
        self.region = external / "cortex_roi.tif"
        tifffile.imwrite(str(self.region), self.mask)

        self.owner = User.objects.create_user(username="owner", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        self.manager = User.objects.create_user(username="mgr", password="x")
        UserProfile.objects.update_or_create(
            user=self.manager, defaults={"role": UserRole.MANAGER}
        )
        self.manager = User.objects.get(pk=self.manager.pk)

        self.project = Project.objects.create(title="Proj", created_by=self.owner)
        self.dataset = Dataset.objects.create(project=self.project, name="DS")
        self.volume = Volume.objects.create(
            project=self.project, dataset=self.dataset, name="cortex",
            image_path=str(self.image), region_mask_path=str(self.region),
            voxel_size_z=40.0, voxel_size_y=8.0, voxel_size_x=8.0,
        )

    def build(self, *layers):
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **PYRAMIDS):
            for layer in layers or ("image", "region"):
                pyramid_service.build_pyramid(self.volume, layer=layer)
        self.volume.refresh_from_db()


class RegionChunkReads(RegionChunkTestCase):
    def test_a_region_chunk_matches_the_mag_1_source_plane(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=2, cy=0, cx=0,
                user=self.owner, layer="region",
            )
        block = np.frombuffer(
            served.result.data, dtype=served.result.dtype
        ).reshape(served.result.shape)
        np.testing.assert_array_equal(block[0], self.mask[2])
        self.assertEqual(served.layer, "region")

    def test_the_two_layers_do_not_serve_each_other_voxels(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            image = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            region = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                user=self.owner, layer="region",
            )
        self.assertEqual(image.result.dtype, "uint16")
        self.assertEqual(region.result.dtype, "uint8")
        self.assertNotEqual(image.result.etag, region.result.etag)

    def test_a_volume_with_no_region_pyramid_is_404_on_that_layer_only(self):
        self.build("image")
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            with self.assertRaises(service.NotFound) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner, layer="region",
                )
        self.assertEqual(ctx.exception.reason, "no_pyramid")

    def test_an_unknown_layer_is_refused(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.NotFound) as ctx:
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.owner, layer="labels",
                )
        self.assertEqual(ctx.exception.reason, "unknown_layer")

    def test_reading_never_modifies_the_source_mask(self):
        before = self.region.read_bytes()
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                user=self.owner, layer="region",
            )
        self.assertEqual(self.region.read_bytes(), before)


class RegionCapabilities(RegionChunkTestCase):
    def test_capabilities_describe_the_region_grid_and_advertise_both_layers(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = service.capabilities(
                volume_id=self.volume.pk, user=self.owner, layer="region"
            )
        self.assertEqual(caps["layer"], "region")
        self.assertEqual(caps["layers"], ["image", "region"])
        self.assertEqual(caps["dtype"], "uint8")
        self.assertEqual(tuple(caps["mags"][0]["shape"]), SHAPE)
        self.assertNotIn("path", str(caps), "capabilities leaked a filesystem path")

    def test_an_image_only_volume_advertises_no_region_layer(self):
        self.build("image")
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            caps = service.capabilities(volume_id=self.volume.pk, user=self.owner)
        self.assertEqual(caps["layers"], ["image"])

    def test_the_two_layers_have_distinct_build_identities(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            image = service.capabilities(volume_id=self.volume.pk, user=self.owner)
            region = service.capabilities(
                volume_id=self.volume.pk, user=self.owner, layer="region"
            )
        self.assertNotEqual(image["build_identity"], region["build_identity"])


class RegionPermissions(RegionChunkTestCase):
    def test_a_stranger_is_denied_the_region_layer_too(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.PermissionDenied):
                service.read_chunk(
                    volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                    user=self.stranger, layer="region",
                )
            with self.assertRaises(service.PermissionDenied):
                service.issue_token(
                    volume_id=self.volume.pk, user=self.stranger, layers=["region"]
                )

    def test_a_manager_may_read_the_region_layer(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            served = service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                user=self.manager, layer="region",
            )
        self.assertGreater(served.result.nbytes, 0)


class RegionTokens(RegionChunkTestCase):
    def test_a_token_is_bound_to_the_layers_it_was_issued_for(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(
                volume_id=self.volume.pk, user=self.owner, layers=["region"]
            )
            self.assertEqual(issued["layers"], ["region"])
            served = service.read_chunk_with_token(
                token=issued["token"], mag="1", cz=0, cy=0, cx=0, layer="region"
            )
            self.assertGreater(served.result.nbytes, 0)
            # The same token must not reach the image layer.
            with self.assertRaises(service.TokenRejected):
                service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=0, cy=0, cx=0
                )

    def test_an_image_token_cannot_read_the_region_layer(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(volume_id=self.volume.pk, user=self.owner)
            self.assertEqual(issued["layers"], ["image"])
            with self.assertRaises(service.TokenRejected):
                service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=0, cy=0, cx=0, layer="region"
                )

    def test_a_token_may_carry_both_layers(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            issued = service.issue_token(
                volume_id=self.volume.pk, user=self.owner, layers=["image", "region"]
            )
            for layer in ("image", "region"):
                served = service.read_chunk_with_token(
                    token=issued["token"], mag="1", cz=0, cy=0, cx=0, layer=layer
                )
                self.assertGreater(served.result.nbytes, 0)

    def test_an_unknown_layer_cannot_be_minted(self):
        from core.deployment import fingerprint

        with self.assertRaises(tokens.TokenError) as ctx:
            tokens.issue(
                user_id=1, volume_id=1, mags=["1"],
                deployment=fingerprint(), layers=["labels"],
            )
        self.assertEqual(ctx.exception.reason, "bad_layer")

    def test_a_region_token_is_refused_when_that_layer_is_not_built(self):
        self.build("image")
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            with self.assertRaises(service.NotFound) as ctx:
                service.issue_token(
                    volume_id=self.volume.pk, user=self.owner, layers=["region"]
                )
        self.assertEqual(ctx.exception.reason, "no_pyramid")


class RegionHandleCache(RegionChunkTestCase):
    def test_the_layers_keep_separate_handles(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                user=self.owner, layer="region",
            )
            self.assertEqual(chunk_core.HANDLES.size(), 2)
            # Neither read evicted the other: both are now warm.
            before = METRICS.snapshot()["chunk_cache_misses_total"]
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=1, cy=0, cx=0, user=self.owner
            )
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=1, cy=0, cx=0,
                user=self.owner, layer="region",
            )
            after = METRICS.snapshot()["chunk_cache_misses_total"]
        self.assertEqual(after, before)

    def test_invalidating_one_layer_leaves_the_other_warm(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0,
                user=self.owner, layer="region",
            )
            dropped = service.invalidate_volume(self.volume.pk, layer="region")
        self.assertEqual(dropped, 1)
        self.assertEqual(chunk_core.HANDLES.size(), 1)

    def test_fetches_are_counted_per_layer(self):
        self.build()
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=0, cy=0, cx=0, user=self.owner
            )
            service.read_chunk(
                volume_id=self.volume.pk, mag="1", cz=1, cy=0, cx=0,
                user=self.owner, layer="region",
            )
        counts = METRICS.snapshot()["chunk_fetch_by_layer_total"]
        self.assertEqual(counts["image"], 1)
        self.assertEqual(counts["region"], 1)


class RegionHttpContract(RegionChunkTestCase):
    def url(self, name, *args):
        return reverse(name, args=args)

    def test_the_authenticated_endpoint_serves_the_layer_it_is_asked_for(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-read", self.volume.pk, "1", 0, 0, 0),
                {"layer": "region"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Mito-Layer"], "region")
        self.assertEqual(resp["X-Mito-Dtype"], "uint8")
        block = np.frombuffer(resp.content, dtype=np.uint8).reshape(
            [int(part) for part in resp["X-Mito-Shape"].split(",")]
        )
        np.testing.assert_array_equal(block[0], self.mask[0])

    def test_capabilities_default_to_the_image_layer(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-capabilities", self.volume.pk)
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["layer"], "image")

    def test_the_signed_endpoint_serves_a_region_chunk(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            token = self.client.post(
                self.url("api-volume-chunk-token", self.volume.pk),
                {"layers": ["region"]},
                content_type="application/json",
            ).json()["token"]
            resp = self.client.get(
                self.url("api-chunk-signed-read", "1", 0, 0, 0),
                {"t": token, "layer": "region"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Mito-Layer"], "region")

    def test_no_region_response_header_leaks_a_filesystem_path(self):
        self.build()
        self.client.force_login(self.owner)
        with override_settings(MITO_DATA_ROOT=self.root.resolve(), **ON):
            resp = self.client.get(
                self.url("api-volume-chunk-read", self.volume.pk, "1", 0, 0, 0),
                {"layer": "region"},
            )
        joined = " ".join(f"{k}:{v}" for k, v in resp.items())
        self.assertNotIn(str(self.root), joined)
        self.assertNotIn(".zarr", joined)
        self.assertNotIn("cortex_roi", joined)
