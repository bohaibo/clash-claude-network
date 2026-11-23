"""Managed proxy profiles for the normal Windows apply/restore lifecycle."""
import copy, ipaddress, json, re, uuid
from pathlib import Path
import chain_profile, routing_checks
from checks import require
from safe_yaml import loads

REGISTRY = 'managed-chains.json'
FRONT = chain_profile.FRONT
AUTO_TYPES = {'url-test': 'URLTest', 'fallback': 'Fallback'}
HEALTH_URLS = {f'{scheme}://{host}/generate_204'
               for scheme in ('http', 'https') for host in ('cp.cloudflare.com', 'www.gstatic.com')}
AUTO_PROXY_TYPES = {'ss', 'ssr', 'vmess', 'vless', 'trojan', 'socks5', 'http', 'snell',
                    'hysteria', 'hysteria2', 'tuic', 'wireguard', 'ssh', 'anytls'}
RESERVED_NAMES = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE', 'GLOBAL',
                  chain_profile.GROUP, FRONT, 'Chain-Exit'}


def static_exit(value):
    """Validate a manually entered IPv4 proxy without echoing credentials."""
    fields = {'protocol', 'ip', 'port', 'username', 'password'}
    require(isinstance(value, dict) and set(value) == fields, 'Invalid static proxy fields')
    protocol = value['protocol']
    require(protocol in ('socks5', 'http', 'https'), 'Unsupported static proxy protocol')
    try:
        address = ipaddress.IPv4Address(value['ip']) if isinstance(value['ip'], str) else None
    except ValueError:
        address = None
    require(address and address.is_global and not address.is_multicast and not address.is_reserved,
            'Static proxy requires a public IPv4 address')
    port = value['port']
    require(type(port) is int and 1 <= port <= 65535, 'Invalid static proxy port')
    username, password = value['username'], value['password']
    require(all(isinstance(v, str) and len(v.encode('utf-8', errors='surrogatepass')) <= 255
                and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in v)
                for v in (username, password)) and bool(username) == bool(password),
            'Static proxy requires both username and password, or neither')
    require(protocol == 'socks5' or ':' not in username, 'Invalid HTTP proxy username')
    return dict(value, ip=str(address))


def anchor(record):
    if record.get('direct'):
        return None
    if 'static_exit' in record:
        return record['front_uid'], record['front_node']
    return record['source_uid'], record['exit_node']


def traffic(profiles, record):
    """Use the remaining-quota bottleneck's account counters, never invented chain usage."""
    if record.get('direct'):
        return None
    sources = []
    keys = ('front_uid',) if 'static_exit' in record else ('front_uid', 'source_uid')
    for key in keys:
        item = next((i for i in profiles['items'] if i.get('uid') == record[key] and i.get('type') == 'remote'), None)
        extra = item.get('extra') if item else None
        if not isinstance(extra, dict):
            return None
        values = {k: extra.get(k) for k in ('upload', 'download', 'total')}
        if any(type(v) is not int or not 0 <= v <= 2**64-1 for v in values.values()) or not values['total']:
            return None
        if values['upload'] + values['download'] > 2**64-1:
            return None
        expiry = extra.get('expire', 0)
        if type(expiry) is not int or not 0 <= expiry <= 253402300799:
            return None
        sources.append(dict(uid=item['uid'], **values, expire=expiry,
                            remaining=max(0, values['total']-values['upload']-values['download'])))
    limiting = min(sources, key=lambda s: s['remaining'])
    expiries = [s['expire'] for s in sources if s['expire'] > 0]
    extra = {k: limiting[k] for k in ('upload', 'download', 'total')}
    extra['expire'] = min(expiries, default=0)
    return {'extra': extra, 'remaining': limiting['remaining'],
            'source_uid': limiting['uid'], 'shared_subscription': record['front_uid'] == record.get('source_uid')}


def sync_traffic(profiles, record):
    item = next((i for i in profiles['items'] if i.get('uid') == record['uid'] and
                 i.get('type') == 'local' and i.get('file') == record['file']), None)
    if item is None:
        return False
    summary = traffic(profiles, record)
    extra = summary['extra'] if summary else None
    changed = item.get('extra') != extra
    if extra is None:
        item.pop('extra', None)
    else:
        item['extra'] = extra
    return changed


def traffic_profiles(root, profiles, local):
    updated = copy.deepcopy(profiles)
    for record in records(root, local).values():
        if not record.get('legacy'):
            sync_traffic(updated, record)
    return updated


def records(root, local):
    path = Path(local) / REGISTRY
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    require(isinstance(rows, dict), 'Invalid managed chain registry')
    legacy = Path(local) / chain_profile.REGISTRY
    if legacy.exists():
        value = json.loads(legacy.read_text(encoding='utf-8'))
        if value.get('uid') not in rows:
            rows[value['uid']] = dict(value, legacy=True)
    return {uid: r for uid, r in rows.items() if Path(r['root']).resolve() == Path(root).resolve()}


