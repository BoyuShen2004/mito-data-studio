"""Cross-format contract for registration header metadata."""

import tempfile
from pathlib import Path

import h5py
import nibabel as nib
import numpy as np
import tifffile
from django.test import SimpleTestCase

from core.utils import clear_header_cache, inspect_volume_shape, inspect_volume_voxel_size


class VolumeHeaderMetadataParityTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mito-meta-parity-")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(clear_header_cache)
        self.root = Path(self.tmp.name)
        self.array = np.zeros((4, 8, 10), dtype=np.uint8)

    def test_tiff_nifti_hdf5_have_identical_shape_and_voxel_contract(self):
        paths = {
            "tiff": self.root / "volume.tif",
            "nifti": self.root / "volume.nii.gz",
            "hdf5": self.root / "volume.h5",
        }
        tifffile.imwrite(
            paths["tiff"], self.array, imagej=True, resolution=(125, 125),
            metadata={"axes": "ZYX", "spacing": .04, "unit": "um"},
        )
        nii = nib.Nifti1Image(self.array.transpose(2, 1, 0), np.eye(4))
        nii.header.set_zooms((.000008, .000008, .00004))
        nii.header.set_xyzt_units("mm")
        nib.save(nii, paths["nifti"])
        with h5py.File(paths["hdf5"], "w") as handle:
            dataset = handle.create_dataset("main", data=self.array)
            dataset.attrs["element_size_um"] = [.04, .008, .008]

        for path in paths.values():
            self.assertEqual(inspect_volume_shape(path), (10, 8, 4))
            z, y, x = inspect_volume_voxel_size(path)
            self.assertAlmostEqual(z, .04, places=5)
            self.assertAlmostEqual(y, .008, places=5)
            self.assertAlmostEqual(x, .008, places=5)

    def test_missing_physical_spacing_is_null_not_an_isotropic_guess(self):
        tif = self.root / "bare.tif"
        h5 = self.root / "bare.h5"
        tifffile.imwrite(tif, self.array)
        with h5py.File(h5, "w") as handle:
            handle.create_dataset("main", data=self.array)

        self.assertIsNone(inspect_volume_voxel_size(tif))
        self.assertIsNone(inspect_volume_voxel_size(h5))
