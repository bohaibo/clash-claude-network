"""Run the real stop function with process mocks; never stop or start Clash."""
import subprocess
import tempfile
import unittest
from pathlib import Path

import client


class ClashProcessExit(unittest.TestCase):
    def run_case(self, setup):
        source = (client.PROJECT / 'scripts/run.ps1').read_text(encoding='utf-8-sig')
        function = source[source.index('function Stop-ClashClient {'):source.index('function Start-ClashClient {')]
        harness = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$script:queries=0
$script:stopped=0
function New-ProcessMock($wait,$exited){
 $p=[pscustomobject]@{Handle=123;HasExited=$exited;WaitResult=$wait;Disposed=$false;Waited=$false}
 $p | Add-Member ScriptMethod WaitForExit {param($timeout) if($timeout -ne 15000){throw 'Wrong timeout'};$this.Waited=$true;return $this.WaitResult}
 $p | Add-Member ScriptMethod Dispose {$this.Disposed=$true}
 return $p
}
function Get-Process {param($Name,$ErrorAction)
 if($Name -ne 'clash-verge'){throw 'Unexpected query'}
 $script:queries++
 if($script:queries -eq 1){return $script:original}
 return $script:remaining
}
function Stop-Process {param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Force)
 process {if($null -ne $InputObject){$script:stopped++}}
}
function Assert-Case($ok,$message){if(!$ok){throw $message}}
"""
        with tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='clash-process-test-') as tmp:
            script = Path(tmp) / 'test.ps1'
            script.write_text(harness + function + setup, encoding='utf-8-sig')
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(script)], capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exited_process_object_does_not_block_configuration(self):
        self.run_case(r"""
$script:original=New-ProcessMock $true $true
$script:remaining=New-ProcessMock $true $true
Stop-ClashClient
Assert-Case ($original.Waited -and $original.Disposed -and $remaining.Disposed) 'Exit must be awaited and handles disposed'
Assert-Case ($script:stopped -eq 1) 'Original process must be stopped'
""")

    def test_live_replacement_blocks_configuration(self):
        self.run_case(r"""
$script:original=New-ProcessMock $true $true
$script:remaining=New-ProcessMock $false $false
$message='';try{Stop-ClashClient}catch{$message=$_.Exception.Message}
Assert-Case ($message -like '*重新启动*') 'Live replacement must block writes'
Assert-Case ($original.Disposed -and $remaining.Disposed) 'Failure must release handles'
""")

    def test_original_process_timeout_blocks_configuration(self):
        self.run_case(r"""
$script:original=New-ProcessMock $false $false
$message='';try{Stop-ClashClient}catch{$message=$_.Exception.Message}
Assert-Case ($message -like '*旧进程尚未退出*') 'Timeout must block writes'
Assert-Case ($original.Waited -and $original.Disposed) 'Timeout must release handles'
Assert-Case ($script:queries -eq 1) 'Do not continue after timeout'
""")

    def test_no_client_process_is_allowed(self):
        self.run_case(r"""
$script:original=@();$script:remaining=@()
Stop-ClashClient
Assert-Case ($script:stopped -eq 0) 'No process should be stopped'
""")


if __name__ == '__main__':
    unittest.main()
