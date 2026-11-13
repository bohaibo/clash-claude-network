"""Offline migration checks. Never starts a browser or contacts any service."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import client
import config_health as health
import gui_server
import gui_window
import test_routing


class ConfigurationMigration(unittest.TestCase):
    def test_overseas_exclusion_shadows_cn_and_dns_uses_same_exit(self):
        fixture = test_routing.Routing()
        fixture.setUp()
        try:
            config = fixture.render()
            client.validate(config, fixture.base, 'Chosen')
            foreign = 'GEOSITE,geolocation-!cn,' + client.GROUP
            config['rules'].remove(foreign)
            config['rules'].insert(-1, foreign)
            with self.assertRaises(AssertionError):
                client.validate(config, fixture.base, 'Chosen')
            config = fixture.render()
            config['dns']['nameserver-policy']['geosite:geolocation-!cn'] = ['system']
            with self.assertRaises(AssertionError):
                client.validate(config, fixture.base, 'Chosen')
        finally:
            fixture.doCleanups()

    def test_legacy_config_and_region_drift_are_actionable(self):
        result = health.inspect({}, {'country_code':'CN', 'culture':'zh-CN',
                                     'timezone':'Singapore Standard Time',
                                     'ipv6':'255', 'ipv6_enabled_bindings':2}, {'region':'SG'})
        rows = {row['title']:row for row in result['checks']}
        self.assertFalse(result['success'])
        self.assertEqual(rows['配置生成版本']['status'], 'unknown')
        self.assertEqual(rows['国家/地区']['action'], '补充配置')
        self.assertEqual(rows['系统时区']['status'], 'passed')
        self.assertEqual(rows['启用的 IPv6 绑定数']['status'], 'failed')

    def test_missing_adapter_inventory_never_proves_ipv6_disabled(self):
        rows = health.inspect({}, {}, {'region':'SG'})['checks']
        self.assertEqual(next(r for r in rows if r['title']=='启用的 IPv6 绑定数')['status'], 'unknown')

    def test_live_core_with_stale_rule_order_is_rejected(self):
        runtime = {'mode':'rule', 'find-process-mode':'strict'}
        rules = [{'type':'GeoSite','payload':'geolocation-!cn','proxy':client.GROUP},
                 {'type':'RuleSet','payload':'strict-cn-domain','proxy':'DIRECT'}]
        health.assert_live(runtime, {'rules':rules})
        with self.assertRaises(AssertionError):
            health.assert_live(runtime, {'rules':list(reversed(rules))})
        with self.assertRaises(AssertionError):
            health.assert_live(dict(runtime, **{'find-process-mode':'always'}), {'rules':rules})

    def test_management_window_rejects_external_addresses_before_launch(self):
        with patch.object(gui_window, 'find_engine') as engine:
            for url in ('https://example.invalid', 'http://localhost:1234', 'file:///x',
                        'http://127.0.0.1:1234@example.invalid'):
                with self.subTest(url=url), self.assertRaises((AssertionError, ValueError)):
                    gui_window.launch(url)
            engine.assert_not_called()

    def test_retired_browser_jobs_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=client.LOCAL) as folder:
            app = gui_server.App(local=Path(folder), open_browser=False)
            for action in ('browser-open','browser-check','browser-close','browser-reset','browser-register'):
                with self.subTest(action=action), self.assertRaises(gui_server.ApiError):
                    app._validate_job({'action':action,'confirm':action.upper()})

    def test_every_frontend_module_parses_without_starting_browser(self):
        import subprocess
        code = "const fs=require('fs'),vm=require('vm'); for(const d of ['gui/js','gui/js/pages']) for(const f of fs.readdirSync(d)) if(f.endsWith('.js')) new vm.SourceTextModule(fs.readFileSync(d+'/'+f,'utf8'));"
        subprocess.run([str(client.PROJECT/'.runtime/node/node.exe'), '--experimental-vm-modules', '-e', code],
                       cwd=client.PROJECT, check=True, capture_output=True, timeout=10)
