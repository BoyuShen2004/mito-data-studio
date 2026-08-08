from django.test import TestCase

from projects.services import (
    DeleteBlocked,
    create_project,
    delete_dataset,
    delete_project,
    get_or_create_dataset,
    update_dataset,
)


class DatasetHierarchyTests(TestCase):
    """project → dataset → volume, and the guards around deleting them."""

    def setUp(self):
        self.project = create_project(title="Study")

    def _dataset(self, name="CellMap"):
        return get_or_create_dataset(project=self.project, name=name)

    def _volume(self, dataset, name="case_00"):
        from volumes.services import register_volume

        return register_volume(
            dataset=dataset, name=name, image_path=f"{name}.tif", autodetect_shape=False
        )

    def test_project_holds_many_datasets(self):
        a = self._dataset("CellMap")
        b = self._dataset("MitoEM")
        self.assertEqual(self.project.datasets.count(), 2)
        self.assertNotEqual(a.id, b.id)

    def test_dataset_holds_many_volumes(self):
        ds = self._dataset()
        self._volume(ds, "case_00")
        self._volume(ds, "case_01")
        self.assertEqual(ds.volumes.count(), 2)

    def test_volume_project_is_taken_from_its_dataset(self):
        ds = self._dataset()
        volume = self._volume(ds)
        # The denormalised FK must agree with the dataset's project.
        self.assertEqual(volume.project_id, self.project.id)
        self.assertEqual(volume.dataset_id, ds.id)

    def test_same_name_reuses_the_dataset(self):
        first = self._dataset("CellMap")
        again = get_or_create_dataset(
            project=self.project, name="CellMap", metadata={"organism": "mouse"}
        )
        self.assertEqual(first.id, again.id)
        self.assertEqual(self.project.datasets.count(), 1)
        # Metadata merges rather than replacing.
        self.assertEqual(again.metadata["organism"], "mouse")

    def test_same_name_in_another_project_is_a_different_dataset(self):
        other = create_project(title="Other")
        a = self._dataset("CellMap")
        b = get_or_create_dataset(project=other, name="CellMap")
        self.assertNotEqual(a.id, b.id)

    def test_update_dataset_renames_and_merges_metadata(self):
        ds = get_or_create_dataset(
            project=self.project, name="Old", metadata={"organism": "mouse"}
        )
        update_dataset(ds, name="New", metadata={"tissue": "kidney"})
        ds.refresh_from_db()
        self.assertEqual(ds.name, "New")
        self.assertEqual(ds.metadata, {"organism": "mouse", "tissue": "kidney"})

    def test_moving_a_dataset_moves_its_volumes(self):
        ds = self._dataset()
        volume = self._volume(ds)
        target = create_project(title="Target")

        update_dataset(ds, project=target)

        volume.refresh_from_db()
        self.assertEqual(volume.project_id, target.id)

    def test_delete_dataset_without_work_succeeds(self):
        ds = self._dataset()
        self._volume(ds)
        counts = delete_dataset(ds)
        self.assertEqual(counts["volumes"], 1)
        self.assertEqual(self.project.datasets.count(), 0)

    def test_delete_is_blocked_while_tasks_exist(self):
        from annotation.models import AnnotationTask

        ds = self._dataset()
        volume = self._volume(ds)
        AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )
        with self.assertRaises(DeleteBlocked) as ctx:
            delete_dataset(ds)
        self.assertEqual(ctx.exception.counts["tasks"], 1)
        # Nothing was removed.
        self.assertEqual(self.project.datasets.count(), 1)

    def test_forced_delete_removes_the_work(self):
        from annotation.models import AnnotationTask

        ds = self._dataset()
        volume = self._volume(ds)
        AnnotationTask.objects.create(
            project=self.project, volume=volume, z_start=0, z_end=8,
            y_start=0, y_end=8, x_start=0, x_end=8, task_type="manual_annotation",
        )
        counts = delete_dataset(ds, force=True)
        self.assertEqual(counts["tasks"], 1)
        self.assertEqual(AnnotationTask.objects.count(), 0)

    def test_deleting_a_project_reports_its_datasets(self):
        self._dataset("A")
        self._dataset("B")
        counts = delete_project(self.project)
        self.assertEqual(counts["datasets"], 2)
