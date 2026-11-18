"""Capture output from the elevated GUI task in a per-job log."""
import base64
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    time = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    line = json.dumps({'time': time, **event}, ensure_ascii=False)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')


def capture(payload, script):
    events = Path(payload['events'])
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(script), *payload['arguments']]
    with events.with_name('elevated.log').open('ab') as log:
        try:
            append_event(events, {'type': 'authorized'})
        except OSError:
            pass
        try:
            return subprocess.call(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=dict(os.environ, PYTHONUNBUFFERED='1'),
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError:
            log.write('无法启动管理员任务。\n'.encode('utf-8'))
            return 1


if __name__ == '__main__':
    args = json.loads(base64.b64decode(sys.argv[1], validate=True).decode('utf-8'))
    raise SystemExit(capture(args, Path(__file__).with_name('run.ps1')))
