"""Rule-update task metadata tests; status/invalid-time only, no task is registered."""
import json
import subprocess
import sys
import unittest

import client


class UpdateTask(unittest.TestCase):
    def run_script(self, *arguments):
        return subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
             '-File', str(client.PROJECT / 'scripts' / 'update_task.ps1'), *arguments],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
        )

    def last_json(self, text):
        for line in reversed((text or '').splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                return value
        return None

    def test_status_is_read_only_and_reports_boolean(self):
        result = self.run_script('-Action', 'status', '-Python', sys.executable)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = self.last_json(result.stdout)
        self.assertIsNotNone(value)
        self.assertIsInstance(value.get('installed'), bool)
        if value.get('installed'):
            self.assertRegex(value.get('time') or '', r'^(?:[01]\d|2[0-3]):[0-5]\d$')
            self.assertIsInstance(value.get('action_matches'), bool)
            self.assertIsInstance(value.get('days'), int)
            self.assertTrue(1 <= value['days'] <= 365)

    def test_invalid_time_stops_before_registering(self):
        result = self.run_script('-Action', 'install', '-At', '25:00', '-Python', sys.executable)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HH:MM', result.stderr + result.stdout)


if __name__ == '__main__':
    unittest.main()
