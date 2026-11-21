"""Export the pinned routing profile from local Clash files."""
import ipaddress
import re
import tempfile
import uuid
from pathlib import Path

from config_health import GROUP
from safe_yaml import dumps, loads

FILES = {'android': 'android-clash-meta.yaml', 'ios': 'ios-shadowrocket.conf'}
POLICIES = {'DIRECT', 'REJECT', GROUP}
RULE_TYPES = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6', 'GEOIP', 'GEOSITE'}
UDP_GUARD = re.compile(r'AND,\(\(NETWORK,udp\),\(([^()]+)\)\),REJECT')


class ExportError(ValueError):
    pass


def pinned_profile(config, selected):
    nodes = {}
    groups = {}
    for field, index in [('proxies', nodes), ('proxy-groups', groups)]:
        for entry in config.get(field, []):
            name = entry['name']
            if name in nodes or name in groups:
                raise ExportError('当前配置存在重名节点或代理组，无法确认固定路径。')
            index[name] = entry

    ordered_nodes = {}
    ordered_groups = {}

    def visit(name, stack):
        if name in stack or name in {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'GLOBAL', 'COMPATIBLE'}:
            raise ExportError('固定代理路径含有循环或内置策略。')
        if name in ordered_nodes or name in ordered_groups:
            return
        if name in nodes:
            node = nodes[name]
            if node.get('type') in {None, 'direct', 'reject', 'pass', 'dns'} or not node.get('server'):
                raise ExportError('固定路径必须使用具体代理节点。')
            front = node.get('dialer-proxy')
            if front:
                visit(front, {*stack, name})
            ordered_nodes[name] = node
            return
        group = groups.get(name)
        if (not group or not group.get('proxies') or group.get('use') or group.get('include-all')
                or group.get('include-all-proxies') or group.get('include-all-providers')
                or group.get('dialer-proxy') or group.get('empty-fallback') not in (None, '', 'REJECT')
                or group.get('type') not in {'select', 'url-test', 'fallback', 'load-balance'}):
            raise ExportError('固定路径引用了无法随文件导出的节点来源。')
        for member in group['proxies']:
            visit(member, {*stack, name})
        ordered_groups[name] = group

    pinned = groups.get(GROUP, {})
    if (config.get('mode') != 'rule' or selected not in nodes or pinned.get('type') != 'select'
            or pinned.get('proxies') != [selected]):
        raise ExportError('当前 Clash 配置未使用记录中的固定节点。')
    visit(GROUP, set())

    rules = config['rules']
    if (not rules or rules[-1] != 'MATCH,' + GROUP
            or sum(rule.startswith('MATCH,') for rule in rules) != 1
            or 'GEOSITE,geolocation-!cn,' + GROUP not in rules
            or 'RULE-SET,strict-cn-domain,DIRECT' not in rules
            or rules.index('GEOSITE,geolocation-!cn,' + GROUP) >= rules.index('RULE-SET,strict-cn-domain,DIRECT')):
        raise ExportError('当前配置缺少固定代理兜底或海外优先分流。')
    rules = [rule for rule in rules if not rule.startswith('PROCESS-NAME,')]
    providers = {}
    for rule in rules:
        kind, _, policy, *_ = rule.split(',') + ['']
        if kind in RULE_TYPES | {'RULE-SET'} and policy not in POLICIES:
            raise ExportError('配置包含未纳入移动分流的策略。')
        if kind == 'RULE-SET':
            name = rule.split(',')[1]
            provider = config['rule-providers'][name]
            if provider.get('type') != 'inline' or not isinstance(provider.get('payload'), list):
                raise ExportError('分流引用了未内嵌的规则集。')
            providers[name] = {key: provider[key] for key in ('type', 'behavior', 'payload')}
        elif kind not in RULE_TYPES | {'MATCH', 'AND', 'NETWORK'}:
            raise ExportError('当前分流包含未支持的规则类型。')
        if ((kind == 'AND' and not UDP_GUARD.fullmatch(rule))
                or (kind == 'NETWORK' and rule != 'NETWORK,udp,REJECT')):
            raise ExportError('当前分流包含未支持的组合规则。')
    dns = dict(config['dns'])
    dns.pop('listen', None)
    return {'mode': 'rule', 'ipv6': False, 'allow-lan': False, 'geodata-mode': True,
            'dns': dns, 'proxies': list(ordered_nodes.values()), 'proxy-groups': list(ordered_groups.values()),
            'rule-providers': providers, 'rules': rules}


def read_varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError('Invalid geodata varint')


def protobuf_fields(data):
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        wire = tag & 7
        value, offset = read_varint(data, offset)
        if wire == 2:
            end = offset + value
            if end > len(data):
                raise ValueError('Truncated geodata')
            value = data[offset:end]
            offset = end
        elif wire != 0:
            raise ValueError('Unknown geodata encoding')
        yield tag >> 3, value


