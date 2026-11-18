"""Offline background state and frontend behavior checks."""
import subprocess
import datetime
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_server


class BackgroundState(unittest.TestCase):
    def test_job_elapsed_stops_at_completion(self):
        with tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='elapsed-contract-') as folder:
            app=gui_server.App(local=Path(folder),open_browser=False,idle_exit=False)
            token='a'*32;record=app.jobs_dir/token;record.mkdir(parents=True)
            start=datetime.datetime(2026,1,1,tzinfo=datetime.timezone.utc)
            client.write(record/'job.json',{'action':'apply','created':start.isoformat()})
            for state in ('success','failed','cancelled','interrupted'):
                client.write(record/'status.json',{'state':state,'started':start.isoformat(),
                    'finished':(start+datetime.timedelta(seconds=60)).isoformat()})
                with patch.object(gui_server.time,'time',return_value=start.timestamp()+3600):
                    self.assertEqual(app.job_view(token)['elapsed'],60)
                app.jobs[token]={'id':token,'action':'apply','started_epoch':start.timestamp()}
                with patch.object(gui_server.time,'time',return_value=start.timestamp()+7200):
                    self.assertEqual(app.job_view(token)['elapsed'],60)
            client.write(record/'status.json',{'state':'running','started':start.isoformat()})
            with patch.object(gui_server.time,'time',return_value=start.timestamp()+120):
                self.assertEqual(app.job_view(token)['elapsed'],120)
            client.write(record/'status.json',{'state':'success','started':start.isoformat()})
            self.assertIsNone(app.job_view(token)['elapsed'])
            gui_server.append_event(record/'events.jsonl',{'type':'done','time':(start+datetime.timedelta(seconds=30)).isoformat()})
            self.assertEqual(app.job_view(token)['elapsed'],30)

    def test_configuration_evidence_reaches_actual_frontend(self):
        with tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='ui-contract-') as folder:
            local=Path(folder);app=gui_server.App(local=local,open_browser=False,idle_exit=False)
            now=datetime.datetime.now().astimezone()
            record={'success':True,'action':'apply','network_tested':False,
                    'configuration_time':(now-datetime.timedelta(minutes=30)).isoformat()}
            client.write(local/'last-run.json',record)
            methods={'plan':{'node':'Offline chosen'},'configured':(True,''),'snapshots':[],
                     'baseline_name':'fixture','verification_summary':{'available':True,'success':True,
                     'source_matches_current':True,'time':(now-datetime.timedelta(hours=1)).isoformat()},
                     'software_summary':{},'settings_transactions':[],'pending_firewall':False,
                     'reconfigure_runs':[],'log_files':[],'node_path':'fixture','collection_status':{}}
            for method,value in methods.items():self.enterContext(patch.object(app,method,return_value=value))
            self.enterContext(patch.object(app,'background_value',side_effect=lambda name,fn,default,ttl:default))
            state=app.app_state()
            self.assertIs(state['last_run']['network_tested'],False)
            self.assertEqual(state['last_run']['configuration_time'],record['configuration_time'])
            fixture=local/'state.json';fixture.write_text(json.dumps(state),encoding='utf-8')
            result=subprocess.run([str(client.PROJECT/'.runtime/node/node.exe'),'scripts/test_ui_accessibility.js',str(fixture)],
                cwd=client.PROJECT,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            guard=local/'tun-drills'/('b'*32);guard.mkdir(parents=True);(guard/'ready').touch()
            self.assertEqual(app.app_state()['operation']['pending_tun'],[{'id':'b'*32,'status':'pending'}])
            client.write(guard/'recovery.json',{'success':False,'root':'private fixture must not reach API'})
            self.assertEqual(app.app_state()['operation']['pending_tun'],[{'id':'b'*32,'status':'failed','retryable':False}])
            from operation_lock import check_pending_tun
            with self.assertRaises(RuntimeError):check_pending_tun(local)
            (guard/'done').touch()
            self.assertEqual(app.app_state()['operation']['pending_tun'],[])
            check_pending_tun(local)
            for value in (True,None,'false',0):
                client.write(local/'last-run.json',dict(record,network_tested=value))
                self.assertIs(app.app_state()['last_run']['network_tested'],value if type(value) is bool else None)

    def wait_idle(self, app, name):
        deadline = time.monotonic() + 2
        while app._background[name]['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(app._background[name]['running'])

    def test_old_collector_cannot_overwrite_authoritative_save(self):
        app = gui_server.App(open_browser=False)
        entered, release = threading.Event(), threading.Event()
        def old():
            entered.set()
            release.wait(2)
            return {'mode': 'old'}
        app.background_value('update', old, {})
        self.assertTrue(entered.wait(1))
        app.publish_background('update', {'mode': 'new'})
        release.set()
        self.wait_idle(app, 'update')
        self.assertEqual(app._background['update']['value'], {'mode': 'new'})

    def test_invalidated_collector_does_not_republish_old_state(self):
        app = gui_server.App(open_browser=False)
        release = threading.Event()
        app.background_value('browser', lambda: (release.wait(2), {'old': True})[1], {})
        app.invalidate_background('browser')
        release.set()
        self.wait_idle(app, 'browser')
        self.assertEqual(app._background['browser']['value'], {})
        self.assertTrue(app.collection_status()['browser']['stale'])

    def test_waiting_state_request_returns_values_after_invalidated_read_is_replaced(self):
        app = gui_server.App(open_browser=False)
        entered, release = threading.Event(), threading.Event()
        def old():
            entered.set()
            release.wait(2)
            return {'current': {'fixture': 'old'}}
        app.background_value('preview', old, {})
        try:
            self.assertTrue(entered.wait(1))
            app.invalidate_background('preview')
        finally:
            release.set()
        with (patch.object(app, 'configuration_preview', return_value={'current': {'fixture': 'new'}}) as preview,
              patch.object(app, 'update_summary', return_value={'mode': 'manual'}),
              patch.object(app, 'service_status', return_value={'service': 'Running'})):
            values, status = app.collect_state(wait=True)
            preview.assert_called_once_with()
        self.assertEqual(values['preview']['current']['fixture'], 'new')
        self.assertEqual(values['update']['mode'], 'manual')
        self.assertEqual(values['service']['service'], 'Running')
        self.assertTrue(all(row['read_at'] and not row['refreshing'] for row in status.values()))

    def test_slow_collection_is_nonblocking_and_single_flight(self):
        with tempfile.TemporaryDirectory(dir=client.LOCAL) as folder:
            app = gui_server.App(local=folder, open_browser=False)
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            calls = []
            def load():
                calls.append(1)
                entered.set()
                release.wait(3)
                finished.set()
                return {'ready': True}
            try:
                before = time.monotonic()
                self.assertEqual(app.background_value('slow', load, {}), {})
                self.assertLess(time.monotonic() - before, 0.5)
                self.assertTrue(entered.wait(1))
                self.assertEqual(app.background_value('slow', load, {}), {})
                self.assertEqual(len(calls), 1)
            finally:
                release.set()
                finished.wait(1)
            deadline = time.monotonic() + 1
            while app._background['slow']['running'] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(app.background_value('slow', load, {}), {'ready': True})

    def test_loader_failure_retains_last_value_and_releases_flight(self):
        app = gui_server.App(open_browser=False)
        with patch.object(app, 'log'):
            app.background_value('bad', lambda: 1 / 0, {'unknown': True})
            deadline = time.monotonic() + 1
            while app._background['bad']['running'] and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertFalse(app._background['bad']['running'])
        self.assertEqual(app._background['bad']['value'], {'unknown': True})
        status = app.collection_status()['bad']
        self.assertTrue(status['stale'])
        self.assertIn('ZeroDivisionError', status['error'])
        self.assertEqual(status['read_at'], '')

    def test_frontend_task_behavior(self):
        node = client.PROJECT / '.runtime/node/node.exe'
        result = subprocess.run([str(node), 'scripts/test_gui_frontend.js'], cwd=client.PROJECT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frontend_form_behavior(self):
        node = client.PROJECT / '.runtime/node/node.exe'
        result = subprocess.run([str(node), 'scripts/test_gui_forms.js'], cwd=client.PROJECT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_soft_read_failure_is_stale_and_retains_last_success(self):
        app = gui_server.App(open_browser=False)
        app.publish_background('update', {'mode': 'manual', 'task': {'installed': False}})
        read_at = app.collection_status()['update']['read_at']
        app.invalidate_background('update')
        app.background_value('update', lambda: {'task': {'error': 'fixture'}}, {})
        self.wait_idle(app, 'update')
        self.assertEqual(app._background['update']['value']['mode'], 'manual')
        status = app.collection_status()['update']
        self.assertTrue(status['stale'])
        self.assertEqual(status['read_at'], read_at)
        self.assertIn('计划任务状态读取失败', status['error'])
