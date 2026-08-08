import os
import tempfile

from django.test import TestCase, override_settings

from core.utils import inspect_volume_voxel_size
from core.choices import LabelType, TaskType
from annotation.services import create_whole_volume_task
from projects.services import create_project
from volumes.models import Volume
import json

from volumes.services import (
    DataRegistrationError,
    case_key,
    detect_volume_pairs,
    infer_task_type,
    pair_by_case,
    register_dataset,
    register_volume,
    scan_data_sources,
    scan_hpc_directory,
    update_volume_metadata,
)

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_reg_test_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class VoxelAutodetectTests(TestCase):
    """Registration reads shape *and* voxel size from the image headers."""

    def test_registration_detects_voxel_size(self):
        import numpy as np
        import tifffile

        directory = os.path.join(_TMP_ROOT, "voxel_ds")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "sample_0000.tiff")
        # x spacing 0.5µm (xres 2 px/µm), y 0.25µm (yres 4), z spacing 0.2µm.
        tifffile.imwrite(
            path,
            np.zeros((8, 16, 32), dtype=np.uint8),
            imagej=True,
            resolution=(2.0, 4.0),
            metadata={"spacing": 0.2, "unit": "um", "axes": "ZYX"},
        )

        project = create_project(title="Voxel", reviewed=True)
        _project, volumes = register_dataset(
            created_by=None,
            dataset="VoxelDS",
            volume="vol",
            project=project,
            image_directory=directory,
            files=[{"name": "sample_0000.tiff"}],
        )
        vol = volumes[0]
        self.assertEqual((vol.shape_z, vol.shape_y, vol.shape_x), (8, 16, 32))
        self.assertAlmostEqual(vol.voxel_size_z, 0.2, places=5)
        self.assertAlmostEqual(vol.voxel_size_y, 0.25, places=5)
        self.assertAlmostEqual(vol.voxel_size_x, 0.5, places=5)

    def test_inspect_volume_voxel_size_reads_ome_physical_sizes(self):
        import numpy as np
        import tifffile

        directory = os.path.join(_TMP_ROOT, "voxel_ome_ds")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "sample_ome.ome.tif")
        tifffile.imwrite(
            path,
            np.zeros((8, 16, 32), dtype=np.uint8),
            metadata={
                "axes": "ZYX",
                "PhysicalSizeZ": 0.03,
                "PhysicalSizeY": 0.008,
                "PhysicalSizeX": 0.008,
            },
            ome=True,
        )

        voxel = inspect_volume_voxel_size(path)
        self.assertIsNotNone(voxel)
        self.assertAlmostEqual(voxel[0], 0.03, places=5)
        self.assertAlmostEqual(voxel[1], 0.008, places=5)
        self.assertAlmostEqual(voxel[2], 0.008, places=5)

    def test_ome_units_are_normalised_to_micrometres(self):
        """A file recording nm must not come back as if it were µm — and OME
        must win outright over the TIFF resolution tags rather than being
        merged with them axis by axis.

        This is the real-data case that motivated it: the EM exports here carry
        ``PhysicalSize*`` in **nm** *and* an ``XResolution`` rational that
        decodes to ~10^6 µm. Mixing the two produced a "voxel" ~30,000x wider
        than it was deep, which the 3D view drew as a flat sheet.
        """
        import numpy as np
        import tifffile

        directory = os.path.join(_TMP_ROOT, "voxel_nm_ds")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "sample_nm.ome.tif")
        tifffile.imwrite(
            path,
            np.zeros((4, 8, 8), dtype=np.uint8),
            resolution=(4828, 4294967295),  # nonsense in-plane tag, as in the real files
            metadata={
                "axes": "ZYX",
                "PhysicalSizeZ": 28.0,
                "PhysicalSizeZUnit": "nm",
                "PhysicalSizeY": 11.24,
                "PhysicalSizeYUnit": "nm",
                "PhysicalSizeX": 11.24,
                "PhysicalSizeXUnit": "nm",
            },
            ome=True,
        )

        voxel = inspect_volume_voxel_size(path)
        self.assertIsNotNone(voxel)
        self.assertAlmostEqual(voxel[0], 0.028, places=6)
        self.assertAlmostEqual(voxel[1], 0.01124, places=6)
        self.assertAlmostEqual(voxel[2], 0.01124, places=6)
        # Plausible anisotropy (2.5:1), so the renderer uses it as-is.
        self.assertLess(max(voxel) / min(voxel), 10)

    def test_resolution_tags_without_a_unit_are_not_a_physical_size(self):
        """``ResolutionUnit = none`` records an aspect ratio, not a size."""
        import numpy as np
        import tifffile

        directory = os.path.join(_TMP_ROOT, "voxel_nounit_ds")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "sample_nounit.tif")
        tifffile.imwrite(
            path,
            np.zeros((4, 8, 8), dtype=np.uint8),
            resolution=(2.0, 4.0),
            resolutionunit="NONE",
        )
        self.assertIsNone(inspect_volume_voxel_size(path))

    def test_imagej_unknown_unit_is_not_assumed_to_be_micrometres(self):
        import numpy as np
        import tifffile

        path = os.path.join(_TMP_ROOT, "voxel_unknown_imagej.tif")
        tifffile.imwrite(
            path,
            np.zeros((4, 8, 8), dtype=np.uint8),
            imagej=True,
            resolution=(2.0, 4.0),
            metadata={"spacing": 0.2, "unit": "um.", "axes": "ZYX"},
        )
        self.assertIsNone(inspect_volume_voxel_size(path))

    def test_ome_explicit_unknown_unit_is_not_guessed(self):
        import numpy as np
        import tifffile

        path = os.path.join(_TMP_ROOT, "voxel_unknown.ome.tif")
        tifffile.imwrite(
            path,
            np.zeros((4, 8, 8), dtype=np.uint8),
            metadata={
                "axes": "ZYX",
                "PhysicalSizeZ": 1.0,
                "PhysicalSizeZUnit": "furlong",
                "PhysicalSizeY": 1.0,
                "PhysicalSizeYUnit": "furlong",
                "PhysicalSizeX": 1.0,
                "PhysicalSizeXUnit": "furlong",
            },
            ome=True,
        )
        self.assertIsNone(inspect_volume_voxel_size(path))


