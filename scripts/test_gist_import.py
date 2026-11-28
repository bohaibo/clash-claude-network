"""Offline tests for Secret Gist import, topology validation, lifecycle, and GUI APIs."""
import copy
import io
import json
import subprocess
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import baseline
import client
import config_health
import gist_import
import gui_server
import managed_chain
import mobile_export
import test_snapshots
import update


class _FakeResponse:
    def __init__(self, body: bytes):
        self._stream = io.BytesIO(body)

    def read(self, n=-1):
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GistImportTests(unittest.TestCase):
    def setUp(self):
        test_snapshots.Snapshots.setUp(self)
        self.source_root = self.local / 'source-client'
        (self.source_root / 'profiles').mkdir(parents=True)
        self.target_root = self.root
        (self.target_root / 'profiles').mkdir(parents=True)

        self.source_index = {
            'current': 'exit',
            'items': [
                {'uid': 'exit', 'type': 'remote', 'name': 'Exit SG', 'file': 'exit.yaml'},
                {'uid': 'front', 'type': 'remote', 'name': 'Entry HK', 'file': 'front.yaml'},
            ],
        }
        self.exit_profile = {
            'proxies': [{'name': 'SG-Exit-01', 'type': 'vless', 'server': '198.51.100.10',
                         'port': 443, 'uuid': 'secret-uuid-123', 'tls': True, 'servername': 'sni.example.invalid'}],
            'proxy-groups': [],
            'rule-providers': {},
            'rules': ['MATCH,DIRECT'],
        }
        self.front_profile = {
            'proxies': [
                {'name': 'HK-Entry-01', 'type': 'trojan', 'server': 'entry1.example.invalid',
                 'port': 443, 'password': 'secret-front-password', 'sni': 'entry1.example.invalid'},
                {'name': 'HK-Entry-02', 'type': 'trojan', 'server': 'entry2.example.invalid',
                 'port': 8443, 'password': 'secret-front-password-2', 'sni': 'entry2.example.invalid'},
            ],
            'proxy-groups': [
                {'name': 'Auto-Front', 'type': 'url-test', 'proxies': ['HK-Entry-01', 'HK-Entry-02'],
                 'url': 'https://cp.cloudflare.com/generate_204', 'interval': 300, 'tolerance': 50},
            ],
            'dns': {'proxy-server-nameserver': ['https://resolver.example.invalid/dns-query']},
        }
        for root_dir in (self.source_root, self.target_root):
            for name in ('geosite.dat', 'geoip.dat'):
                (root_dir / name).write_bytes(b'offline fixture')
            for path, value in [('clash-verge.yaml', {}), ('config.yaml', {}), ('verge.yaml', {})]:
                (root_dir / path).write_text(client.yaml_dumps(value, allow_unicode=True, sort_keys=False), encoding='utf-8')
        for path, value in [('profiles.yaml', self.source_index),
                            ('profiles/exit.yaml', self.exit_profile),
                            ('profiles/front.yaml', self.front_profile)]:
            (self.source_root / path).write_text(client.yaml_dumps(value, allow_unicode=True, sort_keys=False), encoding='utf-8')
        (self.target_root / 'profiles.yaml').write_text(client.yaml_dumps({'items': None}, allow_unicode=True, sort_keys=False), encoding='utf-8')

        self.data = {'domains': ['+.cn'], 'ips': ['1.0.1.0/24'], 'privateDomains': ['+.lan'], 'lanCidrs': ['10.0.0.0/8']}
        self.data['ruleSources'] = {k: {'url': v, 'sha256': 'f' * 64, 'count': 1} for k, v in client.SOURCES.items()}
        self.node_bin = str(client.PROJECT / '.runtime/node/node.exe')
        self.core = 'offline-core.exe'
        self.enterContext(patch.object(client, 'executables', return_value=(self.node_bin, self.core)))
        self.enterContext(patch.object(client, 'download_rules', side_effect=lambda: copy.deepcopy(self.data)))
        real = subprocess.run

        def run(args, *a, **kw):
            if args[0] == self.core:
                return subprocess.CompletedProcess(args, 0, stdout=b'offline validation', stderr=b'')
            return real(args, *a, **kw)

        self.enterContext(patch.object(client.subprocess, 'run', side_effect=run))

    def _export_android_from_source(self, mode, region='SG'):
        source_local = self.local / 'source-local'
        source_local.mkdir(exist_ok=True)
        with patch.object(client, 'LOCAL', source_local), \
             patch.object(client, 'SNAPSHOTS', source_local / 'snapshots'), \
             patch.object(baseline, 'FINAL', source_local):
            if mode == 'single':
                client.prepare(self.source_root, 'SG-Exit-01', 'exit', region)
            elif mode == 'chain':
                req = {'name': 'Chain-SG', 'front_subscription': 'front', 'front_node': 'HK-Entry-01',
                       'exit_subscription': 'exit', 'exit_node': 'SG-Exit-01'}
                client.prepare(self.source_root, 'SG-Exit-01', 'exit', region, chain_spec=req)
            elif mode == 'chain_auto':
                req = {'name': 'AutoChain-SG', 'front_subscription': 'front', 'front_node': 'Auto-Front',
                       'front_group': True, 'exit_subscription': 'exit', 'exit_node': 'SG-Exit-01'}
                client.prepare(self.source_root, 'SG-Exit-01', 'exit', region, chain_spec=req)
            elif mode == 'static_chain':
                req = {'name': 'StaticChain-US', 'front_subscription': 'front', 'front_node': 'HK-Entry-01',
                       'static_exit': {'protocol': 'socks5', 'ip': '93.184.215.1', 'port': 1080,
                                       'username': 'static-user', 'password': 'static-password'}}
                client.prepare(self.source_root, 'HK-Entry-01', 'front', region, chain_spec=req)
            elif mode == 'static_direct':
                req = {'name': 'StaticDirect-US', 'direct': True,
                       'static_exit': {'protocol': 'https', 'ip': '93.184.215.2', 'port': 8443,
                                       'username': 'direct-user', 'password': 'direct-password'}}
                client.prepare(self.source_root, 'StaticDirect-US', None, region, chain_spec=req)
            with patch.object(client, 'api', return_value={}):
                client.apply_client()
            plan = client.load(source_local / 'plan.json')
            dest = source_local / ('out-' + mode)
            dest.mkdir(exist_ok=True)
            exported = mobile_export.export(plan, source_local, destination=dest, platforms={'android'})
            export_id = exported['android'].split('/')[3]
            return (dest / export_id / mobile_export.FILES['android']).read_bytes()

    def _update_api(self, root, method, path, body=None):
        if method == 'PUT':
            return {}
        if path == '/configs':
            return {'mode': 'rule', 'find-process-mode': 'strict'}
        if path == '/rules':
            return {'rules': [
                {'type': 'GeoSite', 'payload': 'geolocation-!cn', 'proxy': client.GROUP},
                {'type': 'RuleSet', 'payload': 'strict-cn-domain', 'proxy': 'DIRECT'},
            ]}
        if path == '/providers/rules':
            return {'providers': {k: {'ruleCount': len(v['payload'])}
                                  for k, v in client.load(self.target_root / 'clash-verge.yaml')['rule-providers'].items()}}
        if path == '/proxies':
            runtime = client.load(self.target_root / 'clash-verge.yaml')
            marker = runtime['x-portable-chain']
            node = marker['name']
            proxies = {client.GROUP: {'all': [node], 'now': node}, node: {'udp': False}}
            if not marker.get('direct'):
                proxies[node]['dialer-proxy'] = managed_chain.FRONT
                proxies[managed_chain.FRONT] = {'dialer-proxy': ''}
                if marker.get('front_group'):
                    policy = next(g for g in runtime['proxy-groups'] if g['name'] == managed_chain.FRONT)
                    members = policy['proxies']
                    proxies.update({member: {} for member in members})
                    proxies[managed_chain.FRONT].update(
                        type=managed_chain.AUTO_TYPES[policy['type']],
                        all=members, now=members[0], emptyFallback='REJECT', testUrl=policy['url'], fixed='',
                    )
            return {'proxies': proxies}
        raise AssertionError('Unexpected offline controller request: ' + str(path))

    def test_url_parsing_and_security_rejections(self):
        gid = 'a' * 32
        commit = 'b' * 40
        expected_raw = f'https://gist.githubusercontent.com/octo-user/{gid}/raw/android-clash-meta.yaml'
        for valid in (
            f'https://gist.github.com/octo-user/{gid}',
            f'https://gist.github.com/octo-user/{gid}/#file-android-clash-meta-yaml',
            f'https://gist.githubusercontent.com/octo-user/{gid}/raw',
            f'https://gist.githubusercontent.com/octo-user/{gid}/raw/',
            f'https://gist.githubusercontent.com/octo-user/{gid}/raw/{commit}/android-clash-meta.yaml',
            f'https://gist.githubusercontent.com/octo-user/{gid}/raw/android-clash-meta.yaml',
        ):
            parsed = gist_import.parse_gist_url(valid)
            self.assertEqual(parsed['raw_url'], expected_raw)
            self.assertEqual(parsed['canonical_url'], f'https://gist.github.com/octo-user/{gid}')
            self.assertEqual(parsed['gist_hint'], f'octo-user/…{gid[-6:]}')

        for bad in (
            '',
            'http://gist.github.com/octo-user/' + gid,
            'https://example.invalid/octo-user/' + gid,
            'https://gist.github.com/octo-user/' + gid + '?secret=1',
            'https://gist.githubusercontent.com/octo-user/' + gid + '/raw/ios-shadowrocket.conf',
            'https://gist.github.com/octo-user/' + gid + '#file-ios-shadowrocket-conf',
            'https://gist.githubusercontent.com/octo-user/' + gid + '/raw/other.yaml',
            'https://user:pass@gist.github.com/octo-user/' + gid,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(gist_import.GistImportError) as ctx:
                    gist_import.parse_gist_url(bad)
                self.assertNotIn(gid, str(ctx.exception))

    def test_fetch_rejects_cross_host_redirect_oversized_and_404_without_leaking_url(self):
        raw_url = f'https://gist.githubusercontent.com/octo/{"c" * 32}/raw/android-clash-meta.yaml'
        handler = gist_import._SafeGistRedirectHandler()
        req = patch.object(gist_import.urllib.request, 'Request').start()
        self.addCleanup(patch.stopall)
        fake_req = type('Req', (), {'full_url': raw_url})()
        with self.assertRaises(gist_import.GistImportError):
            handler.redirect_request(fake_req, None, 302, 'Found', {}, 'https://evil.example.invalid/payload.yaml')
        with self.assertRaises(gist_import.GistImportError):
            handler.redirect_request(fake_req, None, 302, 'Found', {}, 'http://gist.githubusercontent.com/other')

        oversized_opener = type('Opener', (), {'open': lambda self, r, timeout=15: _FakeResponse(b'x' * (gist_import.MAX_BYTES + 5))})()
        with patch.object(gist_import.urllib.request, 'build_opener', return_value=oversized_opener):
            with self.assertRaises(gist_import.GistImportError) as exc:
                gist_import.fetch_gist_bytes(raw_url)
            self.assertNotIn('c' * 32, str(exc.exception))

        def raise_404(r, timeout=15):
            raise urllib.error.HTTPError(raw_url, 404, 'Not Found', {}, None)

        not_found_opener = type('Opener', (), {'open': lambda self, r, timeout=15: raise_404(r, timeout)})()
        with patch.object(gist_import.urllib.request, 'build_opener', return_value=not_found_opener):
            with self.assertRaises(gist_import.GistImportError) as exc:
                gist_import.fetch_gist_bytes(raw_url)
            self.assertIn('缺少 android-clash-meta.yaml', str(exc.exception))
            self.assertNotIn('c' * 32, str(exc.exception))

        # Verify local unauthenticated Clash proxy fallback works when direct fails
        seen_proxies = []

        def open_with_fallback(proxy_handler, redirect_handler):
            proxy = proxy_handler.proxies

            class _Opener:
                def open(self, req, timeout=15):
                    seen_proxies.append(proxy)
                    if not proxy:
                        raise OSError('direct blocked')
                    return _FakeResponse(b'ok')

            return _Opener()

        (self.target_root / 'clash-verge.yaml').write_text(
            client.yaml_dumps({'mixed-port': 7897}, allow_unicode=True), encoding='utf-8')
        with patch.object(client, 'root_path', return_value=self.target_root), \
             patch.object(gist_import.urllib.request, 'ProxyHandler', side_effect=lambda p: type('PH', (), {'proxies': p})()), \
             patch.object(gist_import.urllib.request, 'build_opener', side_effect=open_with_fallback):
            self.assertEqual(gist_import.fetch_gist_bytes(raw_url), b'ok')
        self.assertIn({'https': 'http://127.0.0.1:7897'}, seen_proxies)

    def test_roundtrip_all_five_topologies_rebuild_full_windows_protections(self):
        cases = [
            ('single', 'SG', 'single', False),
            ('chain', 'SG', 'chain', False),
            ('chain_auto', 'SG', 'chain_auto', False),
            ('static_chain', 'US', 'static_chain', True),
            ('static_direct', 'US', 'static_direct', True),
        ]
        for mode, region, expected_topology, expected_udp in cases:
            with self.subTest(mode=mode):
                raw = self._export_android_from_source(mode, region=region)
                self.assertTrue(raw.decode('utf-8').startswith(f'# ccn-region: {region}\n'))
                spec, hint = gist_import.parse_gist_profile(raw)
                self.assertEqual(spec['topology'], expected_topology)
                self.assertEqual(hint, {'code': region, 'source': 'gist'})
                summary = gist_import.summarize(spec, region_hint=hint, gist_hint='octo/…aaaaaa')
                self.assertEqual(summary['udp_blocked'], expected_udp)
                serialized_summary = json.dumps(summary, ensure_ascii=False)
                for secret in ('secret-uuid-123', 'secret-front-password', 'static-password', 'direct-password'):
                    self.assertNotIn(secret, serialized_summary)

                # Test compatibility with older Gist without # ccn-region header
                without_header = raw.split(b'\n', 1)[1]
                _, legacy_hint = gist_import.parse_gist_profile(without_header)
                if mode in ('single', 'chain', 'chain_auto'):
                    self.assertEqual(legacy_hint, {'code': 'SG', 'source': 'name'})

                # Run full prepare -> validate -> apply_client -> inspect -> update -> restore on target PC
                target_local = self.local / ('target-' + mode)
                target_local.mkdir(exist_ok=True)
                target_root = self.local / ('target-root-' + mode)
                (target_root / 'profiles').mkdir(parents=True)
                for name in ('geosite.dat', 'geoip.dat'):
                    (target_root / name).write_bytes(b'offline fixture')
                for path, value in [('profiles.yaml', {'items': None}), ('clash-verge.yaml', {}), ('config.yaml', {}), ('verge.yaml', {})]:
                    (target_root / path).write_text(client.yaml_dumps(value, allow_unicode=True, sort_keys=False), encoding='utf-8')
                gist_url = f'https://gist.github.com/octo/{"a" * 32}'
                request = {'name': 'Imported-' + mode, 'gist_import': True, 'spec': spec,
                           'gist_url': gist_url, 'gist_hint': 'octo/…aaaaaa'}
                with patch.object(client, 'LOCAL', target_local), \
                     patch.object(client, 'SNAPSHOTS', target_local / 'snapshots'), \
                     patch.object(baseline, 'FINAL', target_local), \
                     patch.object(update, 'LOCAL', target_local), \
                     patch.object(update, 'download_rules', side_effect=lambda: copy.deepcopy(self.data)), \
                     patch.object(update, 'api', side_effect=self._update_api), \
                     patch.object(client, 'api', return_value={}):
                    self.target_root = target_root
                    client.prepare(target_root, request['name'], None, region, chain_spec=request)
                    candidate = client.load(target_local / 'candidate.yaml')
                    # Verify Windows-only protections are rebuilt locally
                    self.assertEqual(candidate['find-process-mode'], 'strict')
                    self.assertTrue(candidate['tun']['enable'])
                    self.assertTrue(candidate['tun']['strict-route'])
                    self.assertEqual(set(candidate['tun']['dns-hijack']), {'any:53', 'tcp://any:53'})
                    self.assertTrue(candidate['sniffer']['enable'])
                    self.assertEqual(candidate['dns']['listen'], '127.0.0.1:5334')
                    self.assertTrue(any(r.startswith('PROCESS-NAME,') for r in candidate['rules']))
                    if expected_udp:
                        self.assertIn('NETWORK,udp,REJECT', candidate['rules'])
                    else:
                        self.assertNotIn('NETWORK,udp,REJECT', candidate['rules'])
                    if mode in ('chain', 'chain_auto', 'static_chain'):
                        self.assertIn('resolver.example.invalid',
                                      json.dumps(candidate['dns']['proxy-server-nameserver-policy']))

                    client.apply_client()
                    plan = client.load(target_local / 'plan.json')
                    uid = plan['subscription_uid']
                    self.assertEqual(gist_import.saved_source(target_local, uid)['url'], gist_url)
                    self.assertNotIn('secret', json.dumps(client.discover(target_root)))
                    self.assertNotIn('password', json.dumps(client.discover(target_root)))

                    # Config health & rule update checks
                    current = {'ipv6_enabled_bindings': 0, 'ipv6': '255',
                               'country_code': region,
                               'culture': 'zh-SG' if region == 'SG' else 'en-US',
                               'timezone': 'Singapore Standard Time' if region == 'SG' else 'Eastern Standard Time'}
                    health = config_health.inspect(client.load(target_root / 'clash-verge.yaml'), current, plan)
                    self.assertTrue(health['success'], health)
                    self.assertTrue(update.update()['unchanged'])
                    self.data['domains'].append(f'+.new-cn-{mode}.cn')
                    self.assertTrue(update.update()['active_profile_reloaded'])

                    # Restore keeps local imported source untouched and detaches wrapper
                    (Path(plan['snapshot']) / 'system.json').write_text('{}', encoding='utf-8')
                    client.seal()
                    self.assertTrue(client.restore_client()['client_restored'])
                    restored_profiles = client.load(target_root / 'profiles.yaml')
                    entry = next(i for i in restored_profiles['items'] if i['uid'] == uid)
                    self.assertNotIn('script', entry.get('option', {}))
                    self.assertTrue((target_root / 'profiles' / plan['chain']['file']).is_file())

    def test_refetch_updates_existing_profile_in_place_and_rejects_tampering(self):
        raw1 = self._export_android_from_source('single', region='SG')
        spec1, _ = gist_import.parse_gist_profile(raw1)
        gist_url = f'https://gist.github.com/octo/{"d" * 32}'
        with patch.object(client, 'api', return_value={}):
            client.prepare(self.target_root, 'MyImported', None, 'SG',
                           chain_spec={'name': 'MyImported', 'gist_import': True, 'spec': spec1,
                                       'gist_url': gist_url, 'gist_hint': 'octo/…dddddd'})
            client.apply_client()
        plan1 = client.load(self.local / 'plan.json')
        uid = plan1['subscription_uid']

        # Source switched node to a 2-hop chain and updated the same Gist
        raw2 = self._export_android_from_source('chain', region='JP')
        spec2, hint2 = gist_import.parse_gist_profile(raw2)
        self.assertEqual(hint2, {'code': 'JP', 'source': 'gist'})
        with patch.object(client, 'api', return_value={}):
            client.prepare(self.target_root, 'MyImported-JP', uid, 'JP',
                           chain_spec={'name': 'MyImported-JP', 'gist_import': True, 'spec': spec2,
                                       'gist_url': gist_url, 'gist_hint': 'octo/…dddddd', 'replace_uid': uid})
            client.apply_client()
        plan2 = client.load(self.local / 'plan.json')
        self.assertEqual(plan2['subscription_uid'], uid)
        self.assertEqual(plan2['chain']['file'], plan1['chain']['file'])
        self.assertEqual(plan2['node'], 'MyImported-JP')
        self.assertEqual(client.load(self.target_root / 'clash-verge.yaml')['proxies'][0]['dialer-proxy'],
                         managed_chain.FRONT)

        # Manual tampering with the managed YAML must stop subsequent operations
        (self.target_root / 'profiles' / plan2['chain']['file']).write_text('mode: direct\n', encoding='utf-8')
        with self.assertRaises(AssertionError):
            client.prepare(self.target_root, 'MyImported-JP', uid, 'JP')

    def test_malicious_gist_yaml_mutations_are_rejected(self):
        base_raw = self._export_android_from_source('chain', region='SG').decode('utf-8')
        base_doc = client.yaml_loads(base_raw)
        mutations = [
            lambda d: d['proxy-groups'][-1]['proxies'].append('DIRECT'),
            lambda d: d['proxies'][1].__setitem__('dialer-proxy', 'SG-Exit-01'),
            lambda d: d.__setitem__('mode', 'direct'),
            lambda d: d.__setitem__('listeners', [{'type': 'http', 'listen': '127.0.0.1', 'port': 8080, 'proxy': 'DIRECT'}]),
            lambda d: d['dns']['proxy-server-nameserver-policy'].__setitem__('entry1.example.invalid', ['http://insecure.invalid/dns']),
            lambda d: d['proxies'].append(copy.deepcopy(d['proxies'][0])),
        ]
        for mutate in mutations:
            doc = copy.deepcopy(base_doc)
            mutate(doc)
            with self.assertRaises(gist_import.GistImportError):
                gist_import.parse_gist_profile(client.yaml_dumps(doc, allow_unicode=True).encode('utf-8'))

        # Static IP missing UDP guard must be rejected
        static_doc = client.yaml_loads(self._export_android_from_source('static_direct', region='US').decode('utf-8'))
        static_doc['rules'] = [r for r in static_doc['rules'] if not r.startswith('AND,')]
        with self.assertRaises(gist_import.GistImportError):
            gist_import.parse_gist_profile(client.yaml_dumps(static_doc, allow_unicode=True).encode('utf-8'))

    def test_gui_server_preview_and_job_staging_hide_credentials_and_gist_url(self):
        raw = self._export_android_from_source('static_chain', region='US')
        gist_url = f'https://gist.github.com/octo/{"e" * 32}'
        app = gui_server.App(local=self.local, open_browser=False, idle_exit=False)
        with patch.object(gist_import, 'fetch_gist_bytes', return_value=raw):
            preview = app.preview_gist_import({'gist_url': gist_url})
        self.assertEqual(preview['summary']['topology'], 'static_chain')
        self.assertEqual(preview['summary']['region_hint'], {'code': 'US', 'source': 'gist'})
        self.assertNotIn('e' * 32, json.dumps(preview))
        self.assertNotIn('static-password', json.dumps(preview))

        payload = {
            'action': 'apply', 'confirm': 'APPLY', 'node': 'OfficeStatic', 'subscription': '', 'region': 'US',
            'gist_import': {'token': preview['token'], 'name': 'OfficeStatic'},
        }
        with patch.object(app, 'configured', return_value=(False, None)), \
             patch.object(app, 'plan', return_value={}), \
             patch.object(gui_server.threading.Thread, 'start'):
            view = app.start_job(payload)
        folder = app.jobs_dir / view['id']
        job_text = (folder / 'job.json').read_text(encoding='utf-8')
        self.assertNotIn('e' * 32, job_text)
        self.assertNotIn('static-password', job_text)
        self.assertNotIn('static-password', json.dumps(view))
        self.assertTrue((folder / 'chain-selection.json').is_file())
        command, _ = app._job_command('apply', app.active_job['params'], folder)
        self.assertIn(str(folder / 'chain-selection.json'), command)

        # Saved-source preview and /api/nodes masking
        uid = 'ManagedChain111111111111'
        (self.target_root / 'profiles.yaml').write_text(
            client.yaml_dumps({'items': [{'uid': uid, 'type': 'local', 'name': 'OfficeStatic', 'file': uid + '.yaml'}]},
                              allow_unicode=True), encoding='utf-8')
        gist_import.save_source_mapping(self.local, uid, gist_url, 'octo/…eeeeee')
        with open(self.local / managed_chain.REGISTRY, 'w', encoding='utf-8') as f:
            json.dump({uid: {'root': str(self.target_root), 'uid': uid, 'file': uid + '.yaml', 'node': 'OfficeStatic',
                             'name': 'OfficeStatic', 'gist_import': True, 'topology': 'static_chain',
                             'direct': False, 'front_node': 'Chain-Front-Pinned', 'gist_hint': 'octo/…eeeeee',
                             'static_exit': {'protocol': 'socks5', 'ip': '93.184.215.1', 'port': 1080}}}, f)
        with patch.object(client, 'LOCAL', self.local), \
             patch.object(app, 'plan', return_value={'root': str(self.target_root)}), \
             patch.object(gist_import, 'fetch_gist_bytes', return_value=raw):
            refetch = app.preview_gist_import({'saved_uid': uid})
        self.assertEqual(refetch['replace_uid'], uid)
        with patch.object(gui_server.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, stdout=json.dumps({
                              'subscriptions': [{'uid': uid, 'name': 'OfficeStatic', 'nodes': ['OfficeStatic'],
                                                 'chain': {'name': 'OfficeStatic', 'gist_import': True,
                                                           'topology': 'static_chain', 'front_node': 'Chain-Front-Pinned',
                                                           'gist_hint': 'octo/…eeeeee',
                                                           'static_exit': {'protocol': 'socks5', 'ip': '93.184.215.1', 'port': 1080}}}],
                              'active_uid': '',
                          }), stderr='')):
            nodes_resp = app.nodes(refresh=True)
        row = nodes_resp['subscriptions'][0]
        self.assertTrue(row['chain']['has_saved_gist'])
        self.assertNotIn('e' * 32, json.dumps(nodes_resp))

        # Expired staged token must return 409
        staged_file = self.local / gist_import.STAGING_DIR / (preview['token'] + '.json')
        record = json.loads(staged_file.read_text(encoding='utf-8'))
        record['created'] = time.time() - gist_import.STAGING_TTL - 10
        staged_file.write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaises(gui_server.ApiError) as err:
            app._validate_job(payload)
        self.assertEqual(err.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
