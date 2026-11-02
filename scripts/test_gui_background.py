"""Offline background state and frontend behavior checks."""
import subprocess
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import client
import gui_server


class BackgroundState(unittest.TestCase):
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