class TaskTypeInferenceTests(TestCase):
    def test_label_type_mapping(self):
        self.assertEqual(infer_task_type(LabelType.NONE), TaskType.MANUAL_ANNOTATION)
        self.assertEqual(
            infer_task_type(LabelType.PREDICTION), TaskType.PREDICTION_PROOFREADING
        )
        self.assertEqual(infer_task_type(LabelType.PROOFREAD), TaskType.FINAL_REVIEW)
        self.assertEqual(infer_task_type(LabelType.PARTIAL), TaskType.MANUAL_ANNOTATION)

    def test_override_wins(self):
        self.assertEqual(
            infer_task_type(LabelType.NONE, TaskType.QC_REVIEW), TaskType.QC_REVIEW
        )


class WholeVolumeTaskTests(TestCase):
    """A volume is one assignable unit: exactly one task, full extent."""

    def setUp(self):
        self.project = create_project(title="P")

    def _volume(self, label_type=LabelType.NONE):
        # label_type describes the mask, so a typed volume needs one — without
        # a label_path registration normalises the type back to `none`.
        return register_volume(
            project=self.project,
            name="vol1",
            image_path="vol1.tiff",
            label_path="" if label_type == LabelType.NONE else "vol1_mask.tiff",
            label_type=label_type,
            autodetect_shape=False,
        )

    def test_one_task_spans_the_whole_volume(self):
        volume = self._volume()
        volume.shape_x, volume.shape_y, volume.shape_z = 512, 384, 32
        volume.save()

        task = create_whole_volume_task(volume)
        self.assertIsNotNone(task)
        self.assertEqual(volume.tasks.count(), 1)
        self.assertEqual((task.z_start, task.z_end), (0, 32))
        self.assertEqual((task.y_start, task.y_end), (0, 384))
        self.assertEqual((task.x_start, task.x_end), (0, 512))
        self.assertEqual(task.task_type, TaskType.MANUAL_ANNOTATION)

    def test_prediction_volume_makes_a_proofreading_task(self):
        volume = self._volume(LabelType.PREDICTION)
        volume.shape_x, volume.shape_y, volume.shape_z = 10, 10, 16
        volume.save()
        task = create_whole_volume_task(volume)
        self.assertEqual(task.task_type, TaskType.PREDICTION_PROOFREADING)

    def test_256_layer_task_displays_frames_1_through_256(self):
        volume = self._volume()
        volume.shape_x, volume.shape_y, volume.shape_z = 10, 10, 256
        volume.save()

        task = create_whole_volume_task(volume)

        self.assertEqual((task.z_start, task.z_end), (0, 256))
        self.assertEqual(task.frame_label, "z 1–256")

    def test_second_call_does_not_add_a_task(self):
        volume = self._volume()
        volume.shape_x, volume.shape_y, volume.shape_z = 10, 10, 16
        volume.save()
        self.assertIsNotNone(create_whole_volume_task(volume))
        self.assertIsNone(create_whole_volume_task(volume))
        self.assertEqual(volume.tasks.count(), 1)

    def test_volume_without_shape_gets_no_task(self):
        volume = self._volume()
        self.assertIsNone(create_whole_volume_task(volume))
        self.assertEqual(volume.tasks.count(), 0)

    def test_no_service_can_create_more_than_one_task_per_volume(self):
        """The frame-splitting helpers are gone, not merely unused."""
        import volumes.services as volume_services

        for name in (
            "create_tasks_from_volume",
            "split_volume_into_tasks",
            "split_volume_by_frames",
        ):
            self.assertFalse(
                hasattr(volume_services, name),
                f"volumes.services.{name} should have been removed",
            )


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class RegisterDatasetTests(TestCase):
    def setUp(self):
        # Create a directory of mixed files under the data root.
        self.dir = tempfile.mkdtemp(dir=_TMP_ROOT)
        for name in ("a.tif", "b.tiff", "c.nii.gz", "notes.txt", "d.png"):
            with open(os.path.join(self.dir, name), "wb") as fh:
                fh.write(b"x")

    def test_scan_lists_only_supported_files(self):
        result = scan_hpc_directory(self.dir)
        names = {f["name"] for f in result["files"]}
        self.assertEqual(names, {"a.tif", "b.tiff", "c.nii.gz"})

    def test_register_all_supported_files_as_volumes(self):
        project, volumes = register_dataset(
            created_by=None,
            dataset="DatasetX",
            volume="big_volume",  # ignored legacy grouping field
            hpc_directory=self.dir,
        )
        self.assertEqual(project.dataset, "DatasetX")
        self.assertEqual(len(volumes), 3)
        # Each file is its own volume named by case id.
        self.assertEqual(len({v.name for v in volumes}), 3)
        self.assertEqual({v.project_id for v in volumes}, {project.id})

    def test_register_selected_files_with_rename(self):
        project, volumes = register_dataset(
            created_by=None,
            dataset="DatasetY",
            volume="vol1",
            hpc_directory=self.dir,
            files=[{"name": "a.tif", "chunk_id": "crop-1"}],
            metadata={"organism": "mouse"},
        )
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0].name, "crop-1")
        # Metadata belongs to the dataset, not the project that contains it.
        dataset = project.datasets.get(name="DatasetY")
        self.assertEqual(dataset.metadata.get("organism"), "mouse")
        self.assertEqual(volumes[0].dataset_id, dataset.id)

    def test_missing_dataset_rejected(self):
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None, dataset="", volume="v", hpc_directory=self.dir
            )

    def test_top_level_volume_name_is_optional(self):
        """Register Data names each volume from the file/case id."""
        _project, volumes = register_dataset(
            created_by=None, dataset="NoVolumeName", hpc_directory=self.dir
        )
        self.assertEqual(len(volumes), 3)
        self.assertTrue(all(v.name for v in volumes))
        self.assertEqual(len({v.name for v in volumes}), 3)

    def test_per_volume_rename_sets_volume_name(self):
        _project, volumes = register_dataset(
            created_by=None,
            dataset="Renamed",
            hpc_directory=self.dir,
            files=[{"name": "a.tif", "chunk_id": "crop-1"}],
        )
        self.assertEqual(volumes[0].name, "crop-1")

    def test_legacy_shared_volume_field_is_ignored(self):
        """Top-level ``volume=`` no longer groups crops; renames win per file."""
        _project, volumes = register_dataset(
            created_by=None,
            dataset="Grouped",
            volume="big_volume",
            hpc_directory=self.dir,
            files=[{"name": "a.tif", "chunk_id": "crop-1"},
                   {"name": "b.tiff", "chunk_id": "crop-2"}],
        )
        self.assertEqual({v.name for v in volumes}, {"crop-1", "crop-2"})

    def test_unsupported_extension_rejected(self):
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None,
                dataset="d",
                volume="v",
                hpc_directory=self.dir,
                files=[{"name": "notes.txt"}],
            )

    def test_missing_directory_rejected(self):
        with self.assertRaises(DataRegistrationError):
            scan_hpc_directory("does/not/exist")


