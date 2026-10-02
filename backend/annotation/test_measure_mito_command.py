import csv
import io
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from annotation.visualization import slice_io
from core.choices import LabelType
from projects.models import Project
from volumes.models import Volume


class MeasureMitoCommandTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mito-measure-cmd-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        override = override_settings(MITO_DATA_ROOT=str(self.root))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(slice_io.clear_caches)

        labels = np.zeros((20, 20, 120), dtype=np.uint16)
        labels[7:13, 7:13, 10:110] = 5
        labels[1:3, 1:3, 1:3] = 9
        tifffile.imwrite(self.root / "labels.tif", labels)

        project = Project.objects.create(title="measure")
        self.volume = Volume.objects.create(
            project=project, name="v", image_path="image.tif",
            label_path="labels.tif", label_type=LabelType.PARTIAL,
            shape_z=20, shape_y=20, shape_x=120,
            voxel_size_z=30, voxel_size_y=16, voxel_size_x=16,
        )

    def run_command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        call_command("measure_mito", "--volume", str(self.volume.pk), *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_writes_csv_to_stdout(self):
        out, err = self.run_command()
        rows = list(csv.DictReader(io.StringIO(out)))
        self.assertEqual([r["label_id"] for r in rows], ["5", "9"])
        self.assertEqual(rows[0]["voxel_count"], "3600")
        self.assertGreater(float(rows[0]["skeleton_length_um"]), 1.5)
        self.assertLess(float(rows[0]["skeleton_length_um"]), 1.8)
        self.assertEqual(float(rows[1]["skeleton_length_um"]), 0.0)
        self.assertIn("official label", err)

    def test_out_writes_csv_file(self):
        target = self.root / "out.csv"
        out, _ = self.run_command("--out", str(target))
        self.assertEqual(out, "")
        rows = list(csv.DictReader(target.open(newline="")))
        self.assertEqual([r["label_id"] for r in rows], ["5", "9"])

    def test_missing_voxel_size_is_an_error(self):
        Volume.objects.filter(pk=self.volume.pk).update(voxel_size_y=None)
        with self.assertRaisesMessage(CommandError, "voxel size"):
            self.run_command()

    def test_volume_without_official_label_is_an_error(self):
        Volume.objects.filter(pk=self.volume.pk).update(label_type=LabelType.NONE)
        with self.assertRaisesMessage(CommandError, "no official label"):
            self.run_command()

    def test_working_without_working_copy_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "no working label copy"):
            self.run_command("--working")

    def test_missing_label_file_is_an_error(self):
        (self.root / "labels.tif").unlink()
        with self.assertRaisesMessage(CommandError, "not found"):
            self.run_command()

    def test_unknown_volume_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "No volume with id"):
            call_command("measure_mito", "--volume", "999999", stdout=io.StringIO(), stderr=io.StringIO())
