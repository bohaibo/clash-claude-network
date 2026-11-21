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
import gui_window


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
        self.close_patch = patch.object(gui_window, 'close', return_value={'closed': 0})
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

    def test_drill_retry_uses_only_a_confirmed_failed_record(self):
        identifier = 'c' * 32
        payload = {'action': 'drill-recover', 'recovery': identifier, 'confirm': 'RECOVER-DRILL'}
        with patch.object(gui_server, 'pending_tun', return_value=[{'id': identifier, 'status': 'pending'}]):
            with self.assertRaises(gui_server.ApiError):
                self.app._validate_job(payload)
        with patch.object(gui_server, 'pending_tun', return_value=[{'id': identifier, 'status': 'failed'}]):
            action, params = self.app._validate_job(payload)
            args, _ = self.app._job_command(action, params, self.local)
        self.assertEqual(args[args.index('-RecoveryId') + 1], identifier)
        self.assertIn('-Acknowledged', args)

    def test_verification_revision_survives_snapshot_but_detects_rule_change(self):
        from config_health import configuration_revision
        from verification_reports import save_report
        root = self.local / 'client'
        root.mkdir()
        client.write(root / 'profiles.yaml', {'current': 'one'})
        client.write(root / 'clash-verge.yaml', {'rules': ['first']})
        client.write(self.local / 'plan.json', {'root': str(root)})
        client.write(self.local / 'configuration-applied.json', {'configuration_time': '2026-01-01T00:00:00Z'})
        with patch('verification_reports.LOCAL', self.local):
            save_report({'success': True}, 'connectivity')
        before = configuration_revision(self.local)
        client.write(self.local / 'last-run.json', {'action': 'snapshot', 'success': True})
        self.assertEqual(configuration_revision(self.local), before)
        self.assertTrue(self.app.verification_summary()['configuration_matches_current'])
        client.write(root / 'clash-verge.yaml', {'rules': ['second']})
        self.assertFalse(self.app.verification_summary()['configuration_matches_current'])

    def test_second_instance_reuses_the_bound_server(self):
        another = gui_server.App(local=self.local, gui_dir=self.gui, open_browser=False, idle_exit=False)
        with patch.object(gui_window, 'launch') as launch:
            self.assertEqual(another.run(), 0)
            launch.assert_not_called()
        self.assertIsNone(another.httpd)
        self.assertEqual(json.loads(self.app.session_file.read_text())['port'], self.port)

    def test_open_after_last_window_closes_starts_a_fresh_server(self):
        self.app.frontend_ready = True
        another = gui_server.App(local=self.local, gui_dir=self.gui, open_browser=True, idle_exit=False)
        thread = threading.Thread(target=another.run, daemon=True)
        with patch.object(gui_window, 'browser_window_count', return_value=0), \
             patch.object(gui_window, 'launch') as launch:
            thread.start()
            try:
                deadline = time.monotonic() + 8
                while not another.browser_opened and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(another.browser_opened)
                self.assertTrue(self.app.stopping)
                launch.assert_called_once_with(another.session_url(), local=self.local, gui=True, app=True)
            finally:
                another.stop()
                thread.join(8)

    def test_open_with_active_operation_reuses_server_even_without_a_window(self):
        self.app.frontend_ready = True
        another = gui_server.App(local=self.local, gui_dir=self.gui, open_browser=True, idle_exit=False)
        with self.app.settings_operation(), patch.object(gui_window, 'browser_window_count', return_value=0), \
             patch.object(gui_window, 'launch') as launch:
            self.assertEqual(another.run(), 0)
        self.assertFalse(self.app.stopping)
        launch.assert_called_once_with(self.app.session_url(), local=self.local, gui=True, app=True)

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

    def test_snapshot_delete_auth_confirmation_protection_and_busy(self):
        first = self.local / 'snapshots' / 'first'
        old = self.local / 'snapshots' / 'old'
        for folder in (first, old):
            folder.mkdir(parents=True)
            (folder / 'manifest.json').write_text('{}', encoding='utf-8')
        (self.local / 'baseline-selection.json').write_text('{"snapshot":"first"}', encoding='utf-8')
        cookie = self.session_cookie()
        headers = {'Cookie': cookie, **self.token_headers()}
        body = json.dumps({'snapshot': 'old', 'confirm': 'old'})
        self.assertEqual(self.request('POST', '/api/snapshot-delete', {'Cookie': cookie}, body)[0], 403)
        self.assertEqual(self.request('POST', '/api/snapshot-delete', headers, '{"snapshot":"old"}')[0], 400)
        self.app._settings_busy = True
        self.assertEqual(self.request('POST', '/api/snapshot-delete', headers, body)[0], 409)
        self.app._settings_busy = False
        self.assertEqual(self.request('POST', '/api/snapshot-delete', headers,
                                     '{"snapshot":"first","confirm":"first"}')[0], 409)
        rows = {row['name']: row for row in self.app.snapshots()}
        self.assertTrue(rows['first']['baseline'])
        self.assertTrue(rows['first']['delete_reason'])
        self.assertFalse(rows['old']['delete_reason'])
        self.assertEqual(self.request('POST', '/api/snapshot-delete', headers, body)[0], 200)
        self.assertFalse(old.exists())
        self.assertTrue(first.exists())

    def test_software_check_is_authenticated_and_does_not_block_response(self):
        cookie = self.session_cookie()
        with patch.object(self.app, 'request_software_check', return_value={'checking': True}) as check:
            status, _, _ = self.request('POST', '/api/software-check', {'Cookie': cookie}, '{}')
            self.assertEqual(status, 403)
            check.assert_not_called()
            status, _, data = self.request('POST', '/api/software-check',
                                          {'Cookie': cookie, **self.token_headers()}, '{}')
            self.assertEqual(status, 202)
            self.assertTrue(json.loads(data)['checking'])
            check.assert_called_once_with()
        with patch.object(self.app.release_checker, 'check') as check:
            status, _, data = self.request('POST', '/api/software-check',
                                          {'Cookie': cookie, **self.token_headers()}, '{"wait":true}')
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(data)['checking'])
            check.assert_called_once_with(force=True)

    def test_clean_package_download_requires_session_and_returns_verified_zip(self):
        import hashlib, io, zipfile
        cookie = self.session_cookie()
        status, _, data = self.request('POST', '/api/package',
                                      {'Cookie': cookie, **self.token_headers()}, '{}')
        self.assertEqual(status, 200, data)
        package = json.loads(data)['package']
        self.assertEqual(self.request('GET', package['download_url'])[0], 403)
        status, headers, raw = self.request('GET', package['download_url'], {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), package['sha256'])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertFalse(any('/.local/' in name or '/.runtime/' in name for name in archive.namelist()))

    def test_mobile_export_keeps_fixed_route_and_requires_local_session(self):
        from config_health import GROUP
        from safe_yaml import dumps, loads

        def field(number, value):
            return bytes([(number << 3) | 2, len(value)]) + value

        def geo(code, *entries):
            return field(1, field(1, code) + b''.join(field(2, entry) for entry in entries))

        root = self.local / 'synthetic-client'
        root.mkdir()
        config_path = root / 'clash-verge.yaml'
        index_path = root / 'profiles.yaml'
        index = {'current': 'my-profile', 'items': [{'uid': 'my-profile', 'option': {'script': 'managed'}}]}
        index_path.write_text(dumps(index), encoding='utf-8')
        client.write(self.local / 'plan.json', {'root': str(root), 'node': 'Pinned',
                     'subscription_uid': 'my-profile', 'script_uid': 'managed'})
        (root / 'geosite.dat').write_bytes(geo(b'geolocation-!cn',
            b'\x08\x02' + field(2, b'overseas.invalid'), b'\x08\x01' + field(2, b'^regex$')))
        (root / 'geoip.dat').write_bytes(geo(b'CN', field(1, bytes([203, 0, 113, 0])) + bytes([16, 24])))
        front = {'name': 'Front', 'type': 'ss', 'server': 'front.invalid', 'port': 443,
                 'cipher': 'aes-128-gcm', 'password': 'fixture-only'}
        exit_node = {'name': 'Pinned', 'type': 'socks5', 'server': 'exit.invalid', 'port': 1080,
                     'dialer-proxy': 'Front', 'udp': False}
        rules = [
            'DOMAIN-SUFFIX,local.invalid,DIRECT',
            'PROCESS-NAME,Fixture.exe,' + GROUP,
            'DOMAIN,protected.invalid,' + GROUP,
            'AND,((NETWORK,udp),(GEOSITE,geolocation-!cn)),REJECT',
            'GEOSITE,geolocation-!cn,' + GROUP,
            'RULE-SET,strict-cn-domain,DIRECT',
            'GEOIP,CN,DIRECT,no-resolve',
            'NETWORK,udp,REJECT',
            'MATCH,' + GROUP,
        ]
        config = {'mode': 'rule', 'secret': 'controller-fixture', 'external-controller': '127.0.0.1:1111',
                  'dns': {'enable': True, 'listen': '127.0.0.1:5334'},
                  'proxies': [exit_node, front, dict(front, name='Unrelated', password='unrelated-fixture')],
                  'proxy-groups': [{'type': 'select', 'name': GROUP, 'proxies': ['Pinned']}],
                  'rule-providers': {'strict-cn-domain': {'type': 'inline', 'behavior': 'domain',
                                                         'payload': ['exact.invalid', '+.suffix.invalid']}},
                  'rules': rules}
        config_path.write_text(dumps(config), encoding='utf-8')
        cookie = self.session_cookie()
        headers = {'Cookie': cookie, **self.token_headers()}
        self.assertEqual(self.request('POST', '/api/mobile-export', {'Cookie': cookie}, '{}')[0], 403)
        status, _, raw = self.request('POST', '/api/mobile-export', headers, '{}')
        self.assertEqual(status, 200)
        exported = json.loads(raw)
        self.assertEqual(exported['omitted_geo_regex'], 1)
        self.assertEqual(exported['ios_manual'], [])
        self.assertNotIn(b'fixture-only', raw)
        self.assertEqual(self.request('GET', exported['android'])[0], 403)
        status, response_headers, raw = self.request('GET', exported['android'], {'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertIn('attachment', response_headers['Content-Disposition'])
        android = loads(raw)
        self.assertEqual(android['proxies'], [front, exit_node])
        self.assertEqual(android['rules'], [rules[0], *rules[2:]])
        self.assertNotIn('secret', android)
        self.assertNotIn('external-controller', android)
        self.assertNotIn('listen', android['dns'])
        status, _, raw = self.request('GET', exported['ios'], {'Cookie': cookie})
        self.assertEqual(status, 200)
        conf = raw.decode('utf-8').splitlines()
        alias = 'CCN-' + gui_server.mobile_export.mobile_scope(self.app.plan())[:12]
        self.assertIn(f'{GROUP} = relay,{alias}-1,{alias}-2', conf)
        self.assertIn('AND,((PROTOCOL,UDP),(DOMAIN-SUFFIX,overseas.invalid)),REJECT', conf)
        self.assertLess(conf.index('DOMAIN,protected.invalid,' + GROUP), conf.index('DOMAIN-SUFFIX,overseas.invalid,' + GROUP))
        self.assertLess(conf.index('DOMAIN-SUFFIX,overseas.invalid,' + GROUP), conf.index('DOMAIN,exact.invalid,DIRECT'))
        self.assertIn('DOMAIN-SUFFIX,suffix.invalid,DIRECT', conf)
        self.assertIn('IP-CIDR,203.0.113.0/24,DIRECT,no-resolve', conf)
        self.assertEqual(conf[-2:], ['PROTOCOL,UDP,REJECT', 'FINAL,' + GROUP])

        front['type'] = 'unsupported-fixture'
        config_path.write_text(dumps(config), encoding='utf-8')
        status, _, raw = self.request('POST', '/api/mobile-export', headers, '{}')
        self.assertEqual(status, 200)
        manual = json.loads(raw)
        self.assertEqual(manual['ios_manual'], [{'name': 'Front', 'alias': alias + '-1'},
                                               {'name': 'Pinned', 'alias': alias + '-2'}])
        self.assertNotIn(b'[Proxy]', self.request('GET', manual['ios'], {'Cookie': cookie})[2])

        exit_node['dialer-proxy'] = 'Automatic'
        automatic = {'name': 'Automatic', 'type': 'url-test', 'proxies': ['Front'],
                     'url': 'https://example.invalid/generate_204', 'empty-fallback': 'REJECT'}
        config['proxy-groups'].append(automatic)
        config_path.write_text(dumps(config), encoding='utf-8')
        status, _, raw = self.request('POST', '/api/mobile-export', headers, '{}')
        self.assertEqual(status, 200)
        exported = json.loads(raw)
        self.assertIn('ios_error', exported)
        self.assertNotIn('ios', exported)
        android = loads(self.request('GET', exported['android'], {'Cookie': cookie})[2])
        self.assertEqual(android['proxy-groups'][0], automatic)
        self.assertEqual(android['proxies'][-1]['dialer-proxy'], 'Automatic')

        index['current'] = 'different-profile'
        index_path.write_text(dumps(index), encoding='utf-8')
        self.assertEqual(self.request('POST', '/api/mobile-export', headers, '{}')[0], 409)

    def test_open_github_gist_checks_auth_and_uses_fixed_windows_url(self):
        endpoint = '/api/open-github-gist'
        cookie = self.session_cookie()
        headers = {'Cookie': cookie, **self.token_headers(self.app.base_url)}
        with patch.object(gui_server.os, 'startfile') as open_url:
            self.assertEqual(self.request('POST', endpoint, {'Cookie': cookie}, '{}')[0], 403)
            bad_origin = dict(headers, Origin='https://example.invalid')
            self.assertEqual(self.request('POST', endpoint, bad_origin, '{}')[0], 403)
            open_url.assert_not_called()
            status, _, body = self.request('POST', endpoint, headers, '{"url":"https://example.invalid/"}')
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['opened'])
            open_url.assert_called_once_with('https://gist.github.com/')
            open_url.side_effect = OSError('offline fixture')
            self.assertEqual(self.request('POST', endpoint, headers, '{}')[0], 500)

    def test_mobile_gist_binds_then_patches_only_named_files_with_mock_cli(self):
        from subprocess import CompletedProcess
        from config_health import configuration_revision
        from mobile_export import FILES

        endpoint = '/api/mobile-gist'
        identifier = 'a' * 32
        plan = {'root': str(self.local), 'subscription_uid': 'synthetic', 'node': 'synthetic-node'}
        client.write(self.local / 'plan.json', plan)
        client.write(self.local / 'profiles.yaml', {'current': 'synthetic'})
        client.write(self.local / 'clash-verge.yaml', {'rules': ['synthetic']})
        self.app.mobile_export_revisions[identifier] = (gui_server.mobile_export.mobile_scope(plan), configuration_revision(self.local))
        folder = self.local / 'mobile-exports' / identifier
        folder.mkdir(parents=True)
        for name in FILES.values():
            (folder / name).write_text('offline fixture for ' + name, encoding='utf-8')
        cookie = self.session_cookie()
        headers = {'Cookie': cookie, **self.token_headers()}
        payload = {'id': identifier, 'platforms': ['android'], 'confirm': True}
        gist_id = 'b' * 32
        gist_url = 'https://gist.github.com/fixture-user/' + gist_id
        gist = {'id': gist_id, 'owner': {'login': 'fixture-user'}, 'public': False,
                'files': {FILES['android']: {'content': 'ignored fixture'}, 'notes.txt': {'content': 'unrelated fixture'}}}

        def respond(body):
            return CompletedProcess([], 0, json.dumps(body).encode(), b'')

        with (patch.object(gui_server.mobile_export.shutil, 'which', return_value='fixture-gh.exe'),
              patch.object(gui_server.mobile_export.subprocess, 'run', side_effect=[
                  respond(gist), respond({'login': 'fixture-user'}), respond(gist), respond(gist),
              ]) as execute):
            self.assertEqual(self.request('POST', endpoint, {'Cookie': cookie}, json.dumps(payload))[0], 403)
            self.assertEqual(self.request('POST', endpoint, headers, json.dumps(dict(payload, confirm=False)))[0], 400)
            self.assertEqual(self.request('POST', endpoint, headers, json.dumps(dict(payload, platforms=['../plan.json'])))[0], 400)
            execute.assert_not_called()
            status, _, raw = self.request('POST', endpoint, headers, json.dumps(payload))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['gist_url'], gist_url)
            self.assertEqual(gui_server.mobile_export.gist_binding(self.local, plan)['url'], gist_url)
            self.assertEqual(self.request('POST', endpoint, headers, json.dumps(payload))[0], 409)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[0][1:], [
                'api', '--hostname', 'github.com', '--method', 'POST', 'gists', '--input', '-'])
            sent = json.loads(execute.call_args.kwargs['input'])
            self.assertIs(sent['public'], False)
            self.assertEqual(sent['files'], {FILES['android']: {'content': 'offline fixture for ' + FILES['android']}})
            self.assertNotIn('GH_TOKEN', execute.call_args.kwargs['env'])
            (folder / FILES['android']).write_text('fresh fixture', encoding='utf-8')
            status, _, raw = self.request('POST', endpoint, headers, json.dumps(dict(payload, gist_url=gist_url)))
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['gist_url'], gist_url)
            self.assertEqual(execute.call_args.args[0][5:7], ['PATCH', 'gists/' + gist_id])
            self.assertEqual(json.loads(execute.call_args.kwargs['input']),
                             {'files': {FILES['android']: {'content': 'fresh fixture'}}})
            client.write(self.local / 'clash-verge.yaml', {'rules': ['updated-during-confirmation']})
            self.assertEqual(self.request('POST', endpoint, headers, json.dumps(dict(payload, gist_url=gist_url)))[0], 409)
            self.assertEqual(execute.call_count, 4)

    def test_sync_old_gist_preserves_shadowrocket_alias_and_rejects_public_target(self):
        from config_health import GROUP
        from mobile_export import FILES, ExportError, sync_secret_gist

        prefix = '1' * 12
        previous = '2' * 12
        gist_id = '3' * 32
        url = 'https://gist.github.com/fixture-user/' + gist_id
        path = self.local / FILES['ios']
        path.write_text(f'# Before using, import the exact corresponding servers as CCN-{prefix}-1, CCN-{prefix}-2\n'
                        f'[Proxy Group]\n{GROUP} = relay,CCN-{prefix}-1,CCN-{prefix}-2\n'
                        f'[Rule]\nFINAL,{GROUP}\n', encoding='utf-8')
        previous_content = f'[Proxy Group]\n{GROUP} = relay,CCN-{previous}-1,CCN-{previous}-2\n'
        gist = {'id': gist_id, 'owner': {'login': 'fixture-user'}, 'public': False,
                'files': {FILES['ios']: {'content': previous_content}}}
        with patch.object(gui_server.mobile_export, '_gist_api') as api:
            api.return_value.side_effect = [{'login': 'fixture-user'}, gist, gist]
            self.assertEqual(sync_secret_gist({'ios': path}, url)['ios_alias'], previous)
            body = api.return_value.call_args.args[2]
            content = body['files'][FILES['ios']]['content']
            self.assertIn(f'relay,CCN-{previous}-1,CCN-{previous}-2', content)
            self.assertIn(f'import the exact corresponding servers as CCN-{previous}-1, CCN-{previous}-2', content)
            api.return_value.reset_mock()
            api.return_value.side_effect = [{'login': 'fixture-user'}, dict(gist, public=True)]
            with self.assertRaises(ExportError):
                sync_secret_gist({'ios': path}, url)
            self.assertEqual(api.return_value.call_count, 2)

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
        with (patch.object(self.app, 'app_state', return_value={'ready': True}) as state,
              patch.object(self.app, 'invalidate_background') as invalidate):
            status, _, data = self.request('GET', '/api/state?wait=1&refresh=1', {'Cookie': cookie})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data), {'ready': True})
            state.assert_called_once_with(wait=True)
            invalidate.assert_called_once_with()

    def test_validation_rejects_missing_confirmation(self):
        with self.assertRaises(gui_server.ApiError):
            self.app._validate_job({'action': 'apply', 'node': 'Chosen', 'region': 'SG'})
        with self.assertRaises(gui_server.ApiError):
            self.app._validate_job({'action': 'snapshot', 'confirm': 'WRONG'})

    def test_first_apply_does_not_require_a_plan(self):
        self.assertFalse((self.local / 'plan.json').exists())
        with patch.object(self.app, 'configured', return_value=(False, '')):
            action, params = self.app._validate_job({
                'action': 'apply', 'confirm': gui_server.CONFIRM['apply'],
                'node': 'Chosen', 'region': 'SG',
            })
            self.assertEqual(action, 'apply')
            self.assertEqual(params['node'], 'Chosen')
            for action in ('supplement', 'reconfigure', 'update', 'verify'):
                with self.subTest(action=action), self.assertRaises(gui_server.ApiError):
                    self.app._validate_job({'action': action, 'confirm': gui_server.CONFIRM[action]})

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

    def test_running_job_and_log_view_include_elevated_console_output(self):
        job_id = 'c' * 32
        folder = self.app.jobs_dir / job_id
        folder.mkdir(parents=True)
        self.app.jobs[job_id] = {'id': job_id, 'action': 'audit', 'folder': str(folder),
                                 'created': gui_server.now_iso(), 'state': 'running'}
        (folder / 'console.log').write_text('等待授权\n', encoding='utf-8')
        self.assertEqual(self.app.job_view(job_id)['log'], '等待授权')
        (folder / 'elevated.log').write_text('正在复核防火墙\n', encoding='utf-8')
        view = self.app.job_view(job_id)
        self.assertEqual(view['state'], 'running')
        self.assertEqual(view['log'], '等待授权\n正在复核防火墙')
        self.assertEqual(self.app.log_text('gui/jobs/' + job_id + '/console.log')['text'], view['log'])

    def test_update_settings_persist_and_revert_on_failure(self):
        def checkpoint(action, *args, state_file=None):
            if action == 'snapshot':
                Path(state_file).write_text('{}', encoding='utf-8')
        with patch.object(self.app, '_update_task', side_effect=checkpoint) as update_task, \
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

    def test_shutdown_is_blocked_while_settings_are_saving(self):
        cookie = self.session_cookie()
        self.app._settings_busy = True
        try:
            status, _, _ = self.request('POST', '/api/shutdown',
                {'Cookie': cookie, **self.token_headers()}, '{}')
            self.assertEqual(status, 409)
            self.assertFalse(self.app.stopping)
        finally:
            self.app._settings_busy = False

    def test_update_settings_passes_interval_days(self):
        def checkpoint(action, *args, state_file=None):
            if action == 'snapshot':
                Path(state_file).write_text('{}', encoding='utf-8')
        with patch.object(self.app, '_update_task', side_effect=checkpoint) as update_task, \
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
        with patch.object(self.app, '_process_info', return_value={'alive': False}):
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