class DetectPairsTests(TestCase):
    def test_pairs_image_and_mask_by_shared_base(self):
        pairs, unpaired = detect_volume_pairs(
            [
                "cortex1_image.tif",
                "cortex1_mask.tif",
                "cortex2_raw.tif",
                "cortex2_seg.tif",
                "lonely_volume.tif",
            ]
        )
        by_image = {p["image"]: p["mask"] for p in pairs}
        self.assertEqual(by_image["cortex1_image.tif"], "cortex1_mask.tif")
        self.assertEqual(by_image["cortex2_raw.tif"], "cortex2_seg.tif")
        self.assertEqual(unpaired, ["lonely_volume.tif"])

    def test_bare_name_and_label_suffix(self):
        pairs, unpaired = detect_volume_pairs(["vol.tif", "vol_label.tif"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image"], "vol.tif")
        self.assertEqual(pairs[0]["mask"], "vol_label.tif")
        self.assertEqual(unpaired, [])


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class RegisterPairsTests(TestCase):
    def setUp(self):
        # A folder holding two image+mask pairs plus one unrelated volume.
        self.dir = tempfile.mkdtemp(dir=_TMP_ROOT)
        for name in (
            "sampleA_image.tif",
            "sampleA_mask.tif",
            "sampleB_image.tif",
            "sampleB_mask.tif",
            "other_volume.tif",
        ):
            with open(os.path.join(self.dir, name), "wb") as fh:
                fh.write(b"x")

    def test_auto_registers_all_pairs_and_unpaired(self):
        project, volumes = register_dataset(
            created_by=None,
            dataset="D",
            volume="v",
            hpc_directory=self.dir,
        )
        # 2 pairs (with masks) + 1 unpaired image = 3 volumes.
        self.assertEqual(len(volumes), 3)
        with_mask = [v for v in volumes if v.label_path]
        self.assertEqual(len(with_mask), 2)
        for v in with_mask:
            self.assertEqual(v.label_type, LabelType.PREDICTION)

    def test_register_single_explicit_pair_from_mixed_folder(self):
        project, volumes = register_dataset(
            created_by=None,
            dataset="D2",
            volume="v",
            hpc_directory=self.dir,
            pairs=[
                {
                    "image": "sampleA_image.tif",
                    "mask": "sampleA_mask.tif",
                    "chunk_id": "A",
                }
            ],
            label_type=LabelType.PARTIAL,
        )
        self.assertEqual(len(volumes), 1)
        v = volumes[0]
        self.assertEqual(v.name, "A")
        self.assertTrue(v.label_path.endswith("sampleA_mask.tif"))
        self.assertEqual(v.label_type, LabelType.PARTIAL)

    def test_proofread_label_type_is_rejected(self):
        """`proofread` is no longer an accepted label type for new writes."""
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None,
                dataset="D4",
                volume="v",
                hpc_directory=self.dir,
                pairs=[
                    {
                        "image": "sampleA_image.tif",
                        "mask": "sampleA_mask.tif",
                        "chunk_id": "A",
                    }
                ],
                label_type=LabelType.PROOFREAD,
            )

    def test_mask_without_an_explicit_label_type_defaults_to_prediction(self):
        """Bulk registration cannot know the mask's provenance — default it."""
        _project, volumes = register_dataset(
            created_by=None,
            dataset="D5",
            volume="v",
            hpc_directory=self.dir,
            pairs=[{"image": "sampleA_image.tif", "mask": "sampleA_mask.tif"}],
        )
        self.assertEqual(volumes[0].label_type, LabelType.PREDICTION)

    def test_explicit_none_with_a_mask_is_rejected(self):
        """An explicit `none` alongside a mask is a contradiction, not a default."""
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None,
                dataset="D6",
                volume="v",
                hpc_directory=self.dir,
                pairs=[{"image": "sampleA_image.tif", "mask": "sampleA_mask.tif"}],
                label_type=LabelType.NONE,
            )

    def test_pair_with_missing_mask_rejected(self):
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None,
                dataset="D3",
                volume="v",
                hpc_directory=self.dir,
                pairs=[{"image": "sampleA_image.tif", "mask": "nope.tif"}],
            )


