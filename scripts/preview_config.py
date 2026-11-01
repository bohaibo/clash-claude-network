"""Read-only configuration summaries; credentials and proxy URLs never leave the reader."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


def resolver(value):
    raw = str(value)
    if '://' in raw:
        try:
            parsed = urlsplit(raw.split('#', 1)[0])
            return parsed.scheme + '://' + (parsed.hostname or '地址隐藏') + ('/…' if parsed.path else '')
        except ValueError:
            return '地址格式未知（隐藏）'
    return raw if re.fullmatch(r'[\d.:]+|system|dhcp', raw) else '自定义解析器（地址隐藏）'


def network(config):
    tun = config.get('tun') or {}
    dns = config.get('dns') or {}
    yes = lambda v: '开启' if v is True else '关闭' if v is False else '未显式设置'
    servers = lambda key: '、'.join(resolver(v) for v in (dns.get(key) or [])) or '未设置'
    policies = dns.get('nameserver-policy') or {}
    return {
        'routing_simple': {'rule': '按规则分流', 'global': '全部通过代理', 'direct': '全部直接连接'}.get(config.get('mode'), '未能确认'),
        'dns_simple': yes(dns.get('enable')),
        'dns_protection': '开启' if (dns.get('enable') is True and dns.get('ipv6') is False and dns.get('enhanced-mode') == 'fake-ip' and tun.get('enable') is True and tun.get('strict-route') is True and {'any:53', 'tcp://any:53'}.issubset(set(tun.get('dns-hijack') or []))) else '未完整开启',
        'tun_simple': yes(tun.get('enable')),
        'strict_route_simple': yes(tun.get('strict-route')),
        'dns_filter_simple': '开启' if dns.get('nameserver') and all('disable-qtype-64=true' in str(v) and 'disable-qtype-65=true' in str(v) for v in dns.get('nameserver', [])) else '未发现开启',
        'routing': '模式 ' + str(config.get('mode', '未设置')) + '；规则 ' + str(len(config.get('rules') or [])) + ' 条；代理组 ' + str(len(config.get('proxy-groups') or [])) + ' 个（规则文件，不代表已验证实时流量）',
        'dns': '启用：' + yes(dns.get('enable')) + '；模式：' + str(dns.get('enhanced-mode', '未设置')) + '；DNS IPv6：' + yes(dns.get('ipv6')) + '；上游：' + servers('nameserver') + '；备用：' + servers('fallback') + '；引导：' + servers('default-nameserver') + '；分域策略 ' + str(len(policies)) + ' 项；Type 64/65 过滤：' + ('上游参数包含过滤' if any('disable-qtype-64=true' in str(v) and 'disable-qtype-65=true' in str(v) for v in dns.get('nameserver', [])) else '未在上游参数中发现完整过滤（未做 DNS 实测）'),
        'tun': '启用：' + yes(tun.get('enable')) + '；协议栈：' + str(tun.get('stack', '未设置')) + '；自动路由：' + yes(tun.get('auto-route')) + '；严格路由：' + yes(tun.get('strict-route')) + '；DNS 劫持：' + '、'.join(str(v) for v in tun.get('dns-hijack', [])) + '；全局 IPv6：' + yes(config.get('ipv6')),
    }


def app_proxy(name, path):
    path = Path(path)
    if not path.exists():
        return '配置文件不存在，未配置代理'
    text = path.read_text(encoding='utf-8-sig')
    if name == 'docker':
        groups = json.loads(text).get('proxies', {})
        fields = sorted({key for group in groups.values() if isinstance(group, dict) for key in group if key in ('httpProxy', 'httpsProxy', 'noProxy', 'allProxy')})
    else:
        pattern = r'(?im)^\s*(proxy|https-proxy)\s*=' if name == 'npm' else r'(?im)^\s*(proxy)\s*='
        fields = sorted(set(re.findall(pattern, text)))
    return '已设置：' + '、'.join(fields) + '（值隐藏）' if fields else '未发现代理字段（只读取项目会修改的文件）'

def winhttp():
    import ctypes
    from ctypes import wintypes
    class Config(ctypes.Structure):
        _fields_ = [('access', wintypes.DWORD), ('proxy', ctypes.c_void_p), ('bypass', ctypes.c_void_p)]
    api = ctypes.WinDLL('winhttp', use_last_error=True)
    api.WinHttpGetDefaultProxyConfiguration.argtypes = [ctypes.POINTER(Config)]
    api.WinHttpGetDefaultProxyConfiguration.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel.GlobalFree.restype = ctypes.c_void_p
    value = Config()
    if not api.WinHttpGetDefaultProxyConfiguration(ctypes.byref(value)):
        return '读取失败（未验证）'
    try:
        return {1: '直连（无 WinHTTP 默认代理）', 3: '已配置静态代理（地址及绕过列表隐藏）'}.get(value.access, '访问类型 ' + str(value.access) + '（未验证）')
    finally:
        if value.proxy: kernel.GlobalFree(value.proxy)
        if value.bypass: kernel.GlobalFree(value.bypass)
