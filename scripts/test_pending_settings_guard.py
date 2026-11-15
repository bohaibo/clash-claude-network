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

    def test_pending_rule_transaction_blocks_operations_even_when_malformed(self):
        pending=self.local/'rule-update-pending.json'
        for content in ('{}','{broken','{"id":"'+'a'*32+'"}'):
            pending.write_text(content,encoding='utf-8')
            for settings_recovery in (False,True):
                with self.subTest(content=content,settings_recovery=settings_recovery),self.assertRaisesRegex(RuntimeError,'Rule update recovery'):
                    with operation_lock(self.local,settings_recovery=settings_recovery):self.fail('entered operation')
            with operation_lock(self.local,rule_recovery=True):pass
        pending.unlink()
        with operation_lock(self.local):pass

    def test_rule_recovery_does_not_bypass_settings_or_firewall_guards(self):
        (self.local/'rule-update-pending.json').write_text('{}',encoding='utf-8')
        self.journal.write_text('{"phase":"applying"}',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'Settings recovery'):
            with operation_lock(self.local,rule_recovery=True):pass
        self.journal.unlink()
        (self.local/'pending-firewall.json').write_text('{"token":"guard-'+'b'*32+'"}',encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'Firewall rollback'):
            with operation_lock(self.local,rule_recovery=True):pass

    def test_pending_rules_stop_reconfiguration_before_any_worker(self):
        import safe_reconfigure
        (self.local/'rule-update-pending.json').write_text('{}',encoding='utf-8')
        with patch.object(client,'LOCAL',self.local),patch.object(safe_reconfigure.baseline,'preflight_restore') as baseline,patch.object(safe_reconfigure.subprocess,'Popen') as process:
            with self.assertRaisesRegex(RuntimeError,'Rule update recovery'):safe_reconfigure.reconfigure()
        baseline.assert_not_called();process.assert_not_called()

    def test_gui_blocks_new_jobs_before_validation_when_rule_recovery_is_pending(self):
        import gui_server
        app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
        (self.local/'rule-update-pending.json').write_text('{}',encoding='utf-8')
        for action in ('apply','supplement','restore','snapshot','reconfigure','verify','audit'):
            with self.subTest(action=action),patch.object(app,'_validate_job') as validate:
                with self.assertRaises(gui_server.ApiError):app.start_job({'action':action})
                validate.assert_not_called()

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
