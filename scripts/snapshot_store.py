"""Local snapshot deletion safeguards. Call delete_snapshot under operation_lock."""
import json
import re
import shutil
import stat
from pathlib import Path

import baseline

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$')


def linked(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def protection(local):
    """Fail closed when a recovery pointer cannot be interpreted."""
    local = Path(local)
    protected = {}
    try:
        first = baseline.default_snapshot(local)
        if first:
            protected[first.resolve()] = '首次恢复点需保留'
        for filename in ('plan.json', 'pending-firewall.json', 'reconfigure-active.json'):
            file = local / filename
            if not file.exists():
                continue
            data = json.loads(file.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('Invalid recovery pointer')
            value = data.get('snapshot')
            if filename == 'plan.json' and (not isinstance(value, str) or not value.strip()):
                raise ValueError('Missing plan recovery point')
            if value:
                if not isinstance(value, str):
                    raise ValueError('Invalid snapshot reference')
                protected[Path(value).resolve()] = '当前配置或恢复流程正在使用'
        return protected, ''
    except (OSError, ValueError, TypeError):
        return {}, '恢复点关联记录异常，暂不能删除'


def deletion_reason(folder, protected, error=''):
    try:
        if linked(folder):
            return '链接目录不能作为快照操作'
        return error or protected.get(folder.resolve(), '')
    except OSError:
        return '快照目录无法读取'


def delete_snapshot(local, name):
    if not isinstance(name, str) or not NAME.fullmatch(name) or name in ('.', '..'):
        raise ValueError('快照名称无效。')
    local = Path(local).resolve()
    root = local / 'snapshots'
    if not root.is_dir() or linked(root) or root.resolve().parent != local:
        raise ValueError('快照目录无效。')
    target = root / name
    if not target.is_dir():
        raise FileNotFoundError('快照不存在，请刷新列表。')
    protected, error = protection(local)
    reason = deletion_reason(target, protected, error)
    if reason:
        raise ValueError(reason + '。')
    # Check every entry before deleting anything: never follow a junction out of
    # this snapshot, even if it appears inside an otherwise ordinary directory.
    def inspect(folder):
        for child in folder.iterdir():
            if linked(child) or not child.resolve().is_relative_to(target.resolve()):
                raise ValueError('快照包含链接或异常路径，未执行删除。')
            if child.is_dir():
                inspect(child)
    inspect(target)
    if target.resolve().parent != root.resolve():
        raise ValueError('快照路径无效。')
    shutil.rmtree(target)
