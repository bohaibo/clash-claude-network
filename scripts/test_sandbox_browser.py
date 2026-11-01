"""Isolated browser profile tests; never launches or edits a real browser profile."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import sandbox_browser as sb


class SandboxBrowser(unittest.TestCase):
    def setUp(self):
        windows = patch.object(sb, 'visible_window_count', side_effect=lambda pids: 1 if pids else 0)
        windows.start()
        self.addCleanup(windows.stop)
        shell = patch.object(sb, '_shell_launch', return_value=0)
        shell.start()
        self.addCleanup(shell.stop)
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='sandbox-browser-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)
        self.profile = sb.profile_path(self.local)

    def test_default_home_is_blank_and_legacy_default_migrates(self):
        self.assertEqual(sb.load_settings(self.local)['start_url'], 'about:blank')
        path = sb.settings_path(self.local)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'start_url': 'https://claude.ai/'}), encoding='utf-8')
        self.assertEqual(sb.load_settings(self.local)['start_url'], 'about:blank')
        self.assertEqual(sb.save_settings({'language': 'en-US'}, self.local)['start_url'], 'about:blank')

    def test_only_the_blank_about_address_is_allowed(self):
        with self.assertRaises(ValueError):
            sb.save_settings({'start_url': 'about:settings'}, self.local)
        sb.save_settings({'start_url': 'about:blank'}, self.local)

    def test_environment_checks_open_both_requested_sites_in_same_engine(self):
        with patch.object(sb, 'launch', side_effect=[{'engine': 'edge'}, {'engine': 'edge'}]) as launch:
            result = sb.open_environment_checks(self.local)
        self.assertEqual(result['opened'], list(sb.CHECK_URLS))
        self.assertEqual(launch.call_args_list[0].args, ('https://ip.net.coffee/claude/',))
        self.assertEqual(launch.call_args_list[1].args, ('https://browserleaks.com/webrtc',))
        self.assertEqual(launch.call_args_list[1].kwargs, {'local': self.local, 'engine': 'edge'})

    def test_second_check_failure_reports_partial_open(self):
        with patch.object(sb, 'launch', side_effect=[{'engine': 'chrome'}, RuntimeError('failed')]):
            with self.assertRaisesRegex(RuntimeError, '第一个检测网站已打开'):
                sb.open_environment_checks(self.local)

    def test_desktop_shell_launch_is_preferred_to_direct_child(self):
        found = {'name': 'edge', 'path': 'msedge.exe'}
        with patch.object(sb, '_shell_launch', return_value=33), \
             patch.object(sb, 'running_pids', return_value=[1234]), \
             patch.object(sb, '_launch') as direct:
            result, error = sb._engine_attempt(found, self.profile, 'zh-SG',
                                               'http://127.0.0.1/', True, True,
                                               1, False, self.local / 'engine.log')
        self.assertEqual(result['pid'], 1234)
        self.assertEqual(error, '')
        direct.assert_not_called()

    def test_background_process_without_window_is_not_launch_success(self):
        dead = types.SimpleNamespace(pid=1234, poll=lambda: 1)
        with patch.object(sb, 'running_pids', return_value=[1234]), \
             patch.object(sb, 'visible_window_count', return_value=0), \
             patch.object(sb.time, 'sleep'), \
             patch.object(sb, '_launch', return_value=dead):
            result, error = sb._engine_attempt(
                {'name': 'edge', 'path': 'msedge.exe'}, self.profile, 'zh-SG',
                'https://browserleaks.com/webrtc', False, False, 0, False,
                self.local / 'engine.log')
        self.assertIsNone(result)
        self.assertIn('后台进程', error)
        self.assertIn('0x00000001', error)

    def test_launch_records_the_requested_engine(self):
        self.profile.parent.mkdir(parents=True)
        process = types.SimpleNamespace(pid=4321, poll=lambda: None)
        with patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}), \
             patch.object(sb, 'running_pids', side_effect=[[], [4321], [4321]]), \
             patch.object(sb, 'apply_preferences'), \
             patch.object(sb, '_launch', return_value=process), \
             patch.object(sb, 'LAST_LAUNCH', self.local / 'browser' / 'last-launch.json'):
            result = sb.launch(local=self.local, engine='chrome', wait=1)
        self.assertEqual(result['engine'], 'chrome')
        self.assertEqual(result['requested_engine'], 'chrome')

    def test_arguments_use_isolated_profile_and_expected_flags(self):
        args = sb.build_arguments('chrome.exe', self.profile, 'zh-SG', 'https://claude.ai/')
        self.assertIn('--user-data-dir=' + str(self.profile), args)
        self.assertIn('--force-webrtc-ip-handling-policy=disable_non_proxied_udp', args)
        self.assertIn('--disable-quic', args)
        self.assertIn('--lang=zh-SG', args)
        self.assertTrue(any(value.startswith('--accept-lang=zh-SG') for value in args))
        self.assertFalse(any('--proxy-server' in value for value in args))
        self.assertFalse(any('User Data' in value or 'Google/Chrome/User Data' in value for value in args))
        self.assertIn('https://claude.ai/', args)
        self.assertIn('--new-window', args)

    def test_gui_arguments_use_app_window_and_isolated_profile(self):
        args = sb.build_arguments('msedge.exe', self.profile, 'zh-SG', 'http://127.0.0.1:1234/?t=x', gui=True)
        self.assertIn('--app=http://127.0.0.1:1234/?t=x', args)
        self.assertNotIn('--new-window', args)
        self.assertFalse(any(value == 'http://127.0.0.1:1234/?t=x' for value in args))
        self.assertIn('--window-size=1280,880', args)
        self.assertIn('--disable-extensions', args)
        self.assertFalse(any('--lang=' in value for value in args))

    def test_managed_preferences_preserve_unrelated_keys(self):
        folder = self.profile / 'Default'
        folder.mkdir(parents=True)
        path = folder / 'Preferences'
        path.write_text(json.dumps({
            'other': {'keep': True},
            'intl': {'accept_languages': 'old', 'keep': 2},
        }), encoding='utf-8')
        sb.apply_preferences(self.profile, 'zh-SG')
        stored = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(stored['other'], {'keep': True})
        self.assertEqual(stored['intl']['keep'], 2)
        self.assertEqual(stored['intl']['accept_languages'], 'zh-SG,zh')
        self.assertEqual(stored['dns_over_https']['mode'], 'off')
        self.assertEqual(stored['webrtc']['ip_handling_policy'], 'disable_non_proxied_udp')
        self.assertFalse(stored['webrtc']['multiple_routes_enabled'])
        self.assertFalse(stored['signin']['allowed'])

    def test_corrupt_preferences_are_not_overwritten(self):
        folder = self.profile / 'Default'
        folder.mkdir(parents=True)
        path = folder / 'Preferences'
        path.write_text('{"intl":', encoding='utf-8')
        with self.assertRaises(ValueError):
            sb.apply_preferences(self.profile, 'zh-SG')
        self.assertEqual(path.read_text(encoding='utf-8'), '{"intl":')

    def test_settings_validation_and_roundtrip(self):
        with self.assertRaises(ValueError):
            sb.save_settings({'engine': 'firefox'}, self.local)
        sb.save_settings({'engine': 'edge', 'language': 'en-SG'}, self.local)
        loaded = sb.load_settings(self.local)
        self.assertEqual(loaded['engine'], 'edge')
        self.assertEqual(loaded['language'], 'en-SG')

    def test_reset_requires_confirmation(self):
        self.profile.mkdir(parents=True)
        (self.profile / 'x').write_text('x', encoding='utf-8')
        with self.assertRaises(AssertionError):
            sb.reset(self.local, confirm='')
        self.assertTrue(self.profile.exists())

    def test_reset_refuses_running_browser(self):
        self.profile.mkdir(parents=True)
        with patch.object(sb, 'running_pids', return_value=[1234]):
            with self.assertRaises(RuntimeError):
                sb.reset(self.local, confirm='DELETE')
        self.assertTrue(self.profile.exists())

    def test_reset_deletes_only_sandbox_profile(self):
        self.profile.mkdir(parents=True)
        (self.profile / 'Cookies').write_text('cookie', encoding='utf-8')
        keep = self.local / 'keep.txt'
        keep.write_text('keep', encoding='utf-8')
        with patch.object(sb, 'running_pids', return_value=[]):
            result = sb.reset(self.local, confirm='DELETE')
        self.assertTrue(result['reset'])
        self.assertFalse(self.profile.exists())
        self.assertTrue(keep.exists())
        self.assertFalse(any(sb.browser_root(self.local).glob('trash-*')))

    def test_close_rejects_paths_outside_project(self):
        with self.assertRaises(AssertionError):
            sb.close(local=self.local, profile=Path('C:/Windows'))

    def test_launch_uses_only_isolated_profile(self):
        profile = self.profile
        profile.parent.mkdir(parents=True)
        last = self.local / 'browser' / 'last-launch.json'
        process = types.SimpleNamespace(pid=4321, poll=lambda: None)
        with patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}), \
             patch.object(sb, 'running_pids', side_effect=[[], [4321], [4321]]), \
             patch.object(sb, 'apply_preferences') as apply_preferences, \
             patch.object(sb, '_launch', return_value=process) as launch, \
             patch.object(sb, 'LAST_LAUNCH', last):
            result = sb.launch(local=self.local, wait=1)
        self.assertTrue(result['started'])
        apply_preferences.assert_called_once()
        command = launch.call_args.args
        self.assertEqual(command[0], 'chrome.exe')
        self.assertIn('--user-data-dir=' + str(profile), command[1])
        self.assertNotIn('--proxy-server', ' '.join(command[1]))
        self.assertTrue(last.is_file())

    def test_launch_falls_back_to_shell_when_createprocess_dies(self):
        # A vendor update can make CreateProcess starts abort with
        # STATUS_BREAKPOINT; the Explorer path still works, so it is retried.
        self.profile.parent.mkdir(parents=True)
        dead = types.SimpleNamespace(pid=111, poll=lambda: 2147483651)
        with patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}), \
             patch.object(sb, 'running_pids', side_effect=[[], [], [], [4321], [4321]]), \
             patch.object(sb, 'apply_preferences'), \
             patch.object(sb, '_launch', return_value=dead), \
             patch.object(sb, '_shell_launch', return_value=42) as shell, \
             patch.object(sb, 'LAST_LAUNCH', self.local / 'browser' / 'last-launch.json'):
            result = sb.launch(local=self.local, wait=1)
        self.assertTrue(result['started'])
        self.assertEqual(result['pid'], 4321)
        shell.assert_called_once()

    def test_launch_tries_the_other_engine_when_one_cannot_start(self):
        self.profile.parent.mkdir(parents=True)
        calls = []
        engines = {'auto': {'name': 'chrome', 'path': 'chrome.exe'},
                   'chrome': {'name': 'chrome', 'path': 'chrome.exe'},
                   'edge': {'name': 'edge', 'path': 'msedge.exe'}}

        def attempt(found, *args):
            calls.append(found['name'])
            if found['name'] == 'edge':
                return {'started': True, 'engine': 'edge'}, ''
            return None, 'chrome 启动失败'
        with patch.object(sb, 'find_engine', side_effect=lambda pref: engines.get(pref) or engines['chrome']), \
             patch.object(sb, 'running_pids', return_value=[]), \
             patch.object(sb, 'apply_preferences'), \
             patch.object(sb, '_engine_attempt', side_effect=attempt), \
             patch.object(sb, 'LAST_LAUNCH', self.local / 'browser' / 'last-launch.json'):
            result = sb.launch(local=self.local, wait=0)
        self.assertEqual(result['engine'], 'edge')
        self.assertEqual(calls, ['chrome', 'edge'])

    def test_stale_engine_log_is_not_reported_as_this_failure(self):
        self.profile.parent.mkdir(parents=True)
        log = self.local / 'gui' / 'engine.log'
        log.parent.mkdir(parents=True)
        log.write_text('旧的失败原因：正在现有的浏览器会话中打开。\n', encoding='utf-8')
        dead = types.SimpleNamespace(pid=111, poll=lambda: 1)
        with patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}), \
             patch.object(sb, 'running_pids', return_value=[]), \
             patch.object(sb, 'apply_preferences'), \
             patch.object(sb, '_launch', return_value=dead), \
             patch.object(sb, '_shell_launch', return_value=0), \
             patch.object(sb, 'LAST_LAUNCH', self.local / 'browser' / 'last-launch.json'):
            with self.assertRaises(RuntimeError) as caught:
                sb.launch(local=self.local, gui=True, wait=0)
        self.assertNotIn('旧的失败原因', str(caught.exception))
        self.assertIn('浏览器启动失败', str(caught.exception))

    def test_configure_region_sets_language_and_timezone(self):
        result = sb.configure_region('US', self.local)
        self.assertEqual(result['region'], 'US')
        self.assertEqual(result['language'], 'en-US')
        self.assertEqual(result['timezone'], 'America/New_York')
        self.assertFalse(result['applied_to_profile'])
        stored = sb.load_settings(self.local)
        self.assertEqual(stored['region'], 'US')
        self.assertEqual(stored['language'], 'en-US')
        args = sb.build_arguments('chrome.exe', self.profile, stored['language'], 'https://claude.ai/')
        self.assertIn('--lang=en-US', args)
        with self.assertRaises(ValueError):
            sb.configure_region('USA', self.local)

    def test_check_page_receives_expected_region_and_language(self):
        with patch.object(sb, 'launch', return_value={'started': True}) as launch:
            sb.open_check_page('http://127.0.0.1:1234/', self.local, region='SG', language='zh-SG', timezone='Asia/Singapore')
            launch.assert_called_once_with(
                'http://127.0.0.1:1234/sandbox-check.html?region=SG&lang=zh-SG&tz=Asia%2FSingapore',
                local=self.local, app=False,
            )

    def test_default_browser_launcher_uses_sandbox_profile(self):
        result = sb.write_launcher(self.local)
        self.assertTrue(Path(result['launcher']).is_file())
        raw = Path(result['launcher']).read_bytes()
        self.assertTrue(raw.startswith(b'\xff\xfe'))
        text = raw.decode('utf-16')
        self.assertIn('--user-data-dir=', text)
        self.assertIn(str(self.profile), text)
        self.assertIn('Chrome', text)
        status = sb.default_status(self.local)
        self.assertIn('is_default', status)
        self.assertIn('registered', status)

    def test_status_never_claims_default_browser_was_touched(self):
        with patch.object(sb, 'running_pids', return_value=[]), \
             patch.object(sb, '_directory_size', return_value=0), \
             patch.object(sb, 'integrity_level', return_value='medium'):
            result = sb.status(self.local)
        self.assertFalse(result['touches_default_browser'])
        self.assertTrue(result['limitations'])
        self.assertEqual(result['settings']['language'], 'zh-SG')
        self.assertEqual(result['environment'], {'integrity': 'medium', 'restricted': False, 'message': ''})


class RestrictedEnvironment(unittest.TestCase):
    """A low-integrity GUI cannot write HKCU and Chrome aborts at startup."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='sandbox-restricted-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)

    def test_low_integrity_is_reported_as_restricted(self):
        with patch.object(sb, 'integrity_level', return_value='low'):
            self.assertTrue(sb.restricted_environment())
            status = sb.environment_status()
        self.assertTrue(status['restricted'])
        self.assertIn('低完整性', status['message'])

    def test_medium_integrity_is_not_restricted(self):
        with patch.object(sb, 'integrity_level', return_value='medium'):
            self.assertFalse(sb.restricted_environment())
            self.assertEqual(sb.environment_status()['message'], '')

    def test_register_default_explains_restricted_environment(self):
        with patch.object(sb, 'integrity_level', return_value='low'), \
             patch.object(sb, 'write_launcher') as launcher, \
             patch.object(sb, 'winreg') as registry:
            with self.assertRaises(RuntimeError) as caught:
                sb.register_default(self.local)
        message = str(caught.exception)
        self.assertIn('低完整性', message)
        self.assertIn('一键网络.cmd', message)
        launcher.assert_not_called()
        registry.CreateKeyEx.assert_not_called()

    def test_default_status_marks_registration_unavailable(self):
        with patch.object(sb, 'integrity_level', return_value='low'):
            status = sb.default_status(self.local)
        self.assertFalse(status['can_register'])
        self.assertIn('低完整性', status['restricted_reason'])

    def test_launch_failure_adds_restricted_hint(self):
        self.local.joinpath('browser').mkdir(parents=True)
        with patch.object(sb, 'integrity_level', return_value='low'), \
             patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}), \
             patch.object(sb, 'running_pids', return_value=[]), \
             patch.object(sb, 'apply_preferences'), \
             patch.object(sb, '_engine_attempt', return_value=(None, 'chrome 在创建窗口前退出')), \
             patch.object(sb, 'LAST_LAUNCH', self.local / 'browser' / 'last-launch.json'):
            with self.assertRaises(RuntimeError) as caught:
                sb.launch(local=self.local, wait=0)
        self.assertIn('低完整性', str(caught.exception))


