"""GUI-owned settings that are not machine configuration.

Only rule-update scheduling lives here. Machine settings stay in the existing
project records; this file must never contain nodes, subscriptions or secrets.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT / '.local'
FILENAME = 'ui-settings.json'
MODES = ('manual', 'scheduled')
TIME_PATTERN = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
MAX_DAYS = 365
DEFAULT = {'version': 2, 'update': {'mode': 'scheduled', 'time': '13:00', 'days': 1}}


def settings_path(local=None):
    return (Path(local) if local else LOCAL) / FILENAME


def validate(mode, time_value, days=1):
    if mode not in MODES:
        raise ValueError('规则更新方式无效。')
    if not isinstance(time_value, str) or not TIME_PATTERN.fullmatch(time_value):
        raise ValueError('更新时间须为 HH:MM（00:00–23:59）。')
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValueError('更新间隔必须是 1–365 的整数天。') from None
    if days < 1 or days > MAX_DAYS:
        raise ValueError('更新间隔必须是 1–365 的整数天。')
    return mode, time_value, days


def load(local=None):
    path = settings_path(local)
    data = json.loads(json.dumps(DEFAULT))
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('界面设置文件损坏；未使用默认值覆盖。') from exc
        if not isinstance(stored, dict):
            raise ValueError('界面设置文件格式无效。')
        update = stored.get('update')
        if not isinstance(update, dict):
            raise ValueError('界面设置缺少更新方式。')
        data['update']['mode'] = update.get('mode', data['update']['mode'])
        data['update']['time'] = update.get('time', data['update']['time'])
        data['update']['days'] = update.get('days', data['update'].get('days', 1))
    validate(data['update']['mode'], data['update']['time'], data['update']['days'])
    return data


def save(mode, time_value, days=1, local=None):
    validate(mode, time_value, days)
    data = {'version': 2, 'update': {'mode': mode, 'time': time_value, 'days': int(days)}}
    root = Path(local) if local else LOCAL
    root.mkdir(parents=True, exist_ok=True)
    path = settings_path(local)
    temporary = path.with_name(path.name + '.new')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return data


def update(local=None):
    return load(local)['update']