def geo_entries(path, code):
    key = code.upper().encode('ascii')
    for number, value in protobuf_fields(path.read_bytes()):
        if number != 1:
            continue
        fields = list(protobuf_fields(value))
        if any(field == 1 and name.upper() == key for field, name in fields):
            return [dict(protobuf_fields(data)) for field, data in fields if field == 2]
    raise ExportError('本机规则库缺少移动导出所需的 GeoSite 或 GeoIP 条目。')


def conf_atom(value):
    if (not isinstance(value, str) or value.strip() != value
            or any(char in ',#;\\"' or ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ExportError('Shadowrocket 无法安全转换当前配置中的文本字段。')
    return value


def shadowrocket_proxy(node):
    kind = node['type']
    fields = {
        'socks5': {'tls', 'username', 'password', 'skip-cert-verify', 'sni'},
        'http': {'tls', 'username', 'password', 'skip-cert-verify', 'sni'},
        'ss': {'cipher', 'password'},
        'trojan': {'password', 'sni', 'skip-cert-verify', 'network'},
        'vmess': {'uuid', 'cipher', 'alterId', 'tls', 'servername', 'skip-cert-verify', 'network'},
        'vless': {'uuid', 'tls', 'servername', 'skip-cert-verify', 'network'},
    }
    common = {'type', 'name', 'server', 'port', 'udp', 'tfo', 'dialer-proxy'}
    if (kind not in fields or node.keys() - common - fields[kind]
            or node.get('network', 'tcp') not in ('tcp', '')
            or type(node['port']) is not int or not 1 <= node['port'] <= 65535):
        raise ExportError('Shadowrocket 暂不支持无损转换当前节点的协议或传输参数。')
    server = conf_atom(node['server'])
    if not re.fullmatch(r'[a-zA-Z0-9._-]+', server):
        raise ExportError('Shadowrocket 暂不支持当前节点的地址格式。')
    tls = node.get('tls', kind == 'trojan')
    protocol = {'http': 'https', 'socks5': 'socks5-tls'}.get(kind, kind) if tls else kind
    options = [protocol, server, str(node['port'])]
    if kind in ('http', 'socks5'):
        username, password = node.get('username', ''), node.get('password', '')
        if bool(username) != bool(password):
            raise ExportError('Shadowrocket 无法转换不成对的代理认证字段。')
        if username:
            options.extend([conf_atom(username), conf_atom(password)])
    else:
        password = node.get('uuid') if kind in ('vmess', 'vless') else node.get('password')
        if not password:
            raise ExportError('Shadowrocket 节点缺少所需的认证字段。')
        options.append('password=' + conf_atom(password))
    if kind in ('ss', 'vmess'):
        options.append('method=' + conf_atom(node.get('cipher', 'auto') if kind == 'vmess' else node['cipher']))
    if kind == 'vmess':
        options.append('alterId=' + str(int(node.get('alterId', 0))))
    if kind in ('vmess', 'vless'):
        options.append('tls=' + ('true' if tls else 'false'))
    peer = node.get('sni') or node.get('servername')
    if peer:
        options.append('peer=' + conf_atom(peer))
    if node.get('skip-cert-verify'):
        options.append('skip-common-name-verify=true' if kind in ('http', 'socks5') else 'allowInsecure=1')
    options.append('udp=' + ('1' if node.get('udp') else '0'))
    if node.get('tfo'):
        options.append('tfo=1')
    return ','.join(options)


def ip_condition(value):
    network = ipaddress.ip_network(value)
    return ('IP-CIDR' if network.version == 4 else 'IP-CIDR6', str(network))


def provider_conditions(provider):
    for value in provider['payload']:
        behavior = provider['behavior']
        if behavior == 'domain':
            yield ('DOMAIN-SUFFIX', value[2:]) if value.startswith('+.') else ('DOMAIN', value)
        elif behavior == 'ipcidr':
            yield ip_condition(value)
        elif behavior == 'classical':
            yield tuple(value.split(','))
        else:
            raise ExportError('Shadowrocket 无法转换当前规则集格式。')


def shadowrocket(profile, root, export_id):
    if len(profile['proxy-groups']) != 1 or len(profile['proxies']) > 2:
        raise ExportError('当前前置代理组或多级代理链仅可导出为 Android 配置。')
    aliases = {node['name']: f'CCN-{export_id[:12]}-{i}' for i, node in enumerate(profile['proxies'], 1)}
    manual = []
    try:
        proxy_lines = [aliases[node['name']] + ' = ' + shadowrocket_proxy(node) for node in profile['proxies']]
    except ExportError:
        proxy_lines = []
        manual = [{'name': name, 'alias': alias} for name, alias in aliases.items()]
    exit_node = profile['proxies'][-1]
    member = aliases[exit_node['name']]
    front = exit_node.get('dialer-proxy')
    group = 'relay,' + aliases[front] + ',' + member if front else 'select,' + member

    entries = geo_entries(root / 'geosite.dat', 'geolocation-!cn')
    domain_types = {0: 'DOMAIN-KEYWORD', 2: 'DOMAIN-SUFFIX', 3: 'DOMAIN'}
    overseas = [(domain_types[item.get(1, 0)], item[2].decode('utf-8'))
                for item in entries if item.get(1, 0) in domain_types]
    skipped = len(entries) - len(overseas)
    if not overseas:
        raise ExportError('本机海外 GeoSite 没有可移植的域名条目。')
    cn_addresses = [ip_condition(f'{ipaddress.ip_address(item[1])}/{item.get(2, 0)}')
                    for item in geo_entries(root / 'geoip.dat', 'CN')]

    def conditions(kind, value):
        if kind == 'RULE-SET':
            return provider_conditions(profile['rule-providers'][value])
        if (kind, value) == ('GEOSITE', 'geolocation-!cn'):
            return overseas
        if (kind, value) == ('GEOIP', 'CN'):
            return cn_addresses
        if kind in RULE_TYPES - {'GEOSITE', 'GEOIP'}:
            return [(kind, value)]
        raise ExportError('Shadowrocket 无法转换当前地理规则。')

    lines = ['# Imported routing uses global mode Config.',
             '# Node credentials are private. GeoSite regex entries omitted: ' + str(skipped),
             '[General]', 'ipv6 = false', 'udp-policy-not-supported-behaviour = REJECT', '']
    if proxy_lines:
        lines.extend(['[Proxy]', *proxy_lines, ''])
    else:
        lines.append('# Before using, import the exact corresponding servers as ' + ', '.join(aliases.values()))
    lines.extend(['[Proxy Group]', GROUP + ' = ' + group, '', '[Rule]'])
    for rule in profile['rules']:
        if rule == 'MATCH,' + GROUP:
            lines.append('FINAL,' + GROUP)
            continue
        if rule == 'NETWORK,udp,REJECT':
            lines.append('PROTOCOL,UDP,REJECT')
            continue
        guard = UDP_GUARD.fullmatch(rule)
        if guard:
            kind, value, *flags = guard[1].split(',')
            policy = 'REJECT'
        else:
            kind, value, policy, *flags = rule.split(',')
        for match_kind, match_value, *provider_flags in conditions(kind, value):
            conf_atom(match_value)
            flags_to_write = list(dict.fromkeys(provider_flags + flags))
            if (match_kind not in RULE_TYPES - {'GEOSITE', 'GEOIP'}
                    or any(flag != 'no-resolve' for flag in flags_to_write)):
                raise ExportError('Shadowrocket 无法转换当前规则条件。')
            match = ','.join([match_kind, match_value])
            if guard:
                lines.append('AND,((PROTOCOL,UDP),(' + ','.join([match, *flags_to_write]) + ')),REJECT')
            else:
                lines.append(','.join([match, policy, *flags_to_write]))
    return '\n'.join(lines) + '\n', skipped, manual


def export(plan, local):
    if not all(plan.get(key) for key in ('root', 'node', 'subscription_uid', 'script_uid')):
        raise ExportError('请先应用一键配置，再导出手机配置。')
    root = Path(plan['root'])
    inputs = [root / 'profiles.yaml', root / 'clash-verge.yaml']
    profile_bytes, runtime_bytes = (path.read_bytes() for path in inputs)
    index = loads(profile_bytes)
    item = next((item for item in index.get('items', []) if item['uid'] == index.get('current')), {})
    if item.get('uid') != plan['subscription_uid'] or (item.get('option') or {}).get('script') != plan['script_uid']:
        raise ExportError('当前启用的 Clash 配置与上次一键配置不一致，请先切回已配置的配置文件。')
    profile = pinned_profile(loads(runtime_bytes), plan['node'])
    identifier = uuid.uuid4().hex
    files = {'android': dumps(profile, allow_unicode=True, sort_keys=False)}
    result = {}
    try:
        files['ios'], result['omitted_geo_regex'], result['ios_manual'] = shadowrocket(profile, root, identifier)
    except ExportError as exc:
        result['ios_error'] = str(exc)
    except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError):
        result['ios_error'] = 'Shadowrocket 转换未完成，请检查本机 GeoSite、GeoIP 规则库和当前节点配置。'
    if [path.read_bytes() for path in inputs] != [profile_bytes, runtime_bytes]:
        raise ExportError('导出时 Clash 配置发生变化，请重试。')

    parent = Path(local).resolve() / 'mobile-exports'
    if parent.resolve() != parent:
        raise ExportError('本地导出目录已被重定向。')
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=parent) as temp:
        folder = Path(temp)
        for platform, content in files.items():
            (folder / FILES[platform]).write_text(content, encoding='utf-8', newline='\n')
            result[platform] = '/api/mobile-exports/' + identifier + '/' + platform
        folder.rename(parent / identifier)
    return result
