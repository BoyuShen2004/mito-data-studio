import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.test import SimpleTestCase

from annotation.visualization import slice_io


class ReadOnlySourceVolumeTests(SimpleTestCase):
    def tearDown(self):
        slice_io.clear_caches()

    def test_tiff_source_opens_from_a_read_only_mount_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="mito-readonly-source-") as root:
            path = Path(root) / "source.tif"
            expected = np.arange(3 * 5 * 7, dtype=np.uint16).reshape(3, 5, 7)
            tifffile.imwrite(path, expected)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            os.chmod(path, 0o444)

            opened = slice_io._open_volume(path)

            np.testing.assert_array_equal(opened, expected)
            self.assertFalse(opened.flags.writeable)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
