"""Settings transactions use fake tasks and isolated .local files; no Windows writes."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_server
import gui_settings


class SettingsTransactions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='settings-txn-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.app = gui_server.App(local=self.local, open_browser=False)
        self.task = {'xml': 'original-disabled-weekly-task', 'sddl': 'original-access'}
        self.backups = {}
        self.calls = []
        self.fail_action = None
        def task(action, *args, state_file=None):
            self.calls.append(action)
            if action == 'snapshot':
                self.backups[str(state_file)] = copy.deepcopy(self.task)
                Path(state_file).write_text('{}', encoding='utf-8')
            elif action == 'rollback':
                if self.fail_action == 'rollback':
                    raise OSError('rollback fixture')
                self.task = copy.deepcopy(self.backups[str(state_file)])
            elif action == 'remove':
                self.task = None
            elif action == 'install':
                self.task = {'xml': 'new-task'}
            if action == self.fail_action:
                raise OSError('partial mutation fixture')
        for mock in [patch.object(self.app, '_update_task', side_effect=task),
                     patch.object(self.app, 'configured', return_value=(True, '')),
                     patch.object(self.app, 'update_summary', return_value={'mode': 'manual'})]:
            mock.start()
            self.addCleanup(mock.stop)

    def test_partial_task_failure_restores_exact_file_and_task(self):
        path = gui_settings.settings_path(self.local)
        original = b'{"version":2, "update":{"mode":"manual","time":"07:23","days":3}}\n'
        path.write_bytes(original)
        before = copy.deepcopy(self.task)
        self.fail_action = 'install'
        with self.assertRaisesRegex(gui_server.ApiError, '已恢复'):
            self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00'})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.task, before)
        self.assertEqual(self.calls, ['snapshot', 'install', 'rollback'])

    def test_file_write_failure_restores_absent_file_and_absent_task(self):
        self.task = None
        real_save = gui_settings.save
        def fail_after_write(*args):
            real_save(*args)
            raise OSError('file write fixture')
        with patch.object(gui_settings, 'save', side_effect=fail_after_write):
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00'})
        self.assertFalse(gui_settings.settings_path(self.local).exists())
        self.assertIsNone(self.task)

    def test_rollback_failure_is_reported_as_incomplete(self):
        self.fail_action = 'rollback'
        with patch.object(gui_settings, 'save', side_effect=OSError('fixture')):
            with self.assertRaisesRegex(gui_server.ApiError, '恢复未完成'):
                self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})

    def test_settings_and_jobs_are_excluded_while_saving(self):
        with self.app.settings_operation():
            with self.assertRaises(gui_server.ApiError) as error:
                self.app.start_job({'action': 'snapshot', 'confirm': 'SNAPSHOT'})
            self.assertEqual(error.exception.status, 409)
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})
        self.assertFalse(self.app._settings_busy)

    def test_cross_process_lock_blocks_setting_changes_before_snapshot(self):
        from operation_lock import operation_lock
        with operation_lock(self.local):
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})
        self.assertEqual(self.calls, [])

    def test_two_saves_cannot_interleave(self):
        entered, release = threading.Event(), threading.Event()
        original = self.app._update_task.side_effect
        errors = []
        def delayed(action, *args, **kwargs):
            if action == 'snapshot':
                entered.set()
                release.wait(3)
            return original(action, *args, **kwargs)
        def first():
            try:
                self.app.save_update_settings({'mode': 'manual', 'time': '08:00'})
            except Exception as exc:
                errors.append(exc)
        self.app._update_task.side_effect = delayed
        worker = threading.Thread(target=first)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00'})
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(gui_settings.update(self.local)['time'], '08:00')

    def test_interrupted_save_is_found_after_restart_and_recovers_as_job(self):
        gui_settings.save('manual', '07:23', 3, self.local)
        original = gui_settings.settings_path(self.local).read_bytes()
        original_task = copy.deepcopy(self.task)
        real_save = gui_settings.save
        def crash(*args):
            real_save(*args)
            raise SystemExit('simulated power loss after mutations, before commit')
        with patch.object(gui_settings, 'save', side_effect=crash):
            with self.assertRaises(SystemExit):
                self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00'})
        reopened = gui_server.App(local=self.local, open_browser=False)
        pending = reopened.settings_transactions()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['phase'], 'applying')
        with self.assertRaises(gui_server.ApiError):
            reopened.start_job({'action': 'browser-open', 'confirm': 'BROWSER-OPEN'})
        with patch.object(reopened, '_update_task', side_effect=self.app._update_task.side_effect):
            job = reopened.start_job({'action': 'settings-recover', 'transaction': pending[0]['id'], 'confirm': pending[0]['id']})
            reopened.jobs[job['id']]['thread'].join(3)
        self.assertEqual(reopened.job_view(job['id'])['state'], 'success')
        self.assertEqual(gui_settings.settings_path(self.local).read_bytes(), original)
        self.assertEqual(self.task, original_task)
        self.assertEqual(reopened.settings_transactions(), [])

    def test_crash_before_task_checkpoint_does_not_require_task_rollback(self):
        self.app._update_task.side_effect = SystemExit('power loss before checkpoint')
        with self.assertRaises(SystemExit):
            self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})
        pending = self.app.settings_transactions()
        self.assertEqual(pending[0]['phase'], 'preparing')
        self.app._update_task.reset_mock()
        self.assertTrue(self.app.restore_settings_transaction(pending[0]['id'])['success'])
        self.app._update_task.assert_not_called()

    def test_tampered_checkpoint_is_not_replayed(self):
        with patch.object(gui_settings, 'save', side_effect=SystemExit('power loss')):
            with self.assertRaises(SystemExit):
                self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})
        pending = self.app.settings_transactions()[0]
        folder = self.app.transaction_folder(pending['id'])
        (folder / 'task.json').write_text('tampered', encoding='utf-8')
        before = len(self.calls)
        with self.assertRaisesRegex(gui_server.ApiError, '校验失败'):
            self.app.restore_settings_transaction(pending['id'])
        self.assertEqual(len(self.calls), before)

    def test_failed_rollback_remains_recoverable(self):
        self.fail_action = 'rollback'
        with patch.object(gui_settings, 'save', side_effect=OSError('fixture')):
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'manual', 'time': '09:00'})
        pending = self.app.settings_transactions()[0]
        self.assertEqual(pending['phase'], 'recovery_failed')
        self.fail_action = None
        self.app.restore_settings_transaction(pending['id'])
        self.assertEqual(self.app.settings_transactions(), [])
