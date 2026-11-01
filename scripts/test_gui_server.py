"""Loopback GUI auth/CSRF/job-record tests; no live client or system mutation."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_server
import sandbox_browser


class GuiServer(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='gui-server-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.gui = self.local / 'gui-site'
        self.gui.mkdir()
        (self.gui / 'index.html').write_text('<!doctype html><title>Test GUI</title><p>ok</p>', encoding='utf-8')
        (self.gui / 'app.css').write_text('body{}', encoding='utf-8')
        self.app = gui_server.App(local=self.local, gui_dir=self.gui, open_browser=False, idle_exit=False)
        self.app.app_state = lambda: {'ok': True, 'configured': False}
        self.app.task_status = lambda: {'installed': False}
        self.app.service_status = lambda: {'service': 'Unknown'}
        self.close_patch = patch.object(sandbox_browser, 'close', return_value={'closed': 0})
        self.close_patch.start()
        self.addCleanup(self.close_patch.stop)
        self.thread = threading.Thread(target=self.app.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 8
        while self.app.httpd is None and time.time() < deadline:
            time.sleep(0.05)
        if self.app.httpd is None:
            self.fail('GUI server did not start')
        self.port = self.app.httpd.server_address[1]

    def tearDown(self):
        self.app.stop()
        self.thread.join(timeout=8)

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=6)
        values = dict(headers or {})
        if body is not None and 'Content-Type' not in values:
            values['Content-Type'] = 'application/json'
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def token_headers(self, origin=None):
        headers = {'X-CCN-Token': self.app.token}
        if origin:
            headers['Origin'] = origin
        return headers

    def session_cookie(self):
        status, headers, _ = self.request('GET', '/?t=' + self.app.token)
        self.assertEqual(status, 303)
        cookie = headers.get('Set-Cookie', '')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        return cookie.split(';', 1)[0]

    def test_unauthenticated_root_is_rejected(self):
        status, _, data = self.request('GET', '/')
        self.assertEqual(status, 403)
        self.assertIn(b'error', data)

    def test_token_sets_session_and_serves_html(self):
        cookie = self.session_cookie()
        status, headers, data = self.request('GET', '/', {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIn(b'Test GUI', data)
        self.assertIn('Content-Security-Policy', headers)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])

    def test_post_requires_session_and_token(self):
        status, _, _ = self.request('POST', '/api/heartbeat', {'Content-Type': 'application/json'}, '{}')
        self.assertEqual(status, 403)
        cookie = self.session_cookie()
        status, _, _ = self.request('POST', '/api/heartbeat',
                                    {'Content-Type': 'application/json', 'Cookie': cookie}, '{}')
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/heartbeat',
                                    {'Content-Type': 'application/json', 'Cookie': cookie, **self.token_headers()}, '{}')
        self.assertEqual(status, 200)

    def test_cross_site_origin_is_rejected(self):
        cookie = self.session_cookie()
        status, _, _ = self.request('POST', '/api/heartbeat',
                                    {'Content-Type': 'application/json', 'Cookie': cookie,
                                     **self.token_headers('https://evil.example')}, '{}')
        self.assertEqual(status, 403)

    def test_path_traversal_is_not_served(self):
        cookie = self.session_cookie()
        for path in ('/../scripts/run.ps1', '/%2e%2e/scripts/run.ps1', '/js/../../scripts/run.ps1'):
            with self.subTest(path=path):
                status, _, data = self.request('GET', path, {'Cookie': cookie})
                self.assertIn(status, (403, 404))
                self.assertNotIn(b'param(', data)

    def test_state_requires_session(self):
        status, _, _ = self.request('GET', '/api/state')
        self.assertEqual(status, 403)
        cookie = self.session_cookie()
        status, _, data = self.request('GET', '/api/state', {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {'ok': True, 'configured': False})

    def test_validation_rejects_missing_confirmation(self):
        with self.assertRaises(gui_server.ApiError):
            self.app._validate_job({'action': 'apply', 'node': 'Chosen', 'region': 'SG'})
        with self.assertRaises(gui_server.ApiError):
            self.app._validate_job({'action': 'snapshot', 'confirm': 'WRONG'})

    def test_restore_requires_existing_sealed_snapshot(self):
        folder = self.local / 'snapshots' / '20260101-120000'
        folder.mkdir(parents=True)
        (folder / 'system.json').write_text('{}', encoding='utf-8')
        (folder / 'manifest.json').write_text(json.dumps({
            'time': '2026-01-01T12:00:00+08:00', 'kind': 'auto',
            'sha256': {}, 'extra_sha256': {'system.json': 'x'},
        }), encoding='utf-8')
        action, params = self.app._validate_job({'action': 'restore', 'snapshot': folder.name, 'confirm': folder.name})
        self.assertEqual(action, 'restore')
        self.assertEqual(params['snapshot'], folder.name)
        with self.assertRaises(gui_server.ApiError):
            self.app._validate_job({'action': 'restore', 'snapshot': folder.name, 'confirm': 'other'})

    def test_job_finish_records_done_and_cancelled(self):
        job_id = 'a' * 32
        folder = self.local / 'gui' / 'jobs' / job_id
        folder.mkdir(parents=True)
        (folder / 'events.jsonl').write_text('\n'.join([
            json.dumps({'type': 'start', 'action': 'snapshot'}),
            json.dumps({'type': 'step', 'index': 1, 'total': 3, 'title': '采集客户端记录'}),
            json.dumps({'type': 'done', 'message': '已保存'}),
        ]), encoding='utf-8')
        (self.local / 'last-run.json').write_text(json.dumps({'success': True, 'action': 'snapshot'}), encoding='utf-8')
        job = {'id': job_id, 'action': 'snapshot', 'folder': str(folder), 'params': {}, 'created': gui_server.now_iso(),
               'started_epoch': time.time() - 10, 'state': 'running'}
        self.app._finish_job(job, 0)
        status = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'], 'success')
        self.assertTrue(status['result']['success'])

        cancelled_id = 'b' * 32
        cancelled_folder = self.local / 'gui' / 'jobs' / cancelled_id
        cancelled_folder.mkdir(parents=True)
        (cancelled_folder / 'events.jsonl').write_text('\n'.join([
            json.dumps({'type': 'elevation', 'message': '等待授权'}),
            json.dumps({'type': 'cancelled', 'message': '已取消管理员授权'}),
        ]), encoding='utf-8')
        cancelled = {'id': cancelled_id, 'action': 'apply', 'folder': str(cancelled_folder), 'params': {},
                     'created': gui_server.now_iso(), 'started_epoch': time.time() - 10, 'state': 'running'}
        self.app._finish_job(cancelled, 0)
        status = json.loads((cancelled_folder / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(status['state'], 'cancelled')

    def test_update_settings_persist_and_revert_on_failure(self):
        with patch.object(self.app, '_update_task') as update_task, \
             patch.object(self.app, 'configured', return_value=(True, '')):
            value = self.app.save_update_settings({'mode': 'manual', 'time': '08:00'})
            self.assertEqual(value['mode'], 'manual')
            update_task.assert_called_with('remove')
            self.assertEqual(json.loads((self.local / 'ui-settings.json').read_text(encoding='utf-8'))['update'],
                             {'mode': 'manual', 'time': '08:00', 'days': 1})
            (self.local / 'plan.json').write_text(json.dumps({'node': 'Chosen'}), encoding='utf-8')
            update_task.side_effect = gui_server.ApiError(500, 'fixture')
            with self.assertRaises(gui_server.ApiError):
                self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00'})
            self.assertEqual(json.loads((self.local / 'ui-settings.json').read_text(encoding='utf-8'))['update'],
                             {'mode': 'manual', 'time': '08:00', 'days': 1})

    def test_update_settings_passes_interval_days(self):
        with patch.object(self.app, '_update_task') as update_task, \
             patch.object(self.app, 'configured', return_value=(True, '')):
            value = self.app.save_update_settings({'mode': 'scheduled', 'time': '09:00', 'days': 7})
            self.assertEqual(value['days'], 7)
            update_task.assert_called_with('install', '09:00', 7)

    def test_stuck_job_is_marked_interrupted_and_can_be_cancelled(self):
        job_id = 'c' * 32
        folder = self.app.jobs_dir / job_id
        folder.mkdir(parents=True)
        (folder / 'job.json').write_text(json.dumps({
            'id': job_id, 'action': 'restore', 'params': {'snapshot': '20261018-222521'},
            'created': '2026-11-01T14:30:12+08:00', 'state': 'running',
        }), encoding='utf-8')
        (folder / 'status.json').write_text(json.dumps({
            'state': 'running', 'action': 'restore', 'pid': 999999999,
            'started': '2026-11-01T14:30:12+08:00', 'exit_code': None,
        }), encoding='utf-8')
        (folder / 'events.jsonl').write_text(json.dumps({
            'type': 'error', 'message': 'fixture restore failed', 'step': 4, 'total': 4,
        }) + '\n', encoding='utf-8')
        view = self.app.job_view(job_id)
        self.assertTrue(view['stale'])
        value = self.app.cancel_job(job_id)
        self.assertEqual(value['state'], 'interrupted')
        self.assertIn('手动结束', value['error'])

    def test_completed_apply_is_not_cancelled_while_wrapper_is_running(self):
        job_id = 'd' * 32
        folder = self.app.jobs_dir / job_id
        folder.mkdir(parents=True)
        job = {'id': job_id, 'action': 'apply', 'params': {}, 'folder': str(folder),
               'created': gui_server.now_iso(), 'started_epoch': time.time() - 1, 'state': 'running'}
        self.app.jobs[job_id] = job
        self.app.active_job = job
        (folder / 'status.json').write_text(json.dumps({'state': 'running', 'action': 'apply',
            'started': job['created'], 'pid': 123}), encoding='utf-8')
        (folder / 'events.jsonl').write_text(json.dumps({'type': 'done', 'time': gui_server.now_iso(),
            'message': 'configuration complete'}) + '\n', encoding='utf-8')
        (self.local / 'last-run.json').write_text(json.dumps({'action': 'apply', 'success': True}), encoding='utf-8')
        with patch.object(self.app, '_process_info') as info:
            view = self.app.cancel_job(job_id)
        self.assertEqual(view['state'], 'success')
        self.assertFalse(view['stale'])
        info.assert_not_called()
        self.assertIsNone(self.app.active_job)
        self.assertNotIn('cancelled', (folder / 'events.jsonl').read_text())

    def test_firewall_guard_is_pending_only_without_commit(self):
        token = 'guard-' + 'a' * 32
        (self.local / 'pending-firewall.json').write_text(json.dumps({'token': token}), encoding='utf-8')
        self.assertTrue(self.app.pending_firewall())
        (self.local / (token + '.commit')).write_text('Verified', encoding='utf-8')
        self.assertFalse(self.app.pending_firewall())
        (self.local / 'pending-firewall.json').write_text(json.dumps({'token': '../escape'}), encoding='utf-8')
        self.assertTrue(self.app.pending_firewall())

    def test_configured_scan_reads_only_marker(self):
        root = self.local / 'fake-client'
        (root / 'profiles').mkdir(parents=True)
        (root / 'profiles' / 'a.js').write_text('// PORTABLE-CLAUDE-NETWORK-V1\n', encoding='utf-8')
        (self.local / 'plan.json').write_text(json.dumps({'root': str(root)}), encoding='utf-8')
        self.assertEqual(self.app.configured(), (True, ''))


if __name__ == '__main__':
    unittest.main()
