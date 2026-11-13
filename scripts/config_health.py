"""Read-only, credential-free configuration drift checks. No network I/O."""
import hashlib
from pathlib import Path
import region_profile

PROJECT = Path(__file__).resolve().parent.parent
GROUP = 'Claude-Overseas-Pinned'


def source_digest(project=PROJECT):
    digest = hashlib.sha256()
    for name in ('assets/secure-main.js', 'assets/claude-policy.json', 'scripts/client.py'):
        digest.update((project / name).read_bytes())
    return digest.hexdigest()


def inspect(config, current, plan):
    rows = []
    def add(title, value, target, action):
        status = 'unknown' if value is None else 'passed' if value == target else 'failed'
        rows.append(dict(title=title, current=value, target=target, status=status, action=action))
    rules = config.get('rules', [])
    foreign = 'GEOSITE,geolocation-!cn,' + GROUP
    cn = 'RULE-SET,strict-cn-domain,DIRECT'
    add('海外域名优先', foreign in rules and cn in rules and rules.index(foreign) < rules.index(cn), True, '重新配置')
    dns = config.get('dns') or {}
    add('海外 DNS 优先', bool(dns.get('nameserver')) and dns.get('nameserver-policy', {}).get('geosite:geolocation-!cn') == dns.get('nameserver'), True, '重新配置')
    add('进程匹配', config.get('find-process-mode'), 'strict', '重新配置')
    add('配置生成版本', plan.get('source_digest'), source_digest(), '重新配置')
    add('启用的 IPv6 绑定数', current.get('ipv6_enabled_bindings'), 0, '补充配置')
    add('IPv6 全局禁用值', current.get('ipv6'), '255', '重新配置')
    region = region_profile.get(plan.get('region') or 'SG')
    for title, key, target in [('国家/地区', 'country_code', region['region']),
                               ('已保存区域格式', 'culture', region['culture']),
                               ('系统时区', 'timezone', region['timezone_id'])]:
        add(title, current.get(key), target, '补充配置')
    return dict(checks=rows, success=all(r['status'] == 'passed' for r in rows),
                scope='本地配置文件和系统状态；不代表内核已加载或网络实测通过',
                live_routing='未验证')


def assert_live(runtime, rules):
    """Only consume local controller responses; never probe a routed domain."""
    if runtime.get('mode') != 'rule' or runtime.get('find-process-mode') != 'strict':
        raise AssertionError('内核模式或进程匹配与当前版本不一致，请重新配置')
    values = rules.get('rules', [])
    foreign = next((i for i,r in enumerate(values) if r.get('type') == 'GeoSite' and r.get('payload') == 'geolocation-!cn' and r.get('proxy') == GROUP), None)
    cn = next((i for i,r in enumerate(values) if r.get('type') == 'RuleSet' and r.get('payload') == 'strict-cn-domain'), None)
    if foreign is None or cn is None or foreign >= cn:
        raise AssertionError('内核未加载海外优先规则，请重新配置')