def registered(root, profiles, uid, local):
    record = records(root, local).get(uid)
    if not record:
        return None
    entry = next((i for i in profiles['items'] if i.get('uid') == uid), None)
    require(entry is not None and entry.get('type') == 'local' and entry.get('file') == record['file'],
            'Managed chain association changed; review required')
    return record


def selection(record):
    fields = {'name': record['node']}
    if record.get('direct'):
        fields['direct'] = True
    else:
        fields.update(front_subscription=record['front_uid'], front_node=record['front_node'])
        if record.get('front_group'):
            fields['front_group'] = True
    if 'static_exit' in record:
        endpoint = record['static_exit']
        return dict(fields, static_exit={key: endpoint[key] for key in ('ip', 'port', 'protocol')})
    return dict(fields, exit_subscription=record['source_uid'], exit_node=record['exit_node'])


def automatic_front(source, name):
    groups = [g for g in source.get('proxy-groups') or [] if g.get('name') == name]
    require(len(groups) == 1 and name not in RESERVED_NAMES, 'Exact automatic front group missing or duplicated')
    group = groups[0]
    members = group.get('proxies')
    require(group.get('type') in AUTO_TYPES and group.get('url') in HEALTH_URLS and
            type(group.get('interval')) is int and group['interval'] > 0 and
            not any(group.get(key) for key in ('use', 'include-all', 'include-all-proxies',
                                               'include-all-providers', 'dialer-proxy')) and
            group.get('empty-fallback') in (None, '', 'REJECT') and
            isinstance(members, list) and members and all(isinstance(n, str) for n in members) and
            len(set(members)) == len(members), 'Unsupported automatic front group')
    proxies = source.get('proxies') or []
    node_map = {p['name']: p for p in proxies}
    require(name not in node_map and len(node_map) == len(proxies), 'Ambiguous automatic front group or members')
    nodes = [node_map.get(member) for member in members]
    require(all(p and p['name'] not in RESERVED_NAMES and p.get('type') in AUTO_PROXY_TYPES and
                isinstance(p.get('server'), str) and p['server'] and not p.get('dialer-proxy')
                for p in nodes), 'Automatic front group must contain only concrete proxy nodes')
    options = ('type', 'url', 'interval', 'lazy', 'timeout', 'max-failed-times', 'expected-status',
               'filter', 'exclude-filter', 'exclude-type', 'tolerance', 'disable-udp')
    policy = {k: group[k] for k in options if k in group}
    return nodes, dict(policy, name=FRONT, hidden=True, **{'empty-fallback': 'REJECT'})


def automatic_front_names(source):
    names = []
    for group in source.get('proxy-groups') or []:
        name = group.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            continue
        try:
            automatic_front(source, name)
        except AssertionError:
            continue
        names.append(name)
    return names


def front_source(root, profiles, record):
    source = chain_profile.subscription(root, profiles, record['front_uid'], record['front_file'])
    name = record['front_node']
    if not record.get('front_group'):
        node = chain_profile.exact_node(source, name)
        return [dict(node, name=FRONT)], None, chain_profile.node_dns(source, [node])
    nodes, group = automatic_front(source, name)
    if record['front_uid'] == record.get('source_uid'):
        nodes = [p for p in nodes if p['name'] != record['exit_node']]
    require(nodes and all(p['name'] != record['node'] for p in nodes),
            'Automatic front group is empty or a member matches the new profile name')
    group['proxies'] = [p['name'] for p in nodes]
    return nodes, group, chain_profile.node_dns(source, nodes)


