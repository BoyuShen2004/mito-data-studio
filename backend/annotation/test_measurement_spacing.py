import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import tifffile
from django.test import SimpleTestCase

from annotation.measurement_spacing import measurement_spacing


class MeasurementSpacingTests(SimpleTestCase):
    def spacing(self, path, stored=(None, None, None)):
        volume = SimpleNamespace(**dict(zip(('voxel_size_z', 'voxel_size_y', 'voxel_size_x'), stored)))
        with patch('annotation.measurement_spacing.volume_image_file', return_value=path):
            return measurement_spacing(volume)

    def test_imagej_nm_are_converted_to_storage_um(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'image.tif'
            tifffile.imwrite(path, np.zeros((3, 8, 8), dtype=np.uint8), imagej=True,
                             resolution=(1 / 16, 1 / 16), metadata={'axes': 'ZYX', 'unit': 'nm', 'spacing': 30})
            result = self.spacing(path)
            self.assertEqual(result['voxel_size_um_zyx'], [0.030, 0.016, 0.016])
            self.assertEqual(result['origins'], ['source_file'] * 3)

    def test_registered_values_are_preserved_and_only_missing_axes_are_filled(self):
        with patch('annotation.measurement_spacing.inspect_volume_voxel_size', return_value=(0.03, 0.016, None)):
            result = self.spacing(Path('unused'), (0.05, None, None))
        self.assertEqual(result['voxel_size_um_zyx'], [0.05, 0.016, None])
        self.assertEqual(result['origins'], ['registered', 'source_file', 'unknown'])

    def test_complete_registered_spacing_does_not_read_file(self):
        with patch('annotation.measurement_spacing.volume_image_file') as read:
            measurement_spacing(SimpleNamespace(voxel_size_z=0.03, voxel_size_y=0.016, voxel_size_x=0.016))
            read.assert_not_called()

    def test_unitless_tiff_resolution_is_not_physical_spacing(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'image.tif'
            tifffile.imwrite(path, np.zeros((3, 8, 8), dtype=np.uint8), photometric='minisblack')
            self.assertEqual(self.spacing(path)['voxel_size_um_zyx'], [None] * 3)

    def test_nifti_requires_declared_spatial_units(self):
        import nibabel as nib
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'image.nii.gz'
            image = nib.Nifti1Image(np.zeros((3, 8, 8)), np.diag([0.03, 0.016, 0.016, 1]))
            nib.save(image, path)
            self.assertEqual(self.spacing(path)['voxel_size_um_zyx'], [None] * 3)
            image.header.set_xyzt_units('micron')
            nib.save(image, path)
            np.testing.assert_allclose(self.spacing(path)['voxel_size_um_zyx'], [0.03, 0.016, 0.016])

    def test_hdf5_element_size_um(self):
        import h5py
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'image.h5'
            with h5py.File(path, 'w') as f:
                d = f.create_dataset('main', data=np.zeros((3, 8, 8)))
                d.attrs['element_size_um'] = [0.03, 0.016, 0.016]
            self.assertEqual(self.spacing(path)['voxel_size_um_zyx'], [0.03, 0.016, 0.016])

    def test_nonfinite_and_nonpositive_values_stay_unknown(self):
        with patch('annotation.measurement_spacing.inspect_volume_voxel_size', return_value=(float('nan'), 0, -1)):
            self.assertEqual(self.spacing(Path('unused'))['voxel_size_um_zyx'], [None] * 3)
