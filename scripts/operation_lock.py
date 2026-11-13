"""Windows byte-range lock shared with run.ps1; no live configuration on import."""
from pathlib import Path
from contextlib import contextmanager
import msvcrt,json,re

def pending_settings(local):
    rows=[]
    for folder in sorted((Path(local)/'gui'/'settings-transactions').glob('*')):
        if not re.fullmatch(r'[a-f0-9]{32}',folder.name) or not folder.is_dir():continue
        journal=folder/'transaction.json'
        if not journal.is_file():continue
        try:
            record=json.loads(journal.read_text(encoding='utf-8-sig'))
            phase=record.get('phase') if isinstance(record,dict) else None
        except (OSError,ValueError):phase=None
        if phase not in ('committed','rolled_back'):
            rows.append({'id':folder.name,'phase':phase or 'invalid',
                         'error':'恢复记录损坏，需检查本地备份。' if not phase else ''})
    return rows

def check_pending_settings(local):
    if pending_settings(local):
        raise RuntimeError('Settings recovery is pending; use the GUI snapshot page to restore original settings first')

def check_pending_firewall(local):
    check_pending_tun(local)
    if (Path(local)/'reconfigure-active.json').exists():
        raise RuntimeError('Safe reconfiguration/recovery is active; scheduled updates must wait')
    file=Path(local)/'pending-firewall.json'
    if not file.exists():return
    pending=json.loads(file.read_text(encoding='utf-8-sig'))
    token=pending.get('token','')
    if not re.fullmatch(r'guard-[a-f0-9]{32}',token):
        raise RuntimeError('Invalid pending firewall recovery record')
    if not (Path(local)/(token+'.commit')).exists():
        raise RuntimeError('Firewall rollback is pending; use the root menu to finish recovery before updating')

def check_pending_tun(local):
    for folder in (Path(local)/'tun-drills').glob('*'):
        if re.fullmatch(r'[a-f0-9]{32}',folder.name) and (folder/'ready').exists() and not (folder/'done').exists():
            raise RuntimeError('TUN drill recovery is pending; wait for local recovery and inspect its report')

@contextmanager
def operation_lock(local,*,settings_recovery=False):
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
            if not settings_recovery:check_pending_settings(local)
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