def resolve(root, profiles, request, local):
    require(isinstance(request, dict), 'Invalid chain selection')
    name = request.get('name')
    require(isinstance(name, str) and name == name.strip() and 0 < len(name) <= 100 and
            not any(ord(c) < 32 for c in name) and name not in RESERVED_NAMES and name not in
            ('Claude-Overseas-Pinned', FRONT, 'Chain-Exit'),
            'Invalid chain name')
    record = {'root': str(Path(root).resolve()), 'node': name, 'name': name}
    if 'front_group' in request:
        require(request['front_group'] is True and 'direct' not in request, 'Invalid automatic front selection')
        record['front_group'] = True
    hops = [('front', 'front_uid', 'front_file', 'front_node')]
    if 'direct' in request:
        require(request['direct'] is True and set(request) == {'name', 'direct', 'static_exit'},
                'Invalid single static proxy selection')
        record['direct'] = True
        hops = []
    if 'static_exit' in request:
        record['static_exit'] = static_exit(request['static_exit'])
    else:
        hops.append(('exit', 'source_uid', 'source_file', 'exit_node'))
    for side, uidkey, filekey, nodekey in hops:
        uid, node = request.get(side + '_subscription'), request.get(side + '_node')
        matches = [i for i in profiles['items'] if i.get('type') == 'remote' and i.get('uid') == uid]
        require(len(matches) == 1 and isinstance(node, str) and node, 'Exact chain source subscription missing')
        record.update({uidkey: uid, filekey: matches[0]['file'], nodekey: node})
        if side == 'exit' or not record.get('front_group'):
            chain_profile.source_node(root, profiles, uid, matches[0]['file'], node)
    if record.get('front_group'):
        front_source(root, profiles, record)
    require('source_uid' not in record or anchor(record) != (record['front_uid'], record['front_node']),
            'A chain cannot use the same node twice')
    known = records(root, local)
    active_uids = {i.get('uid') for i in (profiles.get('items') or [])}
    active_known = {uid: r for uid, r in known.items() if uid in active_uids}
    identity = selection(record)
    matches = [r for r in active_known.values() if selection(r) == identity and
               r.get('static_exit') == record.get('static_exit')]
    if matches:
        require(len(matches) == 1, 'Duplicate managed chain selection')
        return matches[0]
    require(not any(r.get('node') == name for r in active_known.values()), 'Chain name already exists; choose another name')
    uid = 'ManagedChain' + uuid.uuid4().hex[:12]
    return dict(record, uid=uid, file=uid + '.yaml')


def check_source(root, record):
    path = chain_profile.profile_file(root, record['file'])
    if path.exists():
        require(record.get('sha256') == chain_profile.digest(path.read_bytes()),
                'Managed chain source changed; review required')


def check_fresh(root, profiles, record):
    check_source(root, record)
    require(record.get('source_fingerprint') == source_fingerprint(root, profiles, record),
            'Managed chain source parameters changed; update rules or reconfigure')


def static_node(endpoint):
    protocol = endpoint['protocol']
    node = {'type': 'socks5' if protocol == 'socks5' else 'http',
            'server': endpoint['ip'], 'port': endpoint['port']}
    if protocol == 'socks5':
        node['udp'] = False
    if protocol == 'https':
        node['tls'] = True
    if endpoint['username']:
        node.update(username=endpoint['username'], password=endpoint['password'])
    return node


def source_fingerprint(root, profiles, record):
    if record.get('front_group'):
        value = source(root, profiles, record)
        return chain_profile.digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8'))
    if 'static_exit' not in record:
        return chain_profile.source_fingerprint(root, profiles, record)
    value = {'nodes': [static_node(static_exit(record['static_exit']))], 'front_dns': {}}
    if not record.get('direct'):
        value['nodes'].append(chain_profile.source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node']))
        value['front_dns'] = chain_profile.front_dns(root, profiles, record)
    return chain_profile.digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8'))


def source(root, profiles, record):
    """Build an independent local profile from the exact front and exit."""
    if 'static_exit' in record:
        exit_node = static_node(static_exit(record['static_exit']))
    else:
        exit_node = copy.deepcopy(chain_profile.source_node(root, profiles, record['source_uid'], record['source_file'], record['exit_node']))
    exit_node['name'] = record['node']
    nodes, original, front_dns, front_group = [exit_node], {}, {}, None
    if not record.get('direct'):
        front_nodes, front_group, front_dns = front_source(root, profiles, record)
        exit_node['dialer-proxy'] = FRONT
        nodes.extend(front_nodes)
        original = loads(chain_profile.profile_file(root, record.get('source_file', record['front_file'])).read_bytes())
    # Only preserve original LAN entries that the common builder will audit.
    lan, providers = [], {}
    for rule in original.get('rules', []):
        parts = rule.split(',')
        if len(parts) < 3 or parts[2] != 'DIRECT':
            continue
        if parts[0] == 'RULE-SET' and re.search('private|localarea|lancidr|lan$', parts[1], re.I):
            providers[parts[1]] = copy.deepcopy(original.get('rule-providers', {})[parts[1]])
            lan.append(rule)
        elif parts[0] in ('IP-CIDR', 'IP-CIDR6') and len(parts) in (3, 4) and (len(parts) == 3 or parts[3] == 'no-resolve'):
            net = ipaddress.ip_network(parts[1], strict=False)
            if routing_checks.local_network(net) and (net.version == 6 or not net.overlaps(ipaddress.ip_network('198.18.0.0/15'))):
                lan.append(rule)
        elif parts[0] in ('DOMAIN', 'DOMAIN-SUFFIX') and (parts[1] in ('localhost', 'lan', 'local', 'internal') or parts[1].endswith(('.lan', '.local', '.internal'))):
            lan.append(rule)
    domestic = ['https://223.5.5.5/dns-query#DIRECT&disable-qtype-64=true&disable-qtype-65=true']
    if 'static_exit' in record:
        lan.append('NETWORK,udp,REJECT')
    group = 'Chain-Exit'
    groups = [{'name': group, 'type': 'select', 'proxies': [record['node']]}]
    if front_group:
        groups.append(front_group)
    return {'mode': 'rule', 'ipv6': False, 'proxies': nodes,
            'proxy-groups': groups,
            'proxy-providers': {}, 'rule-providers': providers, 'rules': lan + ['MATCH,' + group],
            'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'fake-ip',
                    'default-nameserver': ['223.5.5.5'], 'proxy-server-nameserver': domestic,
                    'proxy-server-nameserver-policy': front_dns,
                    'nameserver': [f'https://1.1.1.1/dns-query#{group}&disable-qtype-64=true&disable-qtype-65=true']},
            'x-portable-chain': selection(record)}