class _Denied:
    def __enter__(self):
        raise OSError(13, '拒绝访问。')

    def __exit__(self, *exc):
        return False


class _Key:
    def __init__(self, registry, path):
        self.registry = registry
        self.path = path

    def __enter__(self):
        self.registry.created.append(self.path)
        return self

    def __exit__(self, *exc):
        return False


class _FakeRegistry:
    HKEY_CURRENT_USER = 0x80000001
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self, denied=None):
        self.denied = denied
        self.created = []
        self.values = []

    def CreateKeyEx(self, root, path, *args):
        if self.denied and path == self.denied:
            return _Denied()
        return _Key(self, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values.append((key.path, name, kind, value))


class DefaultBrowserRegistration(unittest.TestCase):
    """Registration is exercised against a fake HKCU; the real registry is never written."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='sandbox-register-test-')
        self.addCleanup(temp.cleanup)
        self.local = Path(temp.name)

    def register(self, registry):
        patches = [
            patch.object(sb, 'integrity_level', return_value='medium'),
            patch.object(sb, 'winreg', registry),
            patch.object(sb, 'write_launcher', return_value={'launcher': 'launcher.vbs'}),
            patch.object(sb, 'find_engine', return_value={'name': 'chrome', 'path': 'chrome.exe'}),
            patch.object(sb, 'load_settings', return_value={'engine': 'auto'}),
            patch.object(sb, 'default_status', return_value={'registered': True}),
            patch.object(sb.os, 'startfile'),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return sb.register_default(self.local)

    def test_registers_progid_capabilities_and_launcher(self):
        registry = _FakeRegistry()
        result = self.register(registry)
        self.assertTrue(result['settings_opened'])
        written = {path: name for path, name, _, _ in registry.values}
        self.assertIn('Software\\Clients\\StartMenuInternet\\' + sb.DEFAULT_APP, written)
        self.assertIn('Software\\RegisteredApplications', written)
        self.assertTrue(any(name == 'http' for _, name, _, _ in registry.values))
        self.assertTrue(any(name == 'https' for _, name, _, _ in registry.values))

    def test_reports_the_key_that_windows_denied(self):
        registry = _FakeRegistry(denied='Software\\RegisteredApplications')
        with self.assertRaises(RuntimeError) as caught:
            self.register(registry)
        message = str(caught.exception)
        self.assertIn('已注册应用', message)
        self.assertIn('Software\\RegisteredApplications', message)
        self.assertIn('拒绝访问', message)


if __name__ == '__main__':
    unittest.main()
