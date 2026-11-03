"""Deterministic watchdog clocks and recovery mocks; no live system operations."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import client


class WatchdogTiming(unittest.TestCase):
    def run_case(self, arm_at, commit_at):
        with tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='watchdog-timing-') as tmp:
            project = Path(tmp)
            scripts = project / 'scripts'
            scripts.mkdir()
            local = project / '.local'
            local.mkdir()
            source = (client.PROJECT / 'scripts/watchdog.ps1').read_text(encoding='utf-8-sig')
            first, rest = source.split('\n', 1)
            mocks = r"""
$script:tick=0
function Get-Date {$script:tick+=10;return [datetime]::new(2026,1,1).AddSeconds($script:tick)}
function Start-Sleep {param($Seconds)}
function Test-Path {param($LiteralPath)
 if($LiteralPath.EndsWith('.armed')){return $script:tick -ge ARM_AT}
 if($LiteralPath.EndsWith('.commit')){return $script:tick -ge COMMIT_AT}
 return $false
}
""".replace('ARM_AT', str(arm_at)).replace('COMMIT_AT', str(commit_at))
            (scripts / 'watchdog.ps1').write_text(first + '\n' + mocks + rest, encoding='utf-8-sig')
            (scripts / 'operation_guard.ps1').write_text(
                "function Resolve-PendingFirewall {param($LocalDir,$Python,$ExpectedToken) "
                "$script:tick | Set-Content -LiteralPath (Join-Path $LocalDir 'rollback-tick')}\n"
                "function Start-Service {throw 'Unexpected service access'}\n", encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(scripts / 'watchdog.ps1'), '-Token', 'guard-' + 'a' * 32,
                                     '-Python', 'unused', '-WaitForFirewall'], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            marker = local / 'rollback-tick'
            return int(marker.read_text()) if marker.exists() else None

    def test_preparation_does_not_consume_firewall_window(self):
        self.assertIsNone(self.run_case(120, 260))

    def test_missing_arm_still_has_bounded_recovery(self):
        self.assertLessEqual(self.run_case(10000, 10000), 210)

    def test_armed_firewall_without_commit_still_rolls_back(self):
        tick = self.run_case(120, 10000)
        self.assertGreaterEqual(tick, 300)
        self.assertLessEqual(tick, 350)


if __name__ == '__main__':
    unittest.main()
