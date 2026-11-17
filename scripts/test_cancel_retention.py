"""Offline process-state and retention checks; never query or terminate real processes."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import client
import gui_server


class CancelRetention(unittest.TestCase):
    def restart(self, job, folder, async_checks=False):
        gui_server.atomic_json(folder/'job.json', {key:job[key] for key in ('id','action','params','created','state')})
        app=gui_server.App(local=self.app.local,open_browser=False,idle_exit=False)
        if not async_checks:
            # Reconciliation tests inject fresh evidence; asynchronous collection
            # and its freshness rules are covered separately below.
            app._recovery_evidence=lambda job,status: {'info':app._process_info(status['pid']) if type(status.get('pid')) is int else {'alive':None},
                'clear':app._local_recovery_quiescent() if job['action'] in gui_server.LOCAL_ACTIONS else False,'checking':False}
        return app

    def test_recovery_collection_coalesces_polls_and_discards_stale_evidence(self):
        job,folder=self.job(1);app=self.restart(job,folder,async_checks=True)
        entered=threading.Event();release=threading.Event()
        self.addCleanup(release.set)
        def query(pid):
            entered.set();release.wait(2);return {'alive':False}
        with patch.object(app,'_process_info',side_effect=query) as probe:
            first=app.active_job_view();self.assertEqual(first['state'],'running')
            self.assertTrue(entered.wait(1))
            for _ in range(5):self.assertEqual(app.active_job_view()['state'],'running')
            self.assertEqual(probe.call_count,1)
            release.set()
            deadline=time.monotonic()+2
            while any(row['running'] for row in app._recovery_checks.values()) and time.monotonic()<deadline:time.sleep(.01)
            for _ in range(3):app.active_job_view()
            self.assertEqual(probe.call_count,1,'fresh results serve repeated polls')
            entry=next(iter(app._recovery_checks.values()))
            with patch.object(gui_server.time,'monotonic',return_value=entry['at']+4),patch.object(gui_server.threading.Thread,'start'):
                evidence=app._recovery_evidence(app.active_job,{'pid':123456789})
            self.assertTrue(evidence['checking']);self.assertIsNone(evidence['info']['alive'],'expired absence cannot release protection')

    def test_unreadable_shell_process_does_not_clear_local_recovery(self):
        original=subprocess.run
        fixtures=[("[pscustomobject]@{ProcessId=123456789;Name='powershell.exe';CommandLine=$null}",False),
                  ("[pscustomobject]@{ProcessId=123456789;Name='pwsh.exe';CommandLine=' '}",False),
                  ("[pscustomobject]@{ProcessId=123456789;Name='unrelated.exe';CommandLine=$null}",True),
                  ("[pscustomobject]@{ProcessId=123456789;Name='powershell.exe';CommandLine='unrelated script'}",True)]
        for fixture,expected in fixtures:
            def simulate(command,**kwargs):
                command=list(command);command[-1]='function Get-CimInstance { '+fixture+' };'+command[-1]
                return original(command,**kwargs)
            with patch.object(gui_server.subprocess,'run',side_effect=simulate):
                self.assertEqual(self.app._local_recovery_quiescent(),expected)

    def test_restart_adopts_live_job_without_starting_or_killing(self):
        job,folder=self.job(1)
        restarted=self.restart(job,folder)
        with patch.object(restarted,'_process_info',return_value={'alive':True}), patch.object(gui_server.subprocess,'Popen') as launch:
            view=restarted.active_job_view()
            self.assertEqual(view['id'],job['id']);self.assertEqual(view['state'],'running')
            self.assertTrue(view['record_warning'])
            with self.assertRaises(gui_server.ApiError):restarted.start_job({'action':'snapshot'})
            launch.assert_not_called()

    def test_restart_unknown_or_absent_without_terminal_evidence_stays_busy(self):
        job,folder=self.job(1);restarted=self.restart(job,folder)
        for info in ({'alive':None},{'alive':False}):
            with patch.object(restarted,'_process_info',return_value=info):
                self.assertEqual(restarted.active_job_view()['state'],'running')
                self.assertFalse(restarted.stop())

    def test_restart_terminal_evidence_requires_process_exit_and_free_lock(self):
        job,folder=self.job(1);restarted=self.restart(job,folder)
        gui_server.append_event(folder/'events.jsonl',{'type':'done','message':'fixture'})
        for alive,locked in ((True,False),(None,False),(False,True)):
            with patch.object(restarted,'_process_info',return_value={'alive':alive}),patch.object(gui_server,'operation_in_use',return_value=locked):
                self.assertEqual(restarted.active_job_view()['state'],'running')
        with patch.object(restarted,'_process_info',return_value={'alive':False}),patch.object(gui_server,'operation_in_use',return_value=False):
            view=restarted.active_job_view()
            self.assertEqual(view['state'],'unverified')
            self.assertIsNone(view['exit_code'])
        self.assertIsNone(restarted.active_job_view())

    def test_recovered_elapsed_uses_original_terminal_time(self):
        job,folder=self.job(1)
        start=gui_server.datetime.datetime.now().astimezone()-gui_server.datetime.timedelta(hours=1)
        job['created']=start.isoformat()
        gui_server.atomic_json(folder/'status.json',{'state':'running','action':'snapshot','pid':123456789,'started':start.isoformat()})
        gui_server.append_event(folder/'events.jsonl',{'type':'done','time':(start+gui_server.datetime.timedelta(seconds=60)).isoformat()})
        restarted=self.restart(job,folder)
        with patch.object(restarted,'_process_info',return_value={'alive':False}),patch.object(gui_server,'operation_in_use',return_value=False):
            view=restarted.active_job_view()
        self.assertEqual(view['elapsed'],60);self.assertEqual(view['elapsed_source'],'terminal-event')
        self.assertEqual(restarted.job_view(job['id'])['elapsed'],60)
        again=gui_server.App(local=restarted.local,open_browser=False,idle_exit=False)
        self.assertIsNone(again.active_job_view())
        self.assertEqual(again.job_view(job['id'])['state'],'unverified')
        # Legacy timestamps without a timezone cannot establish a duration.
        job2,folder2=self.job(2)
        gui_server.append_event(folder2/'events.jsonl',{'type':'done','time':'2026-01-01T00:00:00'})
        restarted=self.restart(job2,folder2)
        with patch.object(restarted,'_process_info',return_value={'alive':False}),patch.object(gui_server,'operation_in_use',return_value=False):
            view=restarted.active_job_view()
        self.assertEqual(view['state'],'unverified');self.assertIsNone(view['elapsed'])

    def test_local_recovery_requires_quiescence_and_preserves_transaction(self):
        job,folder=self.job(1);job['action']='settings-recover'
        transaction='b'*32;job['params']={'transaction':transaction}
        journal=self.app.local/'gui/settings-transactions'/transaction/'transaction.json'
        gui_server.atomic_json(journal,{'phase':'recovering'})
        before=journal.read_bytes();restarted=self.restart(job,folder)
        for clear,locked in ((False,False),(True,True)):
            with patch.object(restarted,'_process_info',return_value={'alive':None}),patch.object(restarted,'_local_recovery_quiescent',return_value=clear),patch.object(gui_server,'operation_in_use',return_value=locked):
                self.assertEqual(restarted.active_job_view()['state'],'running')
        with patch.object(restarted,'_process_info',return_value={'alive':None}),patch.object(restarted,'_local_recovery_quiescent',return_value=True),patch.object(gui_server,'operation_in_use',return_value=False):
            self.assertEqual(restarted.active_job_view()['state'],'unverified')
        self.assertIsNone(restarted.active_job_view());self.assertEqual(journal.read_bytes(),before)
        with patch.object(gui_server.threading.Thread,'start'):
            result=restarted.start_job({'action':'settings-recover','transaction':transaction,'confirm':transaction})
        self.assertEqual(result['state'],'running','original settings recovery is available for retry')

    def test_local_recovery_process_query_fails_closed(self):
        for outcome in (OSError('fixture'),subprocess.TimeoutExpired('fixture',8),SimpleNamespace(returncode=1,stdout='clear'),SimpleNamespace(returncode=0,stdout=''),SimpleNamespace(returncode=0,stdout='busy')):
            kwargs={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
            with patch.object(gui_server.subprocess,'run',**kwargs):self.assertFalse(self.app._local_recovery_quiescent())
        with patch.object(gui_server.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='clear')):
            self.assertTrue(self.app._local_recovery_quiescent())

    def test_restart_observes_external_completion_and_next_unresolved_job(self):
        job,folder=self.job(1);self.restart(job,folder)
        second,other=self.job(2);restarted=self.restart(second,other)
        gui_server.atomic_json(folder/'status.json',{'state':'failed','action':'snapshot','finished':gui_server.now_iso()})
        self.assertEqual(restarted.active_job_view()['state'],'failed')
        with patch.object(restarted,'_process_info',return_value={'alive':None}):
            self.assertEqual(restarted.active_job_view()['id'],second['id'])


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
