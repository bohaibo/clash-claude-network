"""Offline lifecycle/log tests. Browser actions are always mocked."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_server


class GuiLifecycle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='gui-lifecycle-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.app = gui_server.App(local=self.local, open_browser=False, idle_exit=False)
        self.enterContext(patch.object(self.app, 'transaction_folder', return_value=self.local))
        self.enterContext(patch.object(self.app, 'settings_transactions', return_value=[]))

    def test_stop_defers_during_settings_save(self):
        with self.app.settings_operation():
            self.assertFalse(self.app.stop())
            self.assertFalse(self.app.stopping)
        self.app.stop()
        self.assertTrue(self.app.stopping)

    def test_shutdown_reservation_rejects_new_mutations(self):
        self.app._shutdown_requested = True
        with self.assertRaises(gui_server.ApiError):
            self.app.start_job({'action': 'settings-recover', 'transaction': 'fixture', 'confirm': 'fixture'})
        with self.assertRaises(gui_server.ApiError):
            with self.app.settings_operation():
                self.fail('Entered settings operation during shutdown')

    def test_settings_recovery_returns_job_before_slow_mock_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def browser(payload):
            entered.set()
            release.wait(3)
            return {'engine': 'edge', 'requested_engine': 'chrome'}
        with patch.object(self.app, 'restore_settings_transaction', side_effect=browser) as action:
            before = time.monotonic()
            self.app.settings_transactions.return_value=[{'id':'fixture'}]
            job = self.app.start_job({'action': 'settings-recover', 'transaction': 'fixture', 'confirm': 'fixture'})
            self.assertLess(time.monotonic() - before, .5)
            self.assertEqual(job['state'], 'running')
            worker = self.app.active_job['thread']
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.app.stop())
                with self.assertRaises(gui_server.ApiError):
                    self.app.cancel_job(job['id'])
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertIsNone(self.app.active_job)
            result = self.app.job_view(job['id'])
            self.assertEqual(result['state'], 'success')
            self.assertEqual(result['result']['engine'], 'edge')
            action.assert_called_once()

    def test_failed_published_status_is_not_marked_fresh(self):
        self.app.publish_background('update', {'mode': 'manual', 'task': {'error': 'fixture'}})
        status = self.app.collection_status()['update']
        self.assertTrue(status['stale'])
        self.assertIn('计划任务', status['error'])
        self.assertEqual(status['read_at'], '')

    def test_local_job_record_failure_does_not_leave_permanent_busy_state(self):
        with patch.object(gui_server, 'append_event', side_effect=OSError('disk fixture')), \
             patch.object(self.app, 'restore_settings_transaction') as browser:
            self.app.settings_transactions.return_value=[{'id':'fixture'}]
            job = self.app.start_job({'action': 'settings-recover', 'transaction': 'fixture', 'confirm': 'fixture'})
            self.app.jobs[job['id']]['thread'].join(3)
        self.assertIsNone(self.app.active_job)
        self.assertEqual(self.app.job_view(job['id'])['state'], 'failed')
        browser.assert_not_called()

    def test_log_tail_is_bounded_and_preserves_unicode_suffix(self):
        path = self.local / 'large.log'
        content = ('中文日志 abc\n' * 50000) + '末尾🙂完成'
        path.write_text(content, encoding='utf-8')
        result = gui_server.tail_text(path, limit=80, lines=5)
        expected = '\n'.join(content.splitlines()[-5:])[-80:]
        self.assertEqual(result, expected)
        with patch.object(Path, 'read_text', side_effect=AssertionError('unbounded read')):
            self.assertEqual(gui_server.tail_text(path, limit=80, lines=5), expected)
            self.app.local.joinpath('gui').mkdir()
            log = self.app.local / 'gui/server.log'
            log.write_bytes(path.read_bytes())
            self.assertLessEqual(len(self.app.log_text('gui/server.log')['text']), 200000)