class CaseKeyTests(TestCase):
    """Pairing hinges on the case id, so pin down how it is derived."""

    def test_channel_suffix_stripped_from_images(self):
        self.assertEqual(case_key("case_00_0000.tiff"), "case_00")
        self.assertEqual(case_key("jrc_mus-kidney_crop129_0000.nii.gz"), "jrc_mus-kidney_crop129")

    def test_label_without_suffix_yields_same_key(self):
        self.assertEqual(case_key("case_00.tiff"), case_key("case_00_0000.tiff"))

    def test_non_channel_digits_are_kept(self):
        # Only a 4-digit trailing group is a channel; crop ids must survive.
        self.assertEqual(case_key("crop_12.tif"), "crop_12")
        self.assertEqual(case_key("Dataset001_ME2-Beta__high_c1.nii.gz"),
                         "Dataset001_ME2-Beta__high_c1")


class PairByCaseTests(TestCase):
    """Cross-directory pairing: names come from two separate folders."""

    def test_pairs_nnunet_images_and_labels(self):
        pairs, un_img, un_mask, extras = pair_by_case(
            ["case_00_0000.tiff", "case_01_0000.tiff"],
            ["case_00.tiff", "case_01.tiff"],
        )
        self.assertEqual([(p["image"], p["mask"]) for p in pairs], [
            ("case_00_0000.tiff", "case_00.tiff"),
            ("case_01_0000.tiff", "case_01.tiff"),
        ])
        self.assertEqual((un_img, un_mask, extras), ([], [], []))

    def test_leftovers_on_both_sides_are_surfaced(self):
        pairs, un_img, un_mask, _ = pair_by_case(
            ["a_0000.tif", "orphan_0000.tif"], ["a.tif", "stray.tif"]
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(un_img, ["orphan_0000.tif"])
        self.assertEqual(un_mask, ["stray.tif"])

    def test_extra_channels_reported_not_dropped(self):
        pairs, un_img, un_mask, extras = pair_by_case(
            ["m_0000.tif", "m_0001.tif"], ["m.tif"]
        )
        # Channel 0 represents the volume; the second channel is surfaced.
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image"], "m_0000.tif")
        self.assertEqual(extras, ["m_0001.tif"])
        self.assertEqual(un_img, [])

    def test_pairs_role_suffix_after_ome_and_repeated_separators(self):
        image = "2026-02-18_18-03__heart__volume.ome.tif"
        mask = "2026-02-18_18-03__heart__volume.ome_mask.tif"
        pairs, un_img, un_mask, extras = pair_by_case([image], [mask])
        self.assertEqual([(p["image"], p["mask"]) for p in pairs], [(image, mask)])
        self.assertEqual((un_img, un_mask, extras), ([], [], []))

    def test_pairing_is_case_insensitive(self):
        pairs, un_img, un_mask, _ = pair_by_case(
            ["Heart_VOLUME.ome.TIF"], ["heart_volume.ome_MASK.tif"]
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual((un_img, un_mask), ([], []))

    def test_region_role_suffix_does_not_break_pairing(self):
        pairs, un_img, un_mask, _ = pair_by_case(
            ["heart_volume.ome.tif"], ["heart_volume.ome_region_mask.tif"]
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual((un_img, un_mask), ([], []))

    def test_broad_role_key_does_not_guess_between_ambiguous_images(self):
        pairs, un_img, un_mask, extras = pair_by_case(
            ["heart-image.tif", "heart_image.tif"], ["heart_mask.tif"]
        )
        self.assertEqual(pairs, [])
        self.assertEqual(un_img, ["heart-image.tif", "heart_image.tif"])
        self.assertEqual(un_mask, ["heart_mask.tif"])
        self.assertEqual(extras, [])


class SingleDirectoryPairingTests(TestCase):
    """The nnU-Net convention also shows up flattened into one folder."""

    def test_channel_suffix_convention_in_one_folder(self):
        pairs, unpaired = detect_volume_pairs(["vol1_0000.tiff", "vol1.tiff"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image"], "vol1_0000.tiff")
        self.assertEqual(pairs[0]["mask"], "vol1.tiff")
        self.assertEqual(unpaired, [])

    def test_token_convention_still_works(self):
        pairs, _ = detect_volume_pairs(["cortex_image.tif", "cortex_mask.tif"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["mask"], "cortex_mask.tif")

    def test_ome_role_suffix_and_repeated_separators_work_in_one_folder(self):
        image = "2026-02-18_18-03__heart__volume.ome.tif"
        mask = "2026-02-18_18-03__heart__volume.ome_mask.tif"
        pairs, unpaired = detect_volume_pairs([image, mask])
        self.assertEqual([(p["image"], p["mask"]) for p in pairs], [(image, mask)])
        self.assertEqual(unpaired, [])


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class SeparateDirectoryRegistrationTests(TestCase):
    """Images and masks in different folders — the nnU-Net layout."""

    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_TMP_ROOT)
        self.images = os.path.join(self.root, "imagesTr")
        self.labels = os.path.join(self.root, "labelsTr")
        self.instance = os.path.join(self.root, "labelsTr-instance")
        for d in (self.images, self.labels, self.instance):
            os.makedirs(d)
        for case in ("case_00", "case_01"):
            self._touch(self.images, f"{case}_0000.tiff")
            self._touch(self.labels, f"{case}.tiff")
            self._touch(self.instance, f"{case}.tiff")

    def _touch(self, directory, name):
        with open(os.path.join(directory, name), "wb") as fh:
            fh.write(b"x")

    def _write_manifest(self, mask_dir="labelsTr"):
        manifest = {
            "name": "Demo",
            "description": "demo dataset",
            "reference": "a paper",
            "labels": {"background": 0, "mitochondria": 1},
            "channel_names": {"0": "EM"},
            "training": [
                {"image": f"./imagesTr/{c}_0000.tiff", "label": f"./{mask_dir}/{c}.tiff"}
                for c in ("case_00", "case_01")
            ],
        }
        with open(os.path.join(self.root, "dataset.json"), "w") as fh:
            json.dump(manifest, fh)

    def test_scan_lists_files_without_auto_pairing(self):
        result = scan_data_sources(self.images, self.labels)
        self.assertEqual(result["pairs"], [])
        self.assertEqual(result["pairing_source"], "manual")
        self.assertEqual(len(result["unmatched_images"]), 2)
        self.assertEqual(len(result["mask_files"]), 2)
        self.assertEqual(len(result["unmatched_masks"]), 2)
        self.assertEqual(result["split"], "train")

    def test_register_stores_label_from_the_mask_directory(self):
        project, volumes = register_dataset(
            created_by=None,
            dataset="D",
            volume="v",
            image_directory=self.images,
            mask_directory=self.labels,
        )
        self.assertEqual(len(volumes), 2)
        for v in volumes:
            self.assertIn("imagesTr", v.image_path)
            self.assertIn("labelsTr", v.label_path)
            self.assertEqual(v.label_type, LabelType.PREDICTION)
            self.assertEqual(v.metadata.get("split"), "train")
        # Volumes are named by case id, not by raw filename.
        self.assertEqual(sorted(v.name for v in volumes), ["case_00", "case_01"])

    def test_manifest_metadata_still_prefills_without_auto_pairs(self):
        self._write_manifest()
        result = scan_data_sources(self.images, self.labels)
        self.assertEqual(result["pairing_source"], "manual")
        self.assertEqual(result["pairs"], [])
        self.assertEqual(result["dataset_metadata"]["description"], "demo dataset")
        self.assertEqual(result["dataset_metadata"]["publication"], "a paper")
        self.assertEqual(result["dataset_metadata"]["label_classes"]["mitochondria"], 1)

    def test_manifest_ignored_for_a_different_label_set(self):
        # The manifest documents labelsTr; the user picked labelsTr-instance.
        # Scan no longer auto-pairs either way — files are listed for manual pick.
        self._write_manifest(mask_dir="labelsTr")
        result = scan_data_sources(self.images, self.instance)
        self.assertEqual(result["pairing_source"], "manual")
        self.assertEqual(result["pairs"], [])
        self.assertEqual(len(result["mask_files"]), 2)

    def test_stale_manifest_falls_back_to_filenames(self):
        manifest = {
            "training": [{"image": "./imagesTr/gone_0000.tiff", "label": "./labelsTr/gone.tiff"}]
        }
        with open(os.path.join(self.root, "dataset.json"), "w") as fh:
            json.dump(manifest, fh)
        result = scan_data_sources(self.images, self.labels)
        self.assertEqual(result["pairing_source"], "manual")
        self.assertEqual(result["pairs"], [])
        self.assertEqual(len(result["unmatched_images"]), 2)

    def test_suggestions_offer_sibling_label_sets(self):
        result = scan_data_sources(self.images, self.labels)
        names = {s["name"] for s in result["suggestions"]["masks"]}
        self.assertEqual(names, {"labelsTr", "labelsTr-instance"})

    def test_region_only_scan_keeps_editable_role_empty(self):
        regions = os.path.join(self.root, "regionsTr")
        os.makedirs(regions)
        for case in ("case_00", "case_01"):
            self._touch(regions, f"{case}_region_mask.tiff")

        result = scan_data_sources(self.images, "", regions)

        self.assertEqual(result["mask_directory"], "")
        self.assertEqual(result["mask_files"], [])
        self.assertEqual(result["region_by_image"], {})
        self.assertEqual(len(result["region_mask_files"]), 2)
        self.assertEqual(len(result["unmatched_region_masks"]), 2)
        self.assertEqual(result["pairs"], [])
    def test_same_folder_region_and_labels_offer_file_dropdowns(self):
        """Same image/region/label directory lists every file with no filters."""
        mixed = os.path.join(self.root, "mixed")
        os.makedirs(mixed)
        self._touch(mixed, "vol_a_image.tiff")
        self._touch(mixed, "vol_a_mask.tiff")
        self._touch(mixed, "vol_a_region_mask.tiff")

        result = scan_data_sources(mixed, mixed, mixed)
        names = {
            "vol_a_image.tiff",
            "vol_a_mask.tiff",
            "vol_a_region_mask.tiff",
        }

        self.assertTrue(result["region_mask_directory"].endswith("mixed"))
        self.assertEqual({f["name"] for f in result["region_mask_files"]}, names)
        self.assertEqual({f["name"] for f in result["mask_files"]}, names)
        self.assertEqual(set(result["unmatched_images"]), names)
        self.assertEqual(result["pairs"], [])
        self.assertEqual(result["region_by_image"], {})
    def test_register_explicit_region_only_pairs_without_editable_labels(self):
        regions = os.path.join(self.root, "regionsTr")
        os.makedirs(regions)
        self._touch(regions, "case_00_region_mask.tiff")

        _project, volumes = register_dataset(
            created_by=None,
            dataset="Region only",
            volume="v",
            image_directory=self.images,
            region_mask_directory=regions,
            pairs=[{
                "image": "case_00_0000.tiff",
                "region_mask": "case_00_region_mask.tiff",
            }],
            label_type=LabelType.NONE,
        )

        self.assertEqual(len(volumes), 1)
        self.assertTrue(volumes[0].region_mask_path.endswith("case_00_region_mask.tiff"))
        self.assertEqual(volumes[0].label_path, "")
        self.assertEqual(volumes[0].label_type, LabelType.NONE)

    def test_image_directory_required(self):
        with self.assertRaises(DataRegistrationError):
            register_dataset(created_by=None, dataset="D", volume="v")

    def test_mask_directory_traversal_rejected(self):
        with self.assertRaises(DataRegistrationError):
            register_dataset(
                created_by=None,
                dataset="D",
                volume="v",
                image_directory=self.images,
                mask_directory=self.labels,
                # Only basenames are honoured, so this cannot escape labelsTr.
                pairs=[{"image": "case_00_0000.tiff", "mask": "../imagesTr/case_00_0000.tiff"}],
            )


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class LabelTypeCouplingTests(TestCase):
    """The mask ↔ label_type invariant, on every write surface.

    `label_type` describes the mask, so the two must stay coherent: a volume
    with a mask carries `partial` or `prediction`, one without carries `none`.
    `proofread` is retired for new writes but survives on legacy rows, which
    must stay editable. These pin the rules that the registration/edit paths
    disagreed about.
    """

    def setUp(self):
        self.project = create_project(title="Coupling", created_by=None)

    def _volume(self, *, label_path="", label_type=None):
        return register_volume(
            project=self.project,
            name="v",
            image_path="img.tiff",
            label_path=label_path,
            label_type=label_type,
            autodetect_shape=False,
        )

    # --- registration -----------------------------------------------------

    def test_mask_without_a_stated_type_defaults_to_prediction(self):
        v = self._volume(label_path="mask.tiff")
        self.assertEqual(v.label_type, LabelType.PREDICTION)

    def test_no_mask_without_a_stated_type_is_none(self):
        self.assertEqual(self._volume().label_type, LabelType.NONE)

    def test_explicit_none_with_a_mask_is_rejected(self):
        with self.assertRaises(DataRegistrationError):
            self._volume(label_path="mask.tiff", label_type=LabelType.NONE)

    def test_proofread_is_rejected_on_registration(self):
        with self.assertRaises(DataRegistrationError):
            self._volume(label_path="mask.tiff", label_type=LabelType.PROOFREAD)

    def test_a_type_without_a_mask_normalises_to_none(self):
        v = self._volume(label_type=LabelType.PREDICTION)
        self.assertEqual(v.label_type, LabelType.NONE)

    # --- editing ----------------------------------------------------------

    def test_attaching_a_mask_alone_to_an_untyped_volume_asks_rather_than_guesses(self):
        """The reported bug: this used to raise an unactionable error.

        It must still not succeed silently — inventing `prediction` here would
        relabel the user's data — but the message now says what to send.
        """
        v = self._volume()
        with self.assertRaises(DataRegistrationError) as ctx:
            update_volume_metadata(v, label_path="mask.tiff")
        self.assertIn("label_type", str(ctx.exception))
        self.assertIn("partial", str(ctx.exception))

    def test_attaching_a_mask_with_a_stated_type_succeeds(self):
        v = self._volume()
        edited = update_volume_metadata(
            v, label_path="mask.tiff", label_type=LabelType.PARTIAL
        )
        self.assertEqual(edited.label_type, LabelType.PARTIAL)
        self.assertEqual(edited.label_path, "mask.tiff")

    def test_repointing_a_mask_keeps_the_existing_type(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        edited = update_volume_metadata(v, label_path="b.tiff")
        self.assertEqual(edited.label_type, LabelType.PARTIAL)

    def test_removing_the_mask_clears_the_type(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PREDICTION)
        edited = update_volume_metadata(v, label_path="")
        self.assertEqual(edited.label_type, LabelType.NONE)

    def test_legacy_proofread_rows_stay_editable(self):
        """Regression: validating the *stored* type made legacy rows uneditable."""
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        # Simulate a row written before `proofread` was retired.
        Volume.objects.filter(pk=v.pk).update(label_type=LabelType.PROOFREAD)
        v.refresh_from_db()

        edited = update_volume_metadata(v, label_path="b.tiff")
        self.assertEqual(edited.label_path, "b.tiff")
        self.assertEqual(edited.label_type, LabelType.PROOFREAD)

    def test_legacy_proofread_row_can_be_renamed(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        Volume.objects.filter(pk=v.pk).update(label_type=LabelType.PROOFREAD)
        v.refresh_from_db()

        edited = update_volume_metadata(v, name="renamed")
        self.assertEqual(edited.name, "renamed")
        self.assertEqual(edited.label_type, LabelType.PROOFREAD)

    def test_a_caller_may_still_not_send_proofread_on_an_edit(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        with self.assertRaises(DataRegistrationError):
            update_volume_metadata(v, label_type=LabelType.PROOFREAD)

    def test_edit_cannot_set_none_while_a_mask_remains(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        with self.assertRaises(DataRegistrationError):
            update_volume_metadata(v, label_type=LabelType.NONE)

    def test_edits_untouching_label_fields_skip_validation(self):
        v = self._volume(label_path="a.tiff", label_type=LabelType.PARTIAL)
        edited = update_volume_metadata(v, name="just-a-rename")
        self.assertEqual(edited.name, "just-a-rename")
        self.assertEqual(edited.label_type, LabelType.PARTIAL)
