"""Offline process-state and retention checks; never query or terminate real processes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import client
import gui_server


class CancelRetention(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='cancel-retention-test-')
        self.addCleanup(temp.cleanup)
        self.app = gui_server.App(local=Path(temp.name), open_browser=False)

    def job(self, number, state='running', tracked=True):
        jid = f'{number:032x}'
        folder = self.app.jobs_dir / jid
        folder.mkdir(parents=True)
        status = {'state': state, 'action': 'snapshot', 'pid': 123456789, 'started': gui_server.now_iso()}
        gui_server.atomic_json(folder/'status.json', status)
        job = {'id': jid, 'action': 'snapshot', 'params': {}, 'state': state,
               'created': status['started'], 'folder': str(folder), 'runtime': status}
        self.app.jobs[jid] = job
        if tracked:
            job['process'] = SimpleNamespace(poll=Mock(return_value=None), wait=Mock(return_value=0))
            self.app.active_job = job
        os.utime(folder, (100000+number, 100000+number))
        return job, folder

    def test_query_failure_is_unknown_and_preserves_busy_state(self):
        job, folder = self.job(1)
        for response in (subprocess.TimeoutExpired('fixture', 8), OSError('fixture'),
                         SimpleNamespace(returncode=1, stdout=''),
                         SimpleNamespace(returncode=0, stdout='not JSON'),
                         SimpleNamespace(returncode=0, stdout='{"alive":"false"}')):
            with self.subTest(response=response):
                kwargs = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch.object(gui_server.subprocess, 'run', **kwargs) as run:
                    with self.assertRaises(gui_server.ApiError):
                        self.app.cancel_job(job['id'])
                self.assertEqual(run.call_count, 1, 'never call taskkill after uncertain query')
                self.assertIs(self.app.active_job, job)
                self.assertEqual(self.app.job_view(job['id'])['state'], 'running')
                self.assertEqual(json.loads((folder/'status.json').read_text())['state'], 'running')

    def test_absent_query_cannot_override_live_tracked_handle(self):
        job, _ = self.job(1)
        with patch.object(self.app, '_process_info', return_value={'alive': False}), \
             patch.object(gui_server.subprocess, 'run') as kill:
            with self.assertRaises(gui_server.ApiError):self.app.cancel_job(job['id'])
            kill.assert_not_called()
        self.assertIs(self.app.active_job, job)

    def test_powershell_query_errors_cannot_emit_false_absence(self):
        original = subprocess.run
        def fake_cim(command, **kwargs):
            command = list(command)
            command[-1] = "function Get-CimInstance { Write-Error 'offline fixture' };" + command[-1]
            return original(command, **kwargs)
        with patch.object(gui_server.subprocess, 'run', side_effect=fake_cim):
            self.assertIsNone(self.app._process_info(123456789)['alive'])

    def test_confirmed_exit_can_release_busy_state(self):
        job, _ = self.job(1)
        job['process'].poll.return_value = 0
        with patch.object(self.app, '_process_info', return_value={'alive': False}):
            self.assertEqual(self.app.cancel_job(job['id'])['state'], 'interrupted')
        self.assertIsNone(self.app.active_job)

    def test_kill_success_requires_exit_confirmation(self):
        job, _ = self.job(1)
        job['process'].wait.side_effect = subprocess.TimeoutExpired('fixture', 5)
        info = {'alive': True, 'command': str(self.app.scripts/'run.ps1')}
        with patch.object(self.app, '_process_info', return_value=info), \
             patch.object(gui_server.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            with self.assertRaises(gui_server.ApiError):self.app.cancel_job(job['id'])
        self.assertIs(self.app.active_job, job)
        self.assertEqual(self.app.job_view(job['id'])['state'], 'running')

    def test_cleanup_retains_running_unknown_and_corrupt_records(self):
        protected = []
        for index, state in enumerate(('running', 'running', 'unknown', None)):
            _, folder = self.job(index, state, tracked=False)
            if state is None:(folder/'status.json').write_text('{broken')
            protected.append(folder)
        for index in range(4, 36):self.job(index, 'success', tracked=False)
        with patch.object(self.app, '_process_info', side_effect=AssertionError('cleanup must not query processes')):
            self.app._cleanup_jobs()
        self.assertTrue(all(folder.exists() for folder in protected))
        self.assertEqual(sum(1 for folder in self.app.jobs_dir.iterdir() if folder.name not in {p.name for p in protected}), 30)
        self.assertFalse((self.app.jobs_dir/f'{4:032x}').exists())
        self.assertFalse((self.app.jobs_dir/f'{5:032x}').exists())
