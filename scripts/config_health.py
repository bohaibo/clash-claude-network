"""Read-only, credential-free configuration drift checks. No network I/O."""
import hashlib
from pathlib import Path
import region_profile

PROJECT = Path(__file__).resolve().parent.parent
GROUP = 'Claude-Overseas-Pinned'


def source_digest(project=PROJECT):
    digest = hashlib.sha256()
    for name in ('assets/secure-main.js', 'assets/claude-policy.json', 'scripts/client.py', 'scripts/safe_yaml.py', 'scripts/managed_chain.py', 'scripts/chain_profile.py'):
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
    if plan.get('chain'):
        import managed_chain
        from safe_yaml import loads
        try:
            managed_chain.check_source(Path(plan['root']), plan['chain'])
            managed_chain.check_fresh(Path(plan['root']), loads((Path(plan['root'])/'profiles.yaml').read_bytes()), plan['chain'])
            source = loads((Path(plan['root']) / 'profiles' / plan['chain']['file']).read_bytes())
            if source.get('x-portable-chain') != managed_chain.selection(plan['chain']):
                raise ValueError('Managed chain marker changed')
            managed_chain.audit(config, source)
            good = True
        except (OSError, ValueError, AssertionError, KeyError, TypeError):
            good = False
        add('链式方向与节点 DNS', good, True, '重新配置')
    add('配置生成版本', plan.get('source_digest'), source_digest(), '重新配置')
    add('启用的 IPv6 绑定数', current.get('ipv6_enabled_bindings'), 0, '补充配置')
    add('IPv6 全局禁用值', current.get('ipv6'), '255', '重新配置')
    region = region_profile.get(plan.get('region') or 'SG')
    for title, key, target in [('国家/地区', 'country_code', region['region']),
                               ('已保存区域格式', 'culture', region['culture']),
                               ('系统时区', 'timezone', region['timezone_id'])]:
        add(title, current.get(key), target, '补充配置')
    failed = [row for row in rows if row['status'] == 'failed']
    if any(row['status'] == 'unknown' for row in rows):
        recommendation = dict(action='inspect', reason='本地状态尚未读全，请刷新后选择操作。')
    elif any(row['action'] == '重新配置' for row in failed):
        recommendation = dict(action='apply', reason='规则、配置版本或全局设置不一致，请重新配置；无需先恢复最早状态。')
    elif failed:
        recommendation = dict(action='supplement', reason='当前检测到的差异仅涉及可补充的系统设置，建议补充配置。')
    else:
        recommendation = dict(action='none', reason='已检查的本地项目一致，无需重复完整配置；只需刷新分流名单时使用规则更新。')
    return dict(checks=rows, success=all(r['status'] == 'passed' for r in rows), recommendation=recommendation,
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
