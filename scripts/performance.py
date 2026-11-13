"""Best-effort local timings. Labels are fixed by code, never user inputs."""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import json
import os
import re
import threading
import time
import uuid

LOCAL = Path(__file__).resolve().parent.parent / '.local'
_process_id = uuid.uuid4().hex
_lock = threading.Lock()


@contextmanager
def stage(label):
    if not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', label):
        raise ValueError('Timing label must be a fixed identifier')
    started = time.perf_counter()
    status = 'failed'
    try:
        yield
        status = 'passed'
    finally:
        try:
            run = os.environ.get('CCN_OPERATION_ID', '')
            if not re.fullmatch(r'[a-f0-9]{32}', run):
                run = _process_id
            action = os.environ.get('CCN_OPERATION_ACTION', '')
            if action not in ('apply', 'supplement', 'restore', 'snapshot', 'update', 'discover', 'reconfigure', 'audit'):
                action = 'other'
            row = {'stage': label, 'action': action, 'status': status,
                   'elapsed_ms': round((time.perf_counter() - started) * 1000, 3)}
            folder = LOCAL / 'performance'
            folder.mkdir(parents=True, exist_ok=True)
            with _lock, (folder / f'{run}.{os.getpid()}.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, separators=(',', ':')) + '\n')
        except OSError:
            pass  # Diagnostics must never change deployment/recovery behavior.


def timed(label):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with stage(label):
                return function(*args, **kwargs)
        return call
    return decorate
