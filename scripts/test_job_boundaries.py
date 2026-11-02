"""Offline failure injection; child processes and browser actions are mocked."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import client
import gui_server


class JobBoundaries(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='job-boundary-test-')
        self.addCleanup(temp.cleanup)
        self.app = gui_server.App(local=Path(temp.name), open_browser=False)

    def test_initial_record_failure_never_registers_worker_or_busy_state(self):
        original = gui_server.atomic_json
        for failing_write in (1, 2):
            calls = []
            def fail(path, value):
                calls.append(1)
                if len(calls) == failing_write:
                    raise PermissionError('fixture')
                return original(path, value)
            with self.subTest(write=failing_write), patch.object(gui_server, 'atomic_json', side_effect=fail), \
                 patch.object(self.app, '_run_job') as worker:
                with self.assertRaises(gui_server.ApiError):
                    self.app.start_job({'action': 'browser-open', 'confirm': 'BROWSER-OPEN'})
                self.assertIsNone(self.app.active_job)
                self.assertEqual(self.app.jobs, {})
                worker.assert_not_called()

    def test_thread_start_failure_removes_registration(self):
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('fixture')):
            with self.assertRaises(gui_server.ApiError):
                self.app.start_job({'action': 'browser-open', 'confirm': 'BROWSER-OPEN'})
        self.assertIsNone(self.app.active_job)
        self.assertEqual(self.app.jobs, {})

    def test_started_child_stays_tracked_even_when_runtime_and_final_writes_fail(self):
        entered, release = threading.Event(), threading.Event()
        original = gui_server.atomic_json
        def fail_after_launch(path, value):
            if value.get('pid') or value.get('finished'):
                raise PermissionError('fixture')
            return original(path, value)
        def wait():
            entered.set()
            release.wait(3)
            job = next(iter(self.app.jobs.values()))
            gui_server.append_event(Path(job['folder']) / 'events.jsonl', {'type': 'done', 'message': 'fixture'})
            return 0
        child = SimpleNamespace(pid=123456789, wait=wait)
        with patch.object(self.app, '_validate_job', return_value=('snapshot', {})), \
             patch.object(self.app, '_job_command', side_effect=lambda a,p,f: (['offline-fixture'], f/'events.jsonl')), \
             patch.object(gui_server.subprocess, 'Popen', return_value=child), \
             patch.object(gui_server, 'atomic_json', side_effect=fail_after_launch):
            view = self.app.start_job({})
            job = self.app.jobs[view['id']]
            try:
                self.assertTrue(entered.wait(1))
                self.assertIs(job['process'], child)
                running = self.app.job_view(view['id'])
                self.assertEqual(running['state'], 'running')
                self.assertTrue(running['record_warning'])
                self.assertIs(self.app.active_job, job)
                self.assertFalse(self.app.stop())
                with self.assertRaises(gui_server.ApiError):
                    self.app.start_job({})
            finally:
                release.set()
                job['thread'].join(3)
        self.assertFalse(job['thread'].is_alive())
        self.assertIsNone(self.app.active_job)
        completed = self.app.job_view(view['id'])
        self.assertEqual(completed['state'], 'success')
        self.assertTrue(completed['record_warning'])

    def test_wait_failure_uses_confirmed_poll_result_without_orphaning_child(self):
        from unittest.mock import Mock
        child = SimpleNamespace(pid=123456789, wait=Mock(side_effect=OSError('fixture')), poll=Mock(return_value=0))
        with patch.object(self.app, '_validate_job', return_value=('snapshot', {})), \
             patch.object(self.app, '_job_command', side_effect=lambda a,p,f: (['offline-fixture'], f/'events.jsonl')), \
             patch.object(gui_server.subprocess, 'Popen', return_value=child), \
             patch.object(gui_server.time, 'sleep'):
            view = self.app.start_job({})
            job = self.app.jobs[view['id']]
            job['thread'].join(3)
        self.assertFalse(job['thread'].is_alive())
        self.assertIsNone(self.app.active_job)
        self.assertEqual(self.app.job_view(view['id'])['exit_code'], 0)
        child.poll.assert_called()
