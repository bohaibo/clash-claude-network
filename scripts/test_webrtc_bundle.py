"""Offline source bundle preparation and tamper tests, without any browser."""
import json
import tempfile
import unittest
import subprocess
from pathlib import Path
import client
import webrtc_bundle as bundle


class OfflineAddonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='addon-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reuses_exact_payload_and_refuses_to_replace_changed_files(self):
        target = bundle.prepare(self.root / 'destination')
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()}
        self.assertEqual(bundle.prepare(self.root / 'destination'), target)
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in target.iterdir()})
        (target / 'block.js').write_text('later user change')
        with self.assertRaises(ValueError):
            bundle.prepare(self.root / 'destination')
        self.assertEqual((target / 'block.js').read_text(), 'later user change')

    def test_corrupt_source_never_creates_a_destination(self):
        source = self.root / 'project' / 'assets' / 'webrtc-addon'
        source.mkdir(parents=True)
        for name, raw in bundle.payload().items():
            (source / name).write_bytes(raw)
        (source / 'bundle.json').write_bytes((bundle.PROJECT / 'assets/webrtc-addon/bundle.json').read_bytes())
        (source / 'block.js').write_text('corrupted')
        destination = self.root / 'destination'
        with self.assertRaises(ValueError):
            bundle.prepare(destination, self.root / 'project')
        self.assertFalse(destination.exists())

    def test_manifest_has_no_store_update_or_remote_code(self):
        manifest = json.loads(bundle.payload()['manifest.json'])
        self.assertNotIn('update_url', manifest)
        self.assertEqual(manifest['permissions'], ['privacy'])
        self.assertEqual(manifest['content_scripts'][0]['world'], 'MAIN')
        self.assertTrue(manifest['content_scripts'][0]['all_frames'])

    def test_offline_addon_behavior(self):
        node = client.PROJECT / '.runtime/node/node.exe'
        result = subprocess.run([str(node), 'scripts/test_webrtc_addon.js'], cwd=client.PROJECT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
