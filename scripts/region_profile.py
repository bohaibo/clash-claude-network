"""Country-level Windows locale and timezone profiles.

The profile is a country-level default. A country with several time zones uses
its most common/default zone; the selected city is not inferred from a node name.
"""
from __future__ import annotations

import copy
import json
import re
import sys

REGION_RE = re.compile(r'^[A-Za-z]{2}$')

PROFILES = {
    'SG': {'label': '新加坡', 'culture': 'zh-SG', 'language_tag': 'zh-Hans-SG', 'fallback': 'en-SG',
           'timezone_id': 'Singapore Standard Time', 'iana': 'Asia/Singapore'},
    'US': {'label': '美国', 'culture': 'en-US', 'language_tag': 'en-US', 'fallback': '',
           'timezone_id': 'Eastern Standard Time', 'iana': 'America/New_York'},
    'JP': {'label': '日本', 'culture': 'ja-JP', 'language_tag': 'ja-JP', 'fallback': 'en-US',
           'timezone_id': 'Tokyo Standard Time', 'iana': 'Asia/Tokyo'},
    'GB': {'label': '英国', 'culture': 'en-GB', 'language_tag': 'en-GB', 'fallback': 'en-US',
           'timezone_id': 'GMT Standard Time', 'iana': 'Europe/London'},
    'DE': {'label': '德国', 'culture': 'de-DE', 'language_tag': 'de-DE', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Berlin'},
    'FR': {'label': '法国', 'culture': 'fr-FR', 'language_tag': 'fr-FR', 'fallback': 'en-US',
           'timezone_id': 'Romance Standard Time', 'iana': 'Europe/Paris'},
    'NL': {'label': '荷兰', 'culture': 'nl-NL', 'language_tag': 'nl-NL', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Amsterdam'},
    'CA': {'label': '加拿大', 'culture': 'en-CA', 'language_tag': 'en-CA', 'fallback': 'en-US',
           'timezone_id': 'Eastern Standard Time', 'iana': 'America/Toronto'},
    'AU': {'label': '澳大利亚', 'culture': 'en-AU', 'language_tag': 'en-AU', 'fallback': 'en-US',
           'timezone_id': 'AUS Eastern Standard Time', 'iana': 'Australia/Sydney'},
    'KR': {'label': '韩国', 'culture': 'ko-KR', 'language_tag': 'ko-KR', 'fallback': 'en-US',
           'timezone_id': 'Korea Standard Time', 'iana': 'Asia/Seoul'},
    'HK': {'label': '中国香港', 'culture': 'zh-HK', 'language_tag': 'zh-HK', 'fallback': 'en-US',
           'timezone_id': 'China Standard Time', 'iana': 'Asia/Hong_Kong'},
    'TW': {'label': '中国台湾', 'culture': 'zh-TW', 'language_tag': 'zh-Hant-TW', 'fallback': 'en-US',
           'timezone_id': 'Taipei Standard Time', 'iana': 'Asia/Taipei'},
    'IN': {'label': '印度', 'culture': 'en-IN', 'language_tag': 'en-IN', 'fallback': 'en-US',
           'timezone_id': 'India Standard Time', 'iana': 'Asia/Kolkata'},
    'TH': {'label': '泰国', 'culture': 'th-TH', 'language_tag': 'th-TH', 'fallback': 'en-US',
           'timezone_id': 'SE Asia Standard Time', 'iana': 'Asia/Bangkok'},
    'VN': {'label': '越南', 'culture': 'vi-VN', 'language_tag': 'vi-VN', 'fallback': 'en-US',
           'timezone_id': 'SE Asia Standard Time', 'iana': 'Asia/Ho_Chi_Minh'},
    'MY': {'label': '马来西亚', 'culture': 'ms-MY', 'language_tag': 'ms-MY', 'fallback': 'en-US',
           'timezone_id': 'Singapore Standard Time', 'iana': 'Asia/Kuala_Lumpur'},
    'ID': {'label': '印度尼西亚', 'culture': 'id-ID', 'language_tag': 'id-ID', 'fallback': 'en-US',
           'timezone_id': 'SE Asia Standard Time', 'iana': 'Asia/Jakarta'},
    'PH': {'label': '菲律宾', 'culture': 'en-PH', 'language_tag': 'en-PH', 'fallback': 'en-US',
           'timezone_id': 'Singapore Standard Time', 'iana': 'Asia/Manila'},
    'NZ': {'label': '新西兰', 'culture': 'en-NZ', 'language_tag': 'en-NZ', 'fallback': 'en-US',
           'timezone_id': 'New Zealand Standard Time', 'iana': 'Pacific/Auckland'},
    'BR': {'label': '巴西', 'culture': 'pt-BR', 'language_tag': 'pt-BR', 'fallback': 'en-US',
           'timezone_id': 'E. South America Standard Time', 'iana': 'America/Sao_Paulo'},
    'AR': {'label': '阿根廷', 'culture': 'es-AR', 'language_tag': 'es-AR', 'fallback': 'en-US',
           'timezone_id': 'Argentina Standard Time', 'iana': 'America/Argentina/Buenos_Aires'},
    'MX': {'label': '墨西哥', 'culture': 'es-MX', 'language_tag': 'es-MX', 'fallback': 'en-US',
           'timezone_id': 'Central Standard Time (Mexico)', 'iana': 'America/Mexico_City'},
    'CL': {'label': '智利', 'culture': 'es-CL', 'language_tag': 'es-CL', 'fallback': 'en-US',
           'timezone_id': 'Pacific SA Standard Time', 'iana': 'America/Santiago'},
    'ZA': {'label': '南非', 'culture': 'en-ZA', 'language_tag': 'en-ZA', 'fallback': 'en-US',
           'timezone_id': 'South Africa Standard Time', 'iana': 'Africa/Johannesburg'},
    'AE': {'label': '阿联酋', 'culture': 'ar-AE', 'language_tag': 'ar-AE', 'fallback': 'en-US',
           'timezone_id': 'Arabian Standard Time', 'iana': 'Asia/Dubai'},
    'SA': {'label': '沙特', 'culture': 'ar-SA', 'language_tag': 'ar-SA', 'fallback': 'en-US',
           'timezone_id': 'Arab Standard Time', 'iana': 'Asia/Riyadh'},
    'IL': {'label': '以色列', 'culture': 'he-IL', 'language_tag': 'he-IL', 'fallback': 'en-US',
           'timezone_id': 'Israel Standard Time', 'iana': 'Asia/Jerusalem'},
    'TR': {'label': '土耳其', 'culture': 'tr-TR', 'language_tag': 'tr-TR', 'fallback': 'en-US',
           'timezone_id': 'Turkey Standard Time', 'iana': 'Europe/Istanbul'},
    'RU': {'label': '俄罗斯', 'culture': 'ru-RU', 'language_tag': 'ru-RU', 'fallback': 'en-US',
           'timezone_id': 'Russian Standard Time', 'iana': 'Europe/Moscow'},
    'UA': {'label': '乌克兰', 'culture': 'uk-UA', 'language_tag': 'uk-UA', 'fallback': 'en-US',
           'timezone_id': 'FLE Standard Time', 'iana': 'Europe/Kyiv'},
    'PL': {'label': '波兰', 'culture': 'pl-PL', 'language_tag': 'pl-PL', 'fallback': 'en-US',
           'timezone_id': 'Central European Standard Time', 'iana': 'Europe/Warsaw'},
    'IT': {'label': '意大利', 'culture': 'it-IT', 'language_tag': 'it-IT', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Rome'},
    'ES': {'label': '西班牙', 'culture': 'es-ES', 'language_tag': 'es-ES', 'fallback': 'en-US',
           'timezone_id': 'Romance Standard Time', 'iana': 'Europe/Madrid'},
    'PT': {'label': '葡萄牙', 'culture': 'pt-PT', 'language_tag': 'pt-PT', 'fallback': 'en-US',
           'timezone_id': 'GMT Standard Time', 'iana': 'Europe/Lisbon'},
    'IE': {'label': '爱尔兰', 'culture': 'en-IE', 'language_tag': 'en-IE', 'fallback': 'en-US',
           'timezone_id': 'GMT Standard Time', 'iana': 'Europe/Dublin'},
    'SE': {'label': '瑞典', 'culture': 'sv-SE', 'language_tag': 'sv-SE', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Stockholm'},
    'CH': {'label': '瑞士', 'culture': 'de-CH', 'language_tag': 'de-CH', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Zurich'},
    'FI': {'label': '芬兰', 'culture': 'fi-FI', 'language_tag': 'fi-FI', 'fallback': 'en-US',
           'timezone_id': 'FLE Standard Time', 'iana': 'Europe/Helsinki'},
    'NO': {'label': '挪威', 'culture': 'nb-NO', 'language_tag': 'nb-NO', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Oslo'},
    'DK': {'label': '丹麦', 'culture': 'da-DK', 'language_tag': 'da-DK', 'fallback': 'en-US',
           'timezone_id': 'Romance Standard Time', 'iana': 'Europe/Copenhagen'},
    'AT': {'label': '奥地利', 'culture': 'de-AT', 'language_tag': 'de-AT', 'fallback': 'en-US',
           'timezone_id': 'W. Europe Standard Time', 'iana': 'Europe/Vienna'},
    'BE': {'label': '比利时', 'culture': 'nl-BE', 'language_tag': 'nl-BE', 'fallback': 'en-US',
           'timezone_id': 'Romance Standard Time', 'iana': 'Europe/Brussels'},
    'CZ': {'label': '捷克', 'culture': 'cs-CZ', 'language_tag': 'cs-CZ', 'fallback': 'en-US',
           'timezone_id': 'Central Europe Standard Time', 'iana': 'Europe/Prague'},
    'HU': {'label': '匈牙利', 'culture': 'hu-HU', 'language_tag': 'hu-HU', 'fallback': 'en-US',
           'timezone_id': 'Central Europe Standard Time', 'iana': 'Europe/Budapest'},
    'RO': {'label': '罗马尼亚', 'culture': 'ro-RO', 'language_tag': 'ro-RO', 'fallback': 'en-US',
           'timezone_id': 'E. Europe Standard Time', 'iana': 'Europe/Bucharest'},
    'GR': {'label': '希腊', 'culture': 'el-GR', 'language_tag': 'el-GR', 'fallback': 'en-US',
           'timezone_id': 'GTB Standard Time', 'iana': 'Europe/Athens'},
    'RS': {'label': '塞尔维亚', 'culture': 'sr-Latn-RS', 'language_tag': 'sr-Latn-RS', 'fallback': 'en-US',
           'timezone_id': 'Central Europe Standard Time', 'iana': 'Europe/Belgrade'},
    'KZ': {'label': '哈萨克斯坦', 'culture': 'kk-KZ', 'language_tag': 'kk-KZ', 'fallback': 'ru-RU',
           'timezone_id': 'Central Asia Standard Time', 'iana': 'Asia/Almaty'},
    'EG': {'label': '埃及', 'culture': 'ar-EG', 'language_tag': 'ar-EG', 'fallback': 'en-US',
           'timezone_id': 'Egypt Standard Time', 'iana': 'Africa/Cairo'},
    'NG': {'label': '尼日利亚', 'culture': 'en-NG', 'language_tag': 'en-NG', 'fallback': 'en-US',
           'timezone_id': 'W. Central Africa Standard Time', 'iana': 'Africa/Lagos'},
    'KE': {'label': '肯尼亚', 'culture': 'en-KE', 'language_tag': 'en-KE', 'fallback': 'en-US',
           'timezone_id': 'E. Africa Standard Time', 'iana': 'Africa/Nairobi'},
    'MA': {'label': '摩洛哥', 'culture': 'ar-MA', 'language_tag': 'ar-MA', 'fallback': 'fr-FR',
           'timezone_id': 'Morocco Standard Time', 'iana': 'Africa/Casablanca'},
    'MO': {'label': '中国澳门', 'culture': 'zh-MO', 'language_tag': 'zh-MO', 'fallback': 'en-US',
           'timezone_id': 'China Standard Time', 'iana': 'Asia/Macau'},
}


def get(region):
    code = str(region or '').strip().upper()
    if not REGION_RE.fullmatch(code):
        raise ValueError('出口地区须为两位国家代码。')
    profile = copy.deepcopy(PROFILES.get(code, PROFILES['SG']))
    profile['region'] = code
    profile['known'] = code in PROFILES
    profile['fallback_language_tag'] = profile['fallback']
    return profile


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--region', required=True)
    args = parser.parse_args()
    print(json.dumps(get(args.region), ensure_ascii=False))
