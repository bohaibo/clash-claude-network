"""Independent recovery for node and service outage drills."""
import argparse
import ctypes
import hashlib
import json
import os
from contextlib import contextmanager
import re
import subprocess
import sys
import time
import uuid

from checks import require
from client import LOCAL, root_path, write

KINDS = ('node-outage', 'core-exit')


class DrillRecoveryError(RuntimeError):
    pass


def spawn_worker(folder):
    # The short launcher exits before the drill is armed. The detached worker
    # can therefore outlive termination of the operation's process tree.
    with (folder / 'watchdog.log').open('ab') as output:
        subprocess.Popen(
            [sys.executable, __file__, '--watch', folder.name],
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )


def wait_for(folder, marker, seconds):
    deadline = time.monotonic() + seconds
    while not (folder / marker).exists():
        if time.monotonic() >= deadline:
            raise DrillRecoveryError('Outage watchdog did not confirm ' + marker)
        report = folder / 'recovery.json'
        if report.exists() and (marker != 'done' or not json.loads(report.read_text(encoding='utf-8'))['success']):
            raise DrillRecoveryError('Outage watchdog did not confirm ' + marker)
        time.sleep(.1)


@contextmanager
def guarded_outage(root, kind, original, candidate=None):
    require(kind in KINDS, 'Invalid outage drill')
    folder = LOCAL / 'tun-drills' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    backup = original.encode('utf-8')
    (folder / 'runtime.before').write_bytes(backup)
    if candidate is not None:
        (folder / 'runtime.drill').write_text(candidate, encoding='utf-8')
    write(folder / 'guard.json', {
        'kind': kind, 'root': str(root), 'parent': os.getpid(),
        'sha256': hashlib.sha256(backup).hexdigest(),
    })
    subprocess.run(
        [sys.executable, __file__, '--spawn', folder.name],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=8, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    wait_for(folder, 'ready', 8)
    (folder / 'armed').touch()
    try:
        wait_for(folder, 'applied', 40)
        yield
    finally:
        (folder / 'restore').touch()
        wait_for(folder, 'done', 95)
        recovery = json.loads((folder / 'recovery.json').read_text(encoding='utf-8'))
        if recovery.get('automatic'):
            raise DrillRecoveryError('Outage watchdog recovered before the drill completed')


def apply_fault(root, kind, folder):
    if kind == 'core-exit':
        from verify_core_exit import command
        command('Stop-Service clash_verge_service -Force -ErrorAction Stop')
    else:
        from verify_tun import api
        candidate = (folder / 'runtime.drill').read_text(encoding='utf-8')
        api(root, 'PUT', '/configs?force=true', {'payload': candidate})


def restore_runtime(root, kind, original):
    if kind == 'core-exit':
        from verify_core_exit import restore_core
        restore_core(root, original)
    else:
        from verify_tun import api
        api(root, 'PUT', '/configs?force=true', {'payload': original})


def run_guard(folder, record, parent_alive):
    kind = record['kind']
    require(kind in KINDS, 'Invalid outage drill')
    root = root_path(record['root'])
    backup = (folder / 'runtime.before').read_bytes()
    require(hashlib.sha256(backup).hexdigest() == record['sha256'], 'Invalid outage recovery backup')
    original = backup.decode('utf-8')
    (folder / 'ready').touch()
    deadline = time.monotonic() + 20
    while not (folder / 'armed').exists():
        if not parent_alive() or time.monotonic() >= deadline:
            return
        time.sleep(.1)
    attempted = parent_alive() and not (folder / 'restore').exists()
    try:
        if attempted:
            apply_fault(root, kind, folder)
            (folder / 'applied').touch()
            deadline = time.monotonic() + 65
            while parent_alive() and time.monotonic() < deadline:
                if (folder / 'restore').exists():
                    break
                time.sleep(.1)
    finally:
        automatic = not (folder / 'restore').exists()
        # The same worker owns the mutation and its recovery, even if the GUI
        # task goes away before the controller or service command returns.
        try:
            if attempted:
                restore_runtime(root, kind, original)
        except Exception as exc:
            write(folder / 'recovery.json', {'success': False, 'error': type(exc).__name__})
            raise
        else:
            write(folder / 'recovery.json', {'success': True, 'automatic': automatic})
            (folder / 'done').touch()
            for name in ('runtime.before', 'runtime.drill'):
                try:
                    (folder / name).unlink(missing_ok=True)
                except OSError:
                    pass


def watch(folder):
    record = json.loads((folder / 'guard.json').read_text(encoding='utf-8'))
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    parent = kernel.OpenProcess(0x100000, False, record['parent'])
    require(parent, 'Cannot monitor the outage task')
    try:
        run_guard(folder, record, lambda: kernel.WaitForSingleObject(parent, 0) == 258)
    finally:
        kernel.CloseHandle(parent)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--spawn')
    mode.add_argument('--watch')
    args = parser.parse_args()
    token = args.spawn or args.watch
    require(re.fullmatch(r'[a-f0-9]{32}', token), 'Invalid outage watchdog ID')
    folder = LOCAL / 'tun-drills' / token
    if args.spawn:
        spawn_worker(folder)
    else:
        watch(folder)
