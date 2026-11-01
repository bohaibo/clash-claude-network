"""Infer an exit-region hint from a node name.

This is a hint only. The real exit country is verified after the node is
configured; a wrong hint must never silently replace the user's choice.
"""
from __future__ import annotations

import re

ISO = {
    'AE', 'AR', 'AT', 'AU', 'BE', 'BR', 'CA', 'CH', 'CL', 'CN', 'CZ', 'DE', 'DK',
    'EG', 'ES', 'FI', 'FR', 'GB', 'GR', 'HK', 'HU', 'ID', 'IE', 'IL', 'IN', 'IT',
    'JP', 'KR', 'KZ', 'MO', 'MX', 'MY', 'NL', 'NO', 'NZ', 'PH', 'PL', 'PT', 'RO',
    'RS', 'RU', 'SA', 'SE', 'SG', 'TH', 'TR', 'TW', 'UA', 'US', 'VN', 'ZA',
}

# Longer names first so "united states" is not shadowed by "states".
NAME_MAP = {
    '新加坡': 'SG', 'singapore': 'SG',
    '日本': 'JP', 'japan': 'JP',
    '美国': 'US', 'united states': 'US', 'america': 'US', 'usa': 'US',
    '英国': 'GB', 'united kingdom': 'GB', 'britain': 'GB', 'england': 'GB', 'uk': 'GB',
    '德国': 'DE', 'germany': 'DE',
    '法国': 'FR', 'france': 'FR',
    '荷兰': 'NL', 'netherlands': 'NL', 'holland': 'NL',
    '加拿大': 'CA', 'canada': 'CA',
    '澳大利亚': 'AU', '澳洲': 'AU', 'australia': 'AU',
    '韩国': 'KR', 'south korea': 'KR', 'korea': 'KR',
    '香港': 'HK', 'hong kong': 'HK',
    '台湾': 'TW', 'taiwan': 'TW',
    '印度': 'IN', 'india': 'IN',
    '俄罗斯': 'RU', 'russia': 'RU',
    '土耳其': 'TR', 'turkey': 'TR',
    '巴西': 'BR', 'brazil': 'BR',
    '阿根廷': 'AR', 'argentina': 'AR',
    '意大利': 'IT', 'italy': 'IT',
    '西班牙': 'ES', 'spain': 'ES',
    '瑞典': 'SE', 'sweden': 'SE',
    '瑞士': 'CH', 'switzerland': 'CH',
    '芬兰': 'FI', 'finland': 'FI',
    '挪威': 'NO', 'norway': 'NO',
    '丹麦': 'DK', 'denmark': 'DK',
    '波兰': 'PL', 'poland': 'PL',
    '爱尔兰': 'IE', 'ireland': 'IE',
    '奥地利': 'AT', 'austria': 'AT',
    '比利时': 'BE', 'belgium': 'BE',
    '葡萄牙': 'PT', 'portugal': 'PT',
    '捷克': 'CZ', 'czech': 'CZ',
    '匈牙利': 'HU', 'hungary': 'HU',
    '罗马尼亚': 'RO', 'romania': 'RO',
    '塞尔维亚': 'RS', 'serbia': 'RS',
    '乌克兰': 'UA', 'ukraine': 'UA',
    '哈萨克斯坦': 'KZ', 'kazakhstan': 'KZ',
    '阿联酋': 'AE', 'uae': 'AE', 'united arab emirates': 'AE',
    '沙特': 'SA', 'saudi arabia': 'SA',
    '以色列': 'IL', 'israel': 'IL',
    '南非': 'ZA', 'south africa': 'ZA',
    '墨西哥': 'MX', 'mexico': 'MX',
    '智利': 'CL', 'chile': 'CL',
    '泰国': 'TH', 'thailand': 'TH',
    '越南': 'VN', 'vietnam': 'VN',
    '马来西亚': 'MY', 'malaysia': 'MY',
    '印尼': 'ID', '印度尼西亚': 'ID', 'indonesia': 'ID',
    '菲律宾': 'PH', 'philippines': 'PH',
    '新西兰': 'NZ', 'new zealand': 'NZ',
    '澳门': 'MO', 'macau': 'MO', 'macao': 'MO',
    '希腊': 'GR', 'greece': 'GR',
    '埃及': 'EG', 'egypt': 'EG',
}
_NAME_KEYS = sorted(NAME_MAP, key=len, reverse=True)
_ISO_CODE = re.compile(r'(?<![A-Za-z])([A-Za-z]{2})(?![A-Za-z])')
_ISO_PREFIX = re.compile(r'(?<![A-Za-z])([A-Za-z]{2})[-_]?\d{1,3}(?![A-Za-z])')


def _flags(name):
    values = set()
    chars = list(name)
    for index in range(len(chars) - 1):
        first, second = ord(chars[index]), ord(chars[index + 1])
        if 0x1F1E6 <= first <= 0x1F1FF and 0x1F1E6 <= second <= 0x1F1FF:
            values.add(chr(first - 0x1F1E6 + 65) + chr(second - 0x1F1E6 + 65))
    return {value for value in values if value in ISO}


def infer(name):
    if not isinstance(name, str) or not name.strip():
        return None
    text = name.strip()
    lower = text.lower()
    candidates = []

    for code in _flags(text):
        candidates.append((code, 'emoji', 1))

    for key in _NAME_KEYS:
        if key in lower:
            candidates.append((NAME_MAP[key], 'name', 2))
            break

    for match in _ISO_CODE.finditer(text):
        code = match.group(1).upper()
        if code in ISO:
            candidates.append((code, 'code', 3))
    for match in _ISO_PREFIX.finditer(text):
        code = match.group(1).upper()
        if code in ISO:
            candidates.append((code, 'code', 3))

    if not candidates:
        return None
    codes = {item[0] for item in candidates}
    if len(codes) != 1:
        return None
    code = next(iter(codes))
    source = min((item for item in candidates if item[0] == code), key=lambda item: item[2])[1]
    return {'code': code, 'source': source}


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps({name: infer(name) for name in sys.argv[1:]}, ensure_ascii=False))
