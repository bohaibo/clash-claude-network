"""Fixture-only deletion tests; never operate on the user's snapshots."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import snapshot_store as store


class SnapshotStore(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='snapshot-store-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.first = self.snapshot('first')
        self.write('baseline-selection.json', {'snapshot': 'first'})
        self.old = self.snapshot('old')

    def write(self, name, data):
        (self.local / name).write_text(json.dumps(data), encoding='utf-8')

    def snapshot(self, name):
        folder = self.local / 'snapshots' / name
        folder.mkdir(parents=True)
        (folder / 'manifest.json').write_text('{"kind":"manual"}', encoding='utf-8')
        (folder / 'client').mkdir()
        (folder / 'client' / 'original.yaml').write_text('fixture', encoding='utf-8')
        return folder

    def test_deletes_only_selected_unreferenced_snapshot(self):
        store.delete_snapshot(self.local, 'old')
        self.assertFalse(self.old.exists())
        self.assertTrue((self.first / 'client' / 'original.yaml').exists())
        self.assertEqual(json.loads((self.local / 'baseline-selection.json').read_text())['snapshot'], 'first')

    def test_first_baseline_protected(self):
        with self.assertRaises(ValueError):
            store.delete_snapshot(self.local, 'first')
        self.assertTrue(self.first.exists())

    def test_plan_and_pending_references_protected(self):
        for filename in ('plan.json', 'pending-firewall.json', 'reconfigure-active.json'):
            with self.subTest(filename=filename):
                self.write(filename, {'snapshot': str(self.old)})
                with self.assertRaises(ValueError):
                    store.delete_snapshot(self.local, 'old')
                (self.local / filename).unlink()

    def test_unpinned_legacy_baseline_protected(self):
        (self.local / 'baseline-selection.json').unlink()
        with self.assertRaises(ValueError):
            store.delete_snapshot(self.local, 'first')

    def test_corrupt_recovery_pointer_blocks_deletion(self):
        for filename in ('baseline-selection.json', 'plan.json'):
            (self.local / filename).write_text('{broken', encoding='utf-8')
            with self.assertRaises(ValueError):
                store.delete_snapshot(self.local, 'old')
            self.write('baseline-selection.json', {'snapshot': 'first'})

    def test_invalid_and_missing_names(self):
        for name in ('../first', '..', '.', 'old/child', 'C:\\outside', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                store.delete_snapshot(self.local, name)
        with self.assertRaises(FileNotFoundError):
            store.delete_snapshot(self.local, 'missing')

    def test_missing_plan_reference_blocks_deletion(self):
        self.write('plan.json', {})
        with self.assertRaises(ValueError):
            store.delete_snapshot(self.local, 'old')

    def test_nested_link_refused_before_any_delete(self):
        original = store.linked
        with patch.object(store, 'linked', side_effect=lambda p: p.name == 'original.yaml' or original(p)):
            with self.assertRaises(ValueError):
                store.delete_snapshot(self.local, 'old')
        self.assertTrue((self.old / 'client' / 'original.yaml').exists())

    def test_root_and_target_links_refused(self):
        original = store.linked
        for name in ('snapshots', 'old'):
            with patch.object(store, 'linked', side_effect=lambda p: p.name == name or original(p)):
                with self.assertRaises(ValueError):
                    store.delete_snapshot(self.local, 'old')


if __name__ == '__main__':
    unittest.main()
