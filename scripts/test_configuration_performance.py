"""Offline performance-path safety regressions; no browsers or real requests."""
import base64
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import client
import config_health
import gui_server
import performance
import safe_yaml
import yaml


class ConfigurationPerformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = {'domains': ['+.fixture-%d.example.invalid' % i for i in range(10001)],
                'ips': ['11.%d.%d.0/24' % divmod(i, 256) for i in range(8001)],
                'privateDomains': ['+.fixture-%d.lan' % i for i in range(31)],
                'lanCidrs': ['10.0.0.0/8']}
        cls.raw = {key: safe_yaml.dumps({'payload': values}).encode() for key, values in data.items()}

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='performance-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.enterContext(patch.object(client, 'LOCAL', self.local))
        self.enterContext(patch.object(performance, 'LOCAL', self.local))
        self.enterContext(patch.dict(os.environ, CCN_OPERATION_ID='a' * 32, CCN_OPERATION_ACTION='apply'))

    def bundle(self):
        with patch.object(client, 'download_raw_rules', return_value=self.raw):
            token = client.prefetch_rules()['rules_bundle']
        return token, self.local / 'rule-preparations' / (token + '.json')

    def test_safe_codecs_preserve_semantics_and_have_fallback(self):
        document = 'yes: true\nnull_key: null\nnumber: 17\ntext: 中文😀\na: &a [one, two]\nb: *a\n'
        expected = yaml.safe_load(document)
        self.assertEqual(safe_yaml.loads(document), expected)
        self.assertEqual(yaml.safe_load(safe_yaml.dumps(expected, allow_unicode=True)), expected)
        with patch.object(safe_yaml, 'Loader', yaml.SafeLoader), patch.object(safe_yaml, 'Dumper', yaml.SafeDumper):
            self.assertEqual(safe_yaml.loads(safe_yaml.dumps(expected)), expected)
        for loader in (safe_yaml.Loader, yaml.SafeLoader):
            with patch.object(safe_yaml, 'Loader', loader), self.assertRaises(yaml.YAMLError):
                safe_yaml.loads('!!python/object/apply:os.system ["must-never-run"]')
        with self.assertRaises(yaml.YAMLError):
            safe_yaml.loads('payload: [')

    def test_prefetch_consumes_once_without_network_and_keeps_raw_hashes(self):
        token, path = self.bundle()
        record = json.loads(path.read_text())
        with patch.object(client, 'rule_source_bytes', side_effect=AssertionError('network forbidden')):
            data = client.consume_rules(token)
        self.assertEqual(data['ruleSources']['domains']['sha256'], record['sources']['domains']['sha256'])
        self.assertEqual(len(data['domains']), 10001)
        self.assertFalse(path.exists())
        with self.assertRaises(OSError):
            client.consume_rules(token)

    def test_tampering_expiry_generator_and_operation_changes_fail_closed(self):
        for change in ('raw', 'created', 'source_digest', 'operation_id', 'url', 'missing'):
            with self.subTest(change=change):
                token, path = self.bundle()
                record = json.loads(path.read_text())
                if change == 'raw':
                    record['sources']['domains']['raw'] = base64.b64encode(b'payload: []').decode()
                elif change == 'created':
                    record['created'] -= 901
                elif change == 'url':
                    record['sources']['domains']['url'] = 'https://example.invalid/unapproved'
                elif change == 'missing':
                    del record['sources']['ips']
                else:
                    record[change] = 'changed'
                path.write_text(json.dumps(record))
                with patch.object(client, 'download_raw_rules') as download, self.assertRaises(AssertionError):
                    client.consume_rules(token)
                download.assert_not_called()
                self.assertFalse(path.exists())

    def test_invalid_preparation_token_never_reads_external_paths(self):
        for token in ('../outside', '', 'a' * 31, 'B' * 32):
            with self.subTest(token=token), self.assertRaises(AssertionError):
                client.consume_rules(token)

    def test_failed_source_validation_publishes_no_bundle_or_applied_metadata(self):
        with patch.object(client, 'download_raw_rules', return_value={**self.raw, 'domains': b'payload: []'}):
            with self.assertRaises(AssertionError):
                client.prefetch_rules()
        self.assertFalse((self.local / 'rule-preparations').exists())
        self.assertFalse((self.local / 'sources.json').exists())

    def test_preparation_refuses_current_source_or_script_changes(self):
        root = self.local / 'root'
        (root / 'profiles').mkdir(parents=True)
        profiles = {'items': [{'uid': 's', 'type': 'script', 'file': 's.js'}]}
        item = {'file': 'source.yaml', 'option': {'script': 's'}}
        files = {'profiles.yaml': profiles, 'clash-verge.yaml': {}, 'profiles/source.yaml': {'rules': []}}
        for name, value in files.items():
            (root / name).write_text(safe_yaml.dumps(value))
        script = root / 'profiles/s.js'
        script.write_text('original')
        inputs = client.preparation_inputs(root, item, profiles)
        client.assert_preparation_inputs(root, inputs)
        script.write_text('user edit')
        with self.assertRaises(AssertionError):
            client.assert_preparation_inputs(root, inputs)

    def test_selected_subscription_skips_unrelated_yaml_and_preserves_ambiguity(self):
        root = self.local / 'root'
        (root / 'profiles').mkdir(parents=True)
        profiles = {'items': [{'uid': uid, 'name': uid, 'type': 'remote', 'file': uid + '.yaml'} for uid in ('one', 'two')]}
        for uid in ('one', 'two'):
            (root / 'profiles' / (uid + '.yaml')).write_text('proxies: [{name: Chosen}]')
        with patch.object(client, 'load', wraps=client.load) as read:
            self.assertEqual(client.select(root, 'Chosen', 'one', profiles)['uid'], 'one')
        self.assertEqual(read.call_count, 1)
        with self.assertRaises(AssertionError):
            client.select(root, 'Chosen', profiles=profiles)
        with self.assertRaises(AssertionError):
            client.select(root, 'chosen', 'one', profiles)

    def test_prepare_reuses_runtime_and_refuses_stale_publication(self):
        root = self.local / 'root'
        (root / 'profiles').mkdir(parents=True)
        index = {'current': 'one', 'items': [{'uid': 'one', 'type': 'remote', 'file': 'one.yaml'}]}
        (root / 'profiles.yaml').write_text(safe_yaml.dumps(index))
        source = root / 'profiles/one.yaml'
        source.write_text('proxies: [{name: Chosen}]')
        (root / 'clash-verge.yaml').write_text('{}')
        candidate = ('wrapper', 'yaml', {'rules': []}, None)
        with patch.object(client, 'executables', return_value=('fixture-node', 'fixture-core')) as runtime, \
             patch.object(client, 'backup'), patch.object(client, 'pin_baseline'), \
             patch.object(client, 'default_snapshot', return_value=None), \
             patch.object(client, 'snapshot_dir', return_value=self.local / 'snapshots/first'), \
             patch.object(client, 'build', return_value=candidate) as build:
            client.prepare(root, 'Chosen', 'one', 'SG')
            runtime.assert_called_once()
            self.assertEqual(build.call_args.kwargs['runtime'], ('fixture-node', 'fixture-core'))
            previous = (self.local / 'plan.json').read_bytes()
            def changed(*args, **kwargs):
                source.write_text('proxies: [{name: Other}]')
                return candidate
            build.side_effect = changed
            with self.assertRaisesRegex(AssertionError, 'changed during preparation'):
                client.prepare(root, 'Chosen', 'one', 'SG')
            self.assertEqual((self.local / 'plan.json').read_bytes(), previous)
            with self.assertRaisesRegex(AssertionError, 'changed during preparation'):
                client.apply_client()

    def test_status_reads_recorded_script_first_and_does_not_cache_success(self):
        root = self.local / 'root'
        (root / 'profiles').mkdir(parents=True)
        script = root / 'profiles/project.js'
        script.write_text('// PORTABLE-CLAUDE-NETWORK-V1')
        app = gui_server.App(local=self.local, open_browser=False)
        with patch.object(app, 'plan', return_value={'root': str(root), 'script_file': 'project.js'}), patch.object(Path, 'iterdir', side_effect=AssertionError('unnecessary scan')):
            self.assertEqual(app.configured(), (True, ''))
        script.write_text('removed by user')
        with patch.object(app, 'plan', return_value={'root': str(root), 'script_file': 'project.js'}):
            self.assertEqual(app.configured(), (False, ''))

    def test_timings_exclude_arguments_errors_and_results_and_cannot_break_recovery(self):
        @performance.timed('fixture.stage')
        def fail(secret):
            raise RuntimeError(secret)
        with self.assertRaises(RuntimeError):
            fail('sensitive-fixture')
        log = next((self.local / 'performance').glob('*.jsonl'))
        text = log.read_text()
        self.assertNotIn('sensitive-fixture', text)
        self.assertEqual(json.loads(text)['status'], 'failed')
        self.assertGreaterEqual(json.loads(text)['elapsed_ms'], 0)
        with patch.object(Path, 'mkdir', side_effect=OSError('disk full')):
            with performance.stage('fixture.success'):
                pass

    def test_scope_recommendations_never_treat_missing_checks_as_no_change(self):
        config = {'find-process-mode': 'strict', 'rules': ['GEOSITE,geolocation-!cn,' + client.GROUP, 'RULE-SET,strict-cn-domain,DIRECT'],
                  'dns': {'nameserver': ['fixture'], 'nameserver-policy': {'geosite:geolocation-!cn': ['fixture']}}}
        current = {'ipv6_enabled_bindings': 0, 'ipv6': '255', 'country_code': 'SG', 'culture': 'zh-SG', 'timezone': 'Singapore Standard Time'}
        plan = {'region': 'SG', 'source_digest': config_health.source_digest()}
        self.assertEqual(config_health.inspect(config, current, plan)['recommendation']['action'], 'none')
        self.assertEqual(config_health.inspect(config, {**current, 'ipv6_enabled_bindings': 1}, plan)['recommendation']['action'], 'supplement')
        self.assertEqual(config_health.inspect(config, current, {**plan, 'source_digest': 'old'})['recommendation']['action'], 'apply')
        self.assertEqual(config_health.inspect(config, {}, plan)['recommendation']['action'], 'inspect')

    def test_action_scope_ui_routes_explicitly_and_hides_stale_recommendations(self):
        result = subprocess.run([str(client.PROJECT / '.runtime/node/node.exe'), 'scripts/test_action_scope_ui.js'],
                                cwd=client.PROJECT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
