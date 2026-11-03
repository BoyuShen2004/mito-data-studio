"""Isolated tests; no Django, source datasets, credentials, or live services."""
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('skill_installer', ROOT / 'scripts/skills/install.py')
budget = load('manifest_budget', ROOT / 'skills/mito-admin-preflight/scripts/manifest_capacity.py')


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mito-skills-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, rows):
        path = self.root / 'manifest.csv'
        with path.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_preview_is_read_only(self):
        destination = self.root / 'installed'
        report = installer.install_to(ROOT / 'skills', {'test': destination})
        self.assertEqual(len(report['skills']), 4)
        self.assertFalse(report['applied'])
        self.assertFalse(destination.exists())

    def test_install_and_idempotent_replay(self):
        destination = self.root / 'installed'
        report = installer.install_to(ROOT / 'skills', {'test': destination}, True)
        self.assertTrue(report['verified'])
        replay = installer.install_to(ROOT / 'skills', {'test': destination}, True)
        self.assertTrue(all(row['action'] == 'keep-identical' for row in replay['skills']))

    def test_user_edit_blocks_entire_install(self):
        destination = self.root / 'installed'
        target = destination / 'mito-admin-validate'
        target.mkdir(parents=True)
        edited = target / 'SKILL.md'
        edited.write_text('Existing user content')
        with self.assertRaises(ValueError):
            installer.install_to(ROOT / 'skills', {'test': destination}, True)
        self.assertEqual(edited.read_text(), 'Existing user content')
        self.assertFalse((destination / 'mito-admin-archive').exists())

    def test_skill_symlink_is_not_replaced(self):
        destination = self.root / 'installed'
        destination.mkdir()
        external = self.root / 'external'
        external.mkdir()
        (destination / 'mito-admin-archive').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install_to(ROOT / 'skills', {'test': destination}, True)
        self.assertTrue((destination / 'mito-admin-archive').is_symlink())

    def test_capacity_includes_working_labels_reserve_and_commitments(self):
        row = dict(name='example', shape_zyx='256x2048x2048', triplet_bytes=2**30,
                   working_label_uint16_bytes=2 * 2**30, validation_errors='')
        manifest = self.manifest([row])
        class FS:
            f_bavail = 24 * 2**30
            f_frsize = 1
        with patch.object(budget.os, 'statvfs', return_value=FS()):
            sufficient = budget.assess(manifest, self.root)
            committed = budget.assess(manifest, self.root, committed_gib=2)
        self.assertTrue(sufficient['capacity_with_working_labels'])
        self.assertFalse(committed['capacity_with_working_labels'])
        self.assertEqual(committed['shortfall_GiB'], 1)
        self.assertFalse(committed['write'])

    def test_manifest_rejects_duplicates_and_wrong_label_bytes(self):
        row = dict(name='example', shape_zyx='2x3x4', triplet_bytes=100,
                   working_label_uint16_bytes=48, validation_errors='')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            budget.assess(self.manifest([row, row]), self.root)
        row['working_label_uint16_bytes'] = 24
        with self.assertRaisesRegex(ValueError, 'working-label'):
            budget.assess(self.manifest([row]), self.root)

    def test_empty_or_flagged_manifest_does_not_pass(self):
        row = dict(name='example', shape_zyx='2x3x4', triplet_bytes=100,
                   working_label_uint16_bytes=48, validation_errors='missing source')
        with self.assertRaisesRegex(ValueError, 'validation errors'):
            budget.assess(self.manifest([row]), self.root)
        row['validation_errors'] = ''
        manifest = self.manifest([row])
        manifest.write_text(manifest.read_text().splitlines()[0] + '\n')
        with self.assertRaisesRegex(ValueError, 'empty'):
            budget.assess(manifest, self.root)


if __name__ == '__main__':
    unittest.main()
