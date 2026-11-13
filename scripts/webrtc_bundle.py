"""Validate and prepare the included source add-on without network or profiles."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FILES = ('manifest.json', 'background.js', 'block.js', 'README.md')


def payload(project=PROJECT):
    source = Path(project) / 'assets' / 'webrtc-addon'
    record = json.loads((source / 'bundle.json').read_text(encoding='utf-8'))
    if set(record.get('sha256', {})) != set(FILES):
        raise ValueError('Invalid offline WebRTC file list')
    data = {}
    for name in FILES:
        path = source / name
        if path.is_symlink() or path.is_junction():
            raise ValueError('Linked offline WebRTC source rejected')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record['sha256'][name]:
            raise ValueError('Offline WebRTC add-on integrity check failed')
        data[name] = raw
    manifest = json.loads(data['manifest.json'])
    if manifest.get('version') != '1.0.0' or manifest.get('update_url'):
        raise ValueError('Unexpected offline WebRTC manifest')
    return data


def prepare(root=None, project=PROJECT):
    data = payload(project)
    root = Path(root) if root is not None else PROJECT / '.local' / 'webrtc-addon'
    if root.is_symlink() or root.is_junction():
        raise ValueError('Linked offline WebRTC destination rejected')
    root.mkdir(parents=True, exist_ok=True)
    target = root / '1.0.0'
    if target.exists():
        if target.is_symlink() or target.is_junction() or set(p.name for p in target.iterdir()) != set(FILES):
            raise ValueError('Offline WebRTC directory changed; keep it for review')
        for name, raw in data.items():
            path = target / name
            if path.is_symlink() or path.is_junction() or path.read_bytes() != raw:
                raise ValueError('Offline WebRTC files changed; refusing to overwrite')
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.prepare-', dir=root))
    try:
        for name, raw in data.items():
            (temporary / name).write_bytes(raw)
        os.rename(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def status():
    try:
        data = payload()
        target = PROJECT / '.local' / 'webrtc-addon' / '1.0.0'
        ready = not (target.is_symlink() or target.is_junction()) and all(
            (target / name).is_file() and not ((target / name).is_symlink() or (target / name).is_junction())
            and (target / name).read_bytes() == raw for name, raw in data.items())
        return {'available': True, 'prepared': ready, 'path': str(target), 'version': '1.0.0',
                'label': '本地扩展已就绪；首次需手动加载' if ready else '随项目附带；配置时准备本地扩展'}
    except (OSError, ValueError, TypeError):
        return {'available': False, 'prepared': False, 'label': '本地扩展缺失或校验失败，请修复项目文件'}
