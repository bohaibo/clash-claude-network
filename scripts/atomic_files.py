"""Retry transient Windows sharing failures without deleting the destination."""
from pathlib import Path
import time


def replace_file(source, target):
    source, target = Path(source), Path(target)
    for attempt in range(5):
        try:
            source.replace(target)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) in (5, 32, 33)
            if not transient or attempt == 4:
                raise
            time.sleep(.05 * 2 ** attempt)