def audit(config, original):
    if not original.get('x-portable-chain'):
        return
    marker = original['x-portable-chain']
    name = marker['name']
    nodes = config.get('proxies', [])
    front_policy = next((g for g in original['proxy-groups'] if g['name'] == FRONT), None)
    front_members = [] if marker.get('direct') else front_policy['proxies'] if front_policy else [FRONT]
    names = {name, *front_members}
    require(len(nodes) == len(names) and {n['name'] for n in nodes} == names, 'Managed proxy outbounds changed')
    require([g for g in config.get('proxy-groups', []) if g.get('name') == FRONT] ==
            ([front_policy] if front_policy else []), 'Managed automatic front policy changed')
    exit_node = next(n for n in nodes if n['name'] == name)
    if marker.get('direct'):
        require('dialer-proxy' not in exit_node, 'Single static proxy must dial its endpoint directly')
    else:
        require(exit_node.get('dialer-proxy') == FRONT and
                all(not n.get('dialer-proxy') for n in nodes if n['name'] != name), 'Managed chain direction changed')
    require(config.get('dns', {}).get('proxy-server-nameserver-policy', {}) ==
            original['dns'].get('proxy-server-nameserver-policy', {}), 'Managed chain node DNS changed')
    if 'static_exit' in marker:
        endpoint = marker['static_exit']
        expected = static_node(dict(endpoint, username='', password=''))
        require({k: v for k, v in exit_node.items() if k not in ('name', 'dialer-proxy', 'username', 'password')} == expected,
                'Static proxy parameters changed')
        rules = config['rules']
        group = 'Claude-Overseas-Pinned'
        require(rules[-2:] == ['NETWORK,udp,REJECT', 'MATCH,' + group], 'Static proxy UDP fallback changed')
        for index, rule in enumerate(rules[:-1]):
            parts = rule.split(',')
            if len(parts) >= 3 and parts[2] == group:
                condition = ','.join(parts[:2] + parts[3:])
                guard = f'AND,((NETWORK,udp),({condition})),REJECT'
                require(index and rules[index - 1] == guard, 'Static proxy UDP rule missing or reordered')


def input_files(record):
    return ['profiles/' + record[key] for key in ('source_file', 'front_file') if key in record]


def refreshed(record, text, root, profiles):
    value = dict(record, sha256=chain_profile.digest(text.encode('utf-8')),
                 source_fingerprint=source_fingerprint(root, profiles, record))
    value.pop('legacy', None)
    if record.get('front_group'):
        value['front_policy'] = next(g for g in loads(text)['proxy-groups'] if g['name'] == FRONT)
    return value


def assert_live(plan, response):
    if not plan.get('chain'):
        return
    proxies = response.get('proxies', {})
    group = proxies.get('Claude-Overseas-Pinned', {})
    node = plan['node']
    require(group.get('all') == [node] and group.get('now') == node, 'Live chain exit group changed')
    endpoint = proxies.get(node)
    require(isinstance(endpoint, dict), 'Live proxy node missing')
    if plan['chain'].get('direct'):
        require(not endpoint.get('dialer-proxy') and FRONT not in proxies, 'Live single static proxy has a front hop')
    else:
        front = proxies.get(FRONT)
        require(endpoint.get('dialer-proxy') == FRONT and isinstance(front, dict) and
                not front.get('dialer-proxy'), 'Live chain direction changed')
        if plan['chain'].get('front_group'):
            policy = plan['chain']['front_policy']
            members = front.get('all')
            require(front.get('type') == AUTO_TYPES[policy['type']] and
                    front.get('emptyFallback') == 'REJECT' and front.get('testUrl') == policy['url'] and
                    not front.get('fixed') and isinstance(members, list) and members and front.get('now') in members and
                    all(m in policy['proxies'] and m in proxies and not proxies[m].get('dialer-proxy') for m in members),
                    'Live automatic front group changed or has no eligible proxy')
    if 'static_exit' in plan['chain']:
        require(endpoint.get('udp') is False, 'Live static proxy must be TCP only')
