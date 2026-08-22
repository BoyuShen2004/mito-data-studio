import stat
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from core.storage import mito_data_storage


class DataStoragePermissionTests(SimpleTestCase):
    def test_uploaded_artifacts_use_shared_data_modes(self):
        with TemporaryDirectory() as tmp, override_settings(MITO_DATA_ROOT=Path(tmp)):
            # Production data roots are setgid, which makes every newly-created
            # subdirectory inherit both the service group and the setgid bit.
            Path(tmp).chmod(0o2775)
            name = mito_data_storage.save("uploads/example.bin", ContentFile(b"data"))
            path = Path(tmp) / name

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o664)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o2775)
