import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from projects.models import Project
from volumes.models import Volume
from volumes.region_masks import calculate_region_mask_coverage, refresh_region_mask_coverage
from volumes.serializers import VolumeSerializer
from volumes.services import register_volume


class RegionMaskCoverageTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        user = get_user_model().objects.create_user("coverage-owner")
        self.project = Project.objects.create(title="Coverage", created_by=user)

    def tearDown(self):
        self.tmp.cleanup()

    def _volume(self, name="mask.tif"):
        return Volume.objects.create(
            project=self.project,
            name=name,
            image_path="image.tif",
            region_mask_path=name,
        )

    def test_empty_mask_is_cached_as_real_zero(self):
        tifffile.imwrite(self.root / "empty.tif", np.zeros((2, 3, 4), dtype=np.uint8))
        volume = self._volume("empty.tif")
        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertEqual(refresh_region_mask_coverage(volume), 0.0)
        volume.refresh_from_db()
        data = VolumeSerializer(volume).data
        self.assertEqual(data["region_mask_coverage"], 0.0)
        self.assertIs(data["region_mask_empty"], True)

    def test_known_nonempty_mask_reports_fraction(self):
        mask = np.zeros((2, 4, 5), dtype=np.uint8)
        mask[:, :2, :2] = 7  # 8 / 40 voxels
        tifffile.imwrite(self.root / "known.tif", mask)
        with override_settings(MITO_DATA_ROOT=self.root):
            self.assertAlmostEqual(calculate_region_mask_coverage("known.tif"), 0.2)

    def test_coverage_is_format_neutral_for_tiff_hdf5_and_nifti(self):
        import h5py
        import nibabel as nib

        mask = np.zeros((3, 4, 5), dtype=np.uint8)
        mask[:, :2, :2] = 1
        tifffile.imwrite(self.root / "mask.tif", mask)
        with h5py.File(self.root / "mask.h5", "w") as handle:
            handle.create_dataset("main", data=mask)
        nib.save(
            nib.Nifti1Image(mask.transpose(2, 1, 0), np.eye(4)),
            self.root / "mask.nii.gz",
        )
        with override_settings(MITO_DATA_ROOT=self.root):
            values = [
                calculate_region_mask_coverage(name)
                for name in ("mask.tif", "mask.h5", "mask.nii.gz")
            ]
        for value in values:
            self.assertAlmostEqual(value, 0.2)

    def test_missing_mask_has_null_coverage_not_fake_zero(self):
        volume = Volume.objects.create(
            project=self.project, name="No ROI", image_path="image.tif"
        )
        data = VolumeSerializer(volume).data
        self.assertFalse(data["has_region_mask"])
        self.assertIsNone(data["region_mask_coverage"])
        self.assertIsNone(data["region_mask_empty"])

    def test_registration_does_not_scan_region_coverage_inline(self):
        tifffile.imwrite(self.root / "image.tif", np.zeros((2, 3, 4), dtype=np.uint8))
        mask = np.zeros((2, 3, 4), dtype=np.uint8)
        mask[:, :, :1] = 1
        tifffile.imwrite(self.root / "register-roi.tif", mask)
        with override_settings(MITO_DATA_ROOT=self.root):
            volume = register_volume(
                project=self.project,
                name="registered",
                image_path="image.tif",
                region_mask_path="register-roi.tif",
                enqueue_pyramid=False,
            )
        volume.refresh_from_db()
        self.assertIsNone(volume.region_mask_coverage)
