"""Windows byte-range lock shared with run.ps1; no live configuration on import."""
from pathlib import Path
from contextlib import contextmanager
import msvcrt,json,re

def check_pending_firewall(local):
    file=Path(local)/'pending-firewall.json'
    if not file.exists():return
    pending=json.loads(file.read_text(encoding='utf-8-sig'))
    token=pending.get('token','')
    if not re.fullmatch(r'guard-[a-f0-9]{32}',token):
        raise RuntimeError('Invalid pending firewall recovery record')
    if not (Path(local)/(token+'.commit')).exists():
        raise RuntimeError('Firewall rollback is pending; use the root menu to finish recovery before updating')

@contextmanager
def operation_lock(local):
    path=Path(local)/'operation.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with open(path,'a+b') as stream:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise RuntimeError('Another project operation is running; retry later') from None
        try:
            check_pending_firewall(local)
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
