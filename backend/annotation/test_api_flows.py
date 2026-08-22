"""End-to-end API tests for the role-based data-registration + assignment flow."""

import os
import tempfile

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import AnnotatorProfile, Institution, Team, TeamMembership, UserProfile
from accounts.teams import grant_project_team
from annotation.models import AnnotationTask, AssignmentWithdrawal
from annotation.services import create_whole_volume_task
from core.choices import UserRole
from projects.models import Project

_TMP_ROOT = tempfile.mkdtemp(prefix="mito_api_test_")


@override_settings(MITO_DATA_ROOT=_TMP_ROOT)
class DataRegistrationFlowTests(APITestCase):
    def setUp(self):
        # An HPC directory holding supported + unsupported files.
        self.data_dir = tempfile.mkdtemp(dir=_TMP_ROOT)
        for name in ("crop_a.tif", "crop_b.nii.gz", "ignore.txt"):
            with open(os.path.join(self.data_dir, name), "wb") as fh:
                fh.write(b"II*\x00")

        # A requester (registered through the public endpoint).
        res = self.client.post(
            reverse("api-register"),
            {"username": "req", "password": "pw-abc-12345", "role": "requester"},
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.req_token = res.data["token"]

        # A manager (superuser) and an annotator.
        self.manager = User.objects.create_superuser("mgr", password="x")
        self.annotator = User.objects.create_user("ann", password="x")
        UserProfile.objects.update_or_create(
            user=self.annotator, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(user=self.annotator)

    def _new_project(self, title="Test project") -> int:
        """Create a project. Data is registered *into* one, never the reverse."""
        res = self.client.post(reverse("project-list"), {"title": title}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return res.data["id"]

    def _auth(self, token=None, user=None):
        # credentials() only clears headers; a previous force_authenticate would
        # otherwise persist and silently keep the old user signed in.
        self.client.force_authenticate(user=None)
        self.client.credentials()
        if token:
            self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        elif user:
            self.client.force_authenticate(user=user)

    def _working_team(self, project):
        organization, _ = Institution.objects.get_or_create(name="API flow lab")
        team = Team.objects.create(
            organization=organization, name=f"Project {project.id} team"
        )
        TeamMembership.objects.create(team=team, user=self.annotator)
        grant_project_team(project, team)
        return team

    def test_registers_three_layers_and_region_stream_is_immutable(self):
        import hashlib
        import numpy as np
        import tifffile

        root = os.path.join(self.data_dir, "three-layers")
        images = os.path.join(root, "images")
        regions = os.path.join(root, "regions")
        labels = os.path.join(root, "labels")
        for directory in (images, regions, labels):
            os.makedirs(directory, exist_ok=True)
        tifffile.imwrite(
            os.path.join(images, "case_0000.tif"),
            np.zeros((2, 6, 7), dtype=np.uint8),
        )
        region_path = os.path.join(regions, "case.tif")
        region = np.zeros((2, 6, 7), dtype=np.uint8)
        region[:, 1:5, 2:6] = 1
        tifffile.imwrite(region_path, region)
        tifffile.imwrite(
            os.path.join(labels, "case.tif"),
            np.zeros((2, 6, 7), dtype=np.uint16),
        )
        before = hashlib.sha256(open(region_path, "rb").read()).hexdigest()

        self._auth(token=self.req_token)
        scan = self.client.post(
            reverse("api-hpc-scan"),
            {
                "image_directory": images,
                "region_mask_directory": regions,
                "mask_directory": labels,
            },
            format="json",
        )
        self.assertEqual(scan.status_code, 200, scan.data)
        self.assertEqual(
            scan.data["region_by_image"]["case_0000.tif"], "case.tif"
        )
        response = self.client.post(
            reverse("api-register-data"),
            {
                "project": self._new_project("Three layers"),
                "dataset": "ThreeLayerData",
                "volume": "v",
                "image_directory": images,
                "region_mask_directory": regions,
                "mask_directory": labels,
                "label_type": "partial",
                "pairs": [
                    {
                        "image": "case_0000.tif",
                        "region_mask": "case.tif",
                        "mask": "case.tif",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        volume = response.data["volumes"][0]
        self.assertTrue(volume["has_region_mask"])
        overlay = self.client.get(
            reverse("api-volume-region-mask-slice", args=[volume["id"]]),
            {"axis": "z", "index": 0},
        )
        self.assertEqual(overlay.status_code, 200)
        self.assertEqual(overlay["Content-Type"], "image/png")
        after = hashlib.sha256(open(region_path, "rb").read()).hexdigest()
        self.assertEqual(after, before)

    def test_requester_registers_data_and_sees_own_project(self):
        self._auth(token=self.req_token)
        project_id = self._new_project("Cortex study")

        # Scan the HPC directory. `hpc_directory` is the older single-directory
        # spelling of `image_directory`, and is still accepted.
        scan = self.client.post(
            reverse("api-hpc-scan"), {"hpc_directory": self.data_dir}
        )
        self.assertEqual(scan.status_code, 200, scan.data)
        names = {f["name"] for f in scan.data["image_files"]}
        self.assertEqual(names, {"crop_a.tif", "crop_b.nii.gz"})

        # Register the dataset with metadata.
        reg = self.client.post(
            reverse("api-register-data"),
            {
                "project": project_id,
                "dataset": "CortexA",
                "volume": "big_vol",
                "hpc_directory": self.data_dir,
                "metadata": {"organism": "mouse"},
                "files": [
                    {"name": "crop_a.tif", "chunk_id": "c1"},
                    {"name": "crop_b.nii.gz"},
                ],
            },
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        self.assertEqual(len(reg.data["volumes"]), 2)
        self.assertEqual(reg.data["project"]["id"], project_id)
        self.assertEqual(reg.data["project"]["dataset"], "CortexA")
        # Metadata describes the data, so it belongs to the dataset rather than
        # the project — a project may hold several with differing values.
        datasets = reg.data["project"]["datasets"]
        self.assertEqual([d["name"] for d in datasets], ["CortexA"])
        self.assertEqual(datasets[0]["metadata"]["organism"], "mouse")

        # Requester sees the project in their own list.
        lst = self.client.get(reverse("project-list"))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual([p["id"] for p in lst.data], [project_id])

        # And the project summary (progress) is viewable by the requester.
        summ = self.client.get(reverse("project-summary", args=[project_id]))
        self.assertEqual(summ.status_code, 200)
        self.assertIn("progress", summ.data)

    def test_register_image_mask_pairs_via_endpoint(self):
        # A folder with an image+mask pair plus an unrelated volume.
        import os

        pair_dir = os.path.join(self.data_dir, "pairs")
        os.makedirs(pair_dir, exist_ok=True)
        for name in ("s1_image.tif", "s1_mask.tif", "s2_image.tif"):
            with open(os.path.join(pair_dir, name), "wb") as fh:
                fh.write(b"II*\x00")

        self._auth(token=self.req_token)

        # Scan surfaces the auto-detected pair.
        scan = self.client.post(reverse("api-hpc-scan"), {"hpc_directory": pair_dir})
        self.assertEqual(scan.status_code, 200, scan.data)
        self.assertEqual(len(scan.data["pairs"]), 1)
        self.assertEqual(scan.data["pairs"][0]["image"], "s1_image.tif")
        self.assertEqual(scan.data["pairs"][0]["mask"], "s1_mask.tif")

        # Register just the explicit pair out of the folder.
        reg = self.client.post(
            reverse("api-register-data"),
            {
                "project": self._new_project("Paired work"),
                "dataset": "Paired",
                "volume": "v",
                "hpc_directory": pair_dir,
                "label_type": "partial",
                "pairs": [{"image": "s1_image.tif", "mask": "s1_mask.tif"}],
            },
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        self.assertEqual(len(reg.data["volumes"]), 1)
        vol = reg.data["volumes"][0]
        self.assertTrue(vol["label_location"].endswith("s1_mask.tif"))
        self.assertEqual(vol["label_type"], "partial")

    def test_register_pairs_from_two_directories_via_endpoint(self):
        """The nnU-Net shape: images and masks in sibling folders."""
        import os

        root = os.path.join(self.data_dir, "Dataset001")
        images = os.path.join(root, "imagesTr")
        labels = os.path.join(root, "labelsTr")
        for directory in (images, labels):
            os.makedirs(directory, exist_ok=True)
        for case in ("case_00", "case_01"):
            with open(os.path.join(images, f"{case}_0000.tif"), "wb") as fh:
                fh.write(b"II*\x00")
            with open(os.path.join(labels, f"{case}.tif"), "wb") as fh:
                fh.write(b"II*\x00")

        self._auth(token=self.req_token)

        scan = self.client.post(
            reverse("api-hpc-scan"),
            {"image_directory": images, "mask_directory": labels},
        )
        self.assertEqual(scan.status_code, 200, scan.data)
        self.assertEqual(len(scan.data["pairs"]), 2)
        self.assertEqual(scan.data["unmatched_images"], [])
        self.assertEqual(scan.data["split"], "train")
        # labelsTr is offered as a quick-pick for the label set.
        self.assertIn("labelsTr", [s["name"] for s in scan.data["suggestions"]["masks"]])

        reg = self.client.post(
            reverse("api-register-data"),
            {
                "project": self._new_project("CrossDir work"),
                "dataset": "CrossDir",
                "volume": "v",
                "image_directory": images,
                "mask_directory": labels,
                "pairs": [{"image": p["image"], "mask": p["mask"]} for p in scan.data["pairs"]],
            },
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        self.assertEqual(len(reg.data["volumes"]), 2)
        for vol in reg.data["volumes"]:
            self.assertIn("imagesTr", vol["image_location"])
            self.assertIn("labelsTr", vol["label_location"])
        self.assertEqual(
            sorted(v["name"] for v in reg.data["volumes"]), ["case_00", "case_01"]
        )

    def test_register_data_requires_an_image_directory(self):
        self._auth(token=self.req_token)
        reg = self.client.post(
            reverse("api-register-data"),
            {"project": self._new_project(), "dataset": "D", "volume": "v"},
            format="json",
        )
        self.assertEqual(reg.status_code, 400, reg.data)

    def test_register_data_does_not_require_a_volume_name(self):
        """The Register Data UI names each volume in the scanned list, so it
        sends no top-level volume name at all."""
        self._auth(token=self.req_token)
        reg = self.client.post(
            reverse("api-register-data"),
            {
                "project": self._new_project("No volume name"),
                "dataset": "D",
                "hpc_directory": self.data_dir,
                "files": [{"name": "crop_a.tif", "chunk_id": "crop-1"}],
            },
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        self.assertEqual(reg.data["volumes"][0]["name"], "crop-1")

    def test_register_data_requires_an_existing_project(self):
        """Work starts with a project; registering never conjures one."""
        self._auth(token=self.req_token)
        reg = self.client.post(
            reverse("api-register-data"),
            {"dataset": "Orphan", "volume": "v", "image_directory": self.data_dir},
            format="json",
        )
        self.assertEqual(reg.status_code, 400, reg.data)
        self.assertIn("project", reg.data)
        # Nothing was created as a side effect of the attempt.
        self.assertEqual(Project.objects.count(), 0)

    def test_cannot_register_into_someone_elses_project(self):
        self._auth(user=self.manager)
        foreign = self._new_project("Manager's project")

        self._auth(token=self.req_token)
        reg = self.client.post(
            reverse("api-register-data"),
            {"project": foreign, "dataset": "Sneaky", "volume": "v",
             "image_directory": self.data_dir},
            format="json",
        )
        self.assertEqual(reg.status_code, 403, reg.data)

    def test_review_gate_then_auto_assign_distributes_volumes(self):
        import os

        import numpy as np
        import tifffile

        # Real TIFFs so shape auto-detection works and tasks can be created.
        vol_dir = os.path.join(self.data_dir, "vols")
        os.makedirs(vol_dir, exist_ok=True)
        for i in range(2):
            tifffile.imwrite(
                os.path.join(vol_dir, f"v{i}.tif"),
                np.zeros((8, 16, 16), dtype=np.uint8),
            )

        # Requester starts a project, then registers data into it. The project
        # is theirs, so it stays pending manager review.
        self._auth(token=self.req_token)
        project_id = self._new_project("Gated study")
        reg = self.client.post(
            reverse("api-register-data"),
            {"project": project_id, "dataset": "Gated", "volume": "v",
             "hpc_directory": vol_dir},
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        self.assertEqual(reg.data["project"]["id"], project_id)
        self.assertFalse(reg.data["project"]["manager_reviewed"])

        # Auto-assign is blocked until the manager reviews.
        self._auth(user=self.manager)
        blocked = self.client.post(
            reverse("api-assign-tasks", args=[project_id]), {}, format="json"
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertFalse(blocked.data["reviewed"])

        # Manager approves the dataset.
        rev = self.client.post(
            reverse("project-review", args=[project_id]), {}, format="json"
        )
        self.assertEqual(rev.status_code, 200, rev.data)
        self.assertTrue(rev.data["manager_reviewed"])
        self._working_team(Project.objects.get(pk=project_id))

        # Now auto-assign creates one task per volume and assigns them.
        res = self.client.post(
            reverse("api-assign-tasks", args=[project_id]), {}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["created_tasks"], 2)
        self.assertEqual(res.data["assigned"], 2)

    def test_register_data_rejects_unsupported_file(self):
        self._auth(token=self.req_token)
        res = self.client.post(
            reverse("api-register-data"),
            {
                "dataset": "d",
                "volume": "v",
                "hpc_directory": self.data_dir,
                "files": [{"name": "ignore.txt"}],
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_annotator_cannot_register_data(self):
        self._auth(user=self.annotator)
        res = self.client.post(
            reverse("api-register-data"),
            {"dataset": "d", "volume": "v", "hpc_directory": self.data_dir},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_assigned_annotator_reads_shared_project_volume_shape_without_paths(self):
        from volumes.services import register_volume

        project = Project.objects.create(
            title="Shared shape", dataset="legacy", created_by=self.manager,
            manager_reviewed=True,
        )
        volume = register_volume(
            project=project, name="crop", image_path="secret/crop.tif",
            autodetect_shape=False,
        )
        volume.shape_z, volume.shape_y, volume.shape_x = 4, 8, 10
        volume.voxel_size_z, volume.voxel_size_y, volume.voxel_size_x = .04, .008, .008
        volume.save()
        task = create_whole_volume_task(volume)
        task.assigned_to = self.annotator
        task.save(update_fields=["assigned_to"])

        self._auth(user=self.annotator)
        summary = self.client.get(reverse("project-summary", args=[project.id]))
        rows = self.client.get(reverse("api-project-volumes", args=[project.id]))
        detail = self.client.get(reverse("api-volume-detail", args=[volume.id]))

        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(rows.status_code, 200, rows.data)
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(
            (detail.data["shape_z"], detail.data["shape_y"], detail.data["shape_x"]),
            (4, 8, 10),
        )
        self.assertEqual(detail.data["file_format"], "tiff")
        self.assertNotIn("image_location", detail.data)
        self.assertNotIn("image_path", detail.data)

        hidden = Project.objects.create(title="Other", created_by=self.manager)
        self.assertEqual(
            self.client.get(reverse("project-summary", args=[hidden.id])).status_code,
            404,
        )

    def test_manager_manual_assignment(self):
        # Set up a reviewed project + a task owned by nobody.
        project = Project.objects.create(
            title="P", dataset="P", created_by=self.manager, manager_reviewed=True
        )
        from volumes.services import register_volume

        volume = register_volume(
            project=project, name="v", image_path="v.tiff", autodetect_shape=False
        )
        volume.shape_x, volume.shape_y, volume.shape_z = 8, 8, 16
        volume.save()
        task = create_whole_volume_task(volume)
        self.assertIsNone(task.assigned_to_id)
        team = self._working_team(project)

        # Manager assigns it to the annotator.
        self._auth(user=self.manager)
        res = self.client.post(
            reverse("api-task-assign", args=[task.id]),
            {"annotator_id": self.annotator.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to_id, self.annotator.id)
        self.assertEqual(task.status, "assigned")

        # A direct transfer keeps the canonical task/working volume and gives
        # the former annotator an immutable Done-history row.
        successor = User.objects.create_user("successor", password="x")
        UserProfile.objects.update_or_create(
            user=successor, defaults={"role": UserRole.ANNOTATOR}
        )
        AnnotatorProfile.objects.create(user=successor)
        TeamMembership.objects.create(team=team, user=successor)
        res = self.client.post(
            reverse("api-task-assign", args=[task.id]),
            {"annotator_id": successor.id},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to_id, successor.id)
        transfer = AssignmentWithdrawal.objects.get(
            task=task, annotator=self.annotator
        )
        self.assertEqual(transfer.outcome, "transferred")
        self.assertEqual(transfer.transferred_to_id, successor.id)

        self._auth(user=self.annotator)
        history = self.client.get(reverse("api-my-completed-tasks"))
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data[0]["status"], "transferred")
        self.assertTrue(history.data[0]["assignment_transferred"])
        self.assertEqual(history.data[0]["transferred_to_username"], "successor")

        # Reassigning to null unassigns the same task (no duplicate).
        self._auth(user=self.manager)
        res = self.client.post(
            reverse("api-task-assign", args=[task.id]),
            {"annotator_id": None},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        task.refresh_from_db()
        self.assertIsNone(task.assigned_to_id)
        self.assertEqual(AnnotationTask.objects.count(), 1)

    def test_annotator_cannot_manually_assign(self):
        project = Project.objects.create(title="P2", created_by=self.manager)
        from volumes.services import register_volume

        volume = register_volume(
            project=project, name="v2", image_path="v2.tiff", autodetect_shape=False
        )
        volume.shape_x, volume.shape_y, volume.shape_z = 8, 8, 16
        volume.save()
        task = create_whole_volume_task(volume)

        self._auth(user=self.annotator)
        res = self.client.post(
            reverse("api-task-assign", args=[task.id]),
            {"annotator_id": self.annotator.id},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_project_holds_multiple_datasets_via_endpoint(self):
        """Register two datasets into one project, then edit and delete them."""
        import os

        self._auth(token=self.req_token)

        def make(root, case):
            images = os.path.join(self.data_dir, root, "imagesTr")
            labels = os.path.join(self.data_dir, root, "labelsTr")
            for d in (images, labels):
                os.makedirs(d, exist_ok=True)
            with open(os.path.join(images, f"{case}_0000.tif"), "wb") as fh:
                fh.write(b"II*\x00")
            with open(os.path.join(labels, f"{case}.tif"), "wb") as fh:
                fh.write(b"II*\x00")
            return images, labels

        img_a, lbl_a = make("setA", "a_00")
        img_b, lbl_b = make("setB", "b_00")

        project_id = self._new_project("Two-dataset study")
        first = self.client.post(
            reverse("api-register-data"),
            {"project": project_id, "dataset": "SetA", "volume": "v", "image_directory": img_a,
             "mask_directory": lbl_a, "metadata": {"organism": "mouse"}},
            format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["project"]["id"], project_id)

        # A second dataset registered into the *same* project.
        second = self.client.post(
            reverse("api-register-data"),
            {"dataset": "SetB", "volume": "v", "project": project_id,
             "image_directory": img_b, "mask_directory": lbl_b,
             "metadata": {"organism": "rat"}},
            format="json",
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(second.data["project"]["id"], project_id)

        detail = self.client.get(reverse("project-detail", args=[project_id]))
        self.assertEqual(detail.data["dataset_count"], 2)
        names = sorted(d["name"] for d in detail.data["datasets"])
        self.assertEqual(names, ["SetA", "SetB"])
        # Metadata is per-dataset, so the two organisms coexist.
        organisms = sorted(d["metadata"]["organism"] for d in detail.data["datasets"])
        self.assertEqual(organisms, ["mouse", "rat"])

        # Datasets are listable and editable.
        listed = self.client.get(reverse("dataset-list") + f"?project={project_id}")
        self.assertEqual(len(listed.data), 2)
        ds_id = listed.data[0]["id"]
        renamed = self.client.patch(
            reverse("dataset-detail", args=[ds_id]), {"name": "Renamed"}, format="json"
        )
        self.assertEqual(renamed.status_code, 200, renamed.data)
        self.assertEqual(renamed.data["name"], "Renamed")

        # And deletable while they carry no annotation work.
        gone = self.client.delete(reverse("dataset-detail", args=[ds_id]))
        self.assertEqual(gone.status_code, 200, gone.data)
        self.assertEqual(
            self.client.get(reverse("project-detail", args=[project_id])).data["dataset_count"], 1
        )

    def test_delete_is_blocked_until_forced(self):
        """A dataset with tasks refuses to delete without an explicit force."""
        import os

        self._auth(token=self.req_token)
        images = os.path.join(self.data_dir, "guard", "imagesTr")
        labels = os.path.join(self.data_dir, "guard", "labelsTr")
        for d in (images, labels):
            os.makedirs(d, exist_ok=True)
        with open(os.path.join(images, "g_00_0000.tif"), "wb") as fh:
            fh.write(b"II*\x00")
        with open(os.path.join(labels, "g_00.tif"), "wb") as fh:
            fh.write(b"II*\x00")

        reg = self.client.post(
            reverse("api-register-data"),
            {"project": self._new_project("Guarded work"), "dataset": "Guarded",
             "volume": "v", "image_directory": images, "mask_directory": labels},
            format="json",
        )
        self.assertEqual(reg.status_code, 201, reg.data)
        volume_id = reg.data["volumes"][0]["id"]
        project_id = reg.data["project"]["id"]

        from annotation.models import AnnotationTask
        from volumes.models import Volume

        volume = Volume.objects.get(pk=volume_id)
        AnnotationTask.objects.create(
            project_id=project_id, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )
        dataset_id = volume.dataset_id

        # What is in the way is reported before anything is destroyed.
        dep = self.client.get(reverse("dataset-dependents", args=[dataset_id]))
        self.assertEqual(dep.data["tasks"], 1)

        blocked = self.client.delete(reverse("dataset-detail", args=[dataset_id]))
        self.assertEqual(blocked.status_code, 409, blocked.data)
        self.assertEqual(blocked.data["counts"]["tasks"], 1)
        self.assertTrue(Volume.objects.filter(pk=volume_id).exists())

        forced = self.client.delete(
            reverse("dataset-detail", args=[dataset_id]) + "?force=true"
        )
        self.assertEqual(forced.status_code, 200, forced.data)
        self.assertFalse(Volume.objects.filter(pk=volume_id).exists())
        self.assertEqual(AnnotationTask.objects.count(), 0)

    def test_volume_edit_fixes_a_wrong_pairing(self):
        import os

        self._auth(token=self.req_token)
        images = os.path.join(self.data_dir, "fix", "imagesTr")
        labels = os.path.join(self.data_dir, "fix", "labelsTr")
        for d in (images, labels):
            os.makedirs(d, exist_ok=True)
        for name in ("f_00_0000.tif",):
            with open(os.path.join(images, name), "wb") as fh:
                fh.write(b"II*\x00")
        for name in ("f_00.tif", "better.tif"):
            with open(os.path.join(labels, name), "wb") as fh:
                fh.write(b"II*\x00")

        reg = self.client.post(
            reverse("api-register-data"),
            {"project": self._new_project("Fixable work"), "dataset": "Fixable",
             "volume": "v", "image_directory": images, "mask_directory": labels},
            format="json",
        )
        volume_id = reg.data["volumes"][0]["id"]

        edited = self.client.patch(
            reverse("api-volume-detail", args=[volume_id]),
            {"name": "renamed", "label_path": "fix/labelsTr/better.tif",
             "label_type": "partial"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200, edited.data)
        self.assertEqual(edited.data["name"], "renamed")
        self.assertTrue(edited.data["label_location"].endswith("better.tif"))
        self.assertEqual(edited.data["label_type"], "partial")
