"""Offline shared guard coverage; no live scheduler/network actions."""
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import client
from operation_lock import operation_lock, pending_settings


class PendingSettingsGuard(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='pending-settings-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        folder = self.local / 'gui/settings-transactions' / ('a' * 32)
        folder.mkdir(parents=True)
        self.journal = folder / 'transaction.json'

    def test_pending_or_malformed_journal_blocks_normal_entries(self):
        for data in ('{"phase":"applying"}', '{broken', '{}', '[]'):
            self.journal.write_text(data, encoding='utf-8')
            with self.subTest(data=data), self.assertRaisesRegex(RuntimeError, 'Settings recovery'):
                with operation_lock(self.local):
                    self.fail('entered protected operation')

    def test_completed_and_legacy_records_do_not_block(self):
        for phase in ('committed', 'rolled_back'):
            self.journal.write_text(json.dumps({'phase': phase}), encoding='utf-8')
            with operation_lock(self.local):
                self.assertEqual(pending_settings(self.local), [])
        self.journal.unlink()
        with operation_lock(self.local):pass

    def test_recovery_channel_does_not_bypass_firewall_protection(self):
        self.journal.write_text('{"phase":"recovery_failed"}', encoding='utf-8')
        with operation_lock(self.local, settings_recovery=True):pass
        (self.local/'pending-firewall.json').write_text(json.dumps({'token': 'guard-'+'b'*32}), encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'Firewall rollback'):
            with operation_lock(self.local, settings_recovery=True):pass

    def test_shared_guard_blocks_separate_python_entry(self):
        self.journal.write_text('{"phase":"applying"}', encoding='utf-8')
        code = 'from operation_lock import operation_lock;import sys\nwith operation_lock(sys.argv[1]): print("unexpected")'
        result = subprocess.run([sys.executable, '-c', code, str(self.local)], cwd=client.PROJECT/'scripts',
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Settings recovery', result.stderr)
        self.assertNotIn('unexpected', result.stdout)

    def test_reconfiguration_stops_before_baseline_or_worker_access(self):
        import safe_reconfigure
        self.journal.write_text('{"phase":"applying"}', encoding='utf-8')
        with patch.object(client, 'LOCAL', self.local), \
             patch.object(safe_reconfigure.baseline, 'preflight_restore') as baseline, \
             patch.object(safe_reconfigure.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(RuntimeError, 'Settings recovery'):
                safe_reconfigure.reconfigure()
            baseline.assert_not_called()
            process.assert_not_called()
