"""First-class NIfTI registration metadata and nnU-Net pairing coverage."""

import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.choices import FileFormat
from volumes.services import pair_by_case, register_dataset, scan_data_sources


class NiftiRegistrationTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-nifti-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "data"
        self.images = base / "imagesTr"
        self.labels = base / "labelsTr"
        self.root.mkdir()
        self.images.mkdir()
        self.labels.mkdir()
        self.array = np.arange(4 * 8 * 10, dtype=np.int16).reshape(4, 8, 10)
        self._write(self.images / "case_a_0000.nii.gz", self.array)
        self._write(self.labels / "case_a.nii.gz", (self.array > 40).astype(np.uint16))
        self._write(self.images / "case_b_0000.nii", self.array)
        self._write(self.labels / "case_b.nii", (self.array > 60).astype(np.uint16))
        self.user = get_user_model().objects.create_user("nii-manager")

    @staticmethod
    def _write(path, zyx):
        image = nib.Nifti1Image(zyx.transpose(2, 1, 0), np.eye(4))
        image.header.set_zooms((0.008, 0.009, 0.04))
        image.header.set_xyzt_units("mm")
        nib.save(image, str(path))

    def test_scan_and_pair_nii_and_nii_gz(self):
        scan = scan_data_sources(str(self.images), str(self.labels))
        self.assertEqual(
            {row["extension"] for row in scan["image_files"]},
            {".nii", ".nii.gz"},
        )
        pairs, unmatched_images, unmatched_masks, extras = pair_by_case(
            ["case_a_0000.nii.gz", "case_b_0000.nii"],
            ["case_a.nii.gz", "case_b.nii"],
        )
        self.assertEqual(len(pairs), 2)
        self.assertEqual((unmatched_images, unmatched_masks, extras), ([], [], []))

    def test_register_populates_format_shape_voxels_and_dtype(self):
        with override_settings(MITO_DATA_ROOT=self.root):
            _project, volumes = register_dataset(
                created_by=self.user,
                dataset="nnunet",
                image_directory=str(self.images),
                mask_directory=str(self.labels),
            )
        self.assertEqual(len(volumes), 2)
        for volume in volumes:
            volume.refresh_from_db()
            self.assertEqual(volume.file_format, FileFormat.NIFTI)
            self.assertEqual(
                (volume.shape_z, volume.shape_y, volume.shape_x), (4, 8, 10)
            )
            self.assertAlmostEqual(volume.voxel_size_z, 40.0, places=4)
            self.assertAlmostEqual(volume.voxel_size_y, 9.0, places=4)
            self.assertAlmostEqual(volume.voxel_size_x, 8.0, places=4)
            self.assertEqual(volume.metadata["dtype"], "int16")
