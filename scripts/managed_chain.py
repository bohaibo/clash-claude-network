"""Managed proxy profiles for the normal Windows apply/restore lifecycle."""
import copy, ipaddress, json, re, uuid
from pathlib import Path
import chain_profile
from checks import require
from safe_yaml import loads

REGISTRY = 'managed-chains.json'
FRONT = chain_profile.FRONT


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
    if 'static_exit' in record:
        endpoint = record['static_exit']
        return dict(fields, static_exit={key: endpoint[key] for key in ('ip', 'port', 'protocol')})
    return dict(fields, exit_subscription=record['source_uid'], exit_node=record['exit_node'])


def resolve(root, profiles, request, local):
    require(isinstance(request, dict), 'Invalid chain selection')
    name = request.get('name')
    require(isinstance(name, str) and name == name.strip() and 0 < len(name) <= 100 and
            not any(ord(c) < 32 for c in name) and name not in
            ('DIRECT', 'REJECT', 'GLOBAL', 'Claude-Overseas-Pinned', FRONT, 'Chain-Exit'),
            'Invalid chain name')
    record = {'root': str(Path(root).resolve()), 'node': name, 'name': name}
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
        chain_profile.source_node(root, profiles, uid, matches[0]['file'], node)
    require('source_uid' not in record or anchor(record) != (record['front_uid'], record['front_node']),
            'A chain cannot use the same node twice')
    known = records(root, local)
    identity = selection(record)
    matches = [r for uid, r in known.items() if selection(r) == identity and
               r.get('static_exit') == record.get('static_exit') and
               any(i.get('uid') == uid for i in profiles.get('items') or [])]
    if matches:
        require(len(matches) == 1, 'Duplicate managed chain selection')
        return matches[0]
    require(not any(r.get('node') == name for r in known.values()), 'Chain name already exists; choose another name')
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
    if 'static_exit' not in record:
        return chain_profile.source_fingerprint(root, profiles, record)
    source = {'nodes': [static_node(static_exit(record['static_exit']))], 'front_dns': {}}
    if not record.get('direct'):
        source['nodes'].append(chain_profile.source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node']))
        source['front_dns'] = chain_profile.front_dns(root, profiles, record)
    return chain_profile.digest(json.dumps(source, sort_keys=True, ensure_ascii=False).encode('utf-8'))


def source(root, profiles, record):
    """Build an independent local profile from the exact front and exit."""
    if 'static_exit' in record:
        exit_node = static_node(static_exit(record['static_exit']))
    else:
        exit_node = copy.deepcopy(chain_profile.source_node(root, profiles, record['source_uid'], record['source_file'], record['exit_node']))
    exit_node['name'] = record['node']
    nodes, original, front_dns = [exit_node], {}, {}
    if not record.get('direct'):
        front_node = copy.deepcopy(chain_profile.source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node']))
        front_node['name'] = FRONT
        exit_node['dialer-proxy'] = FRONT
        nodes.append(front_node)
        original = loads(chain_profile.profile_file(root, record.get('source_file', record['front_file'])).read_bytes())
        front_dns = chain_profile.front_dns(root, profiles, record)
    # Only preserve original LAN entries that the common builder will audit.
    lan, providers = [], {}
    for rule in original.get('rules', []):
        parts = rule.split(',')
        if len(parts) < 3 or parts[2] != 'DIRECT':
            continue
        if parts[0] == 'RULE-SET' and re.search('private|localarea|lancidr|lan$', parts[1], re.I):
            providers[parts[1]] = copy.deepcopy(original.get('rule-providers', {})[parts[1]])
            lan.append(rule)
        elif parts[0] in ('IP-CIDR', 'IP-CIDR6'):
            net = ipaddress.ip_network(parts[1], strict=False)
            if (net.is_private or net.is_loopback or net.is_link_local) and (net.version == 6 or not net.overlaps(ipaddress.ip_network('198.18.0.0/15'))):
                lan.append(rule)
        elif parts[0] in ('DOMAIN', 'DOMAIN-SUFFIX') and (parts[1] in ('localhost', 'lan', 'local', 'internal') or parts[1].endswith(('.lan', '.local', '.internal'))):
            lan.append(rule)
    domestic = ['https://223.5.5.5/dns-query#DIRECT&disable-qtype-64=true&disable-qtype-65=true']
    if 'static_exit' in record:
        lan.append('NETWORK,udp,REJECT')
    group = chain_profile.GROUP if record.get('direct') else 'Chain-Exit'
    return {'mode': 'rule', 'ipv6': False, 'proxies': nodes,
            'proxy-groups': [{'name': group, 'type': 'select', 'proxies': [record['node']]}],
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
    names = {name} if marker.get('direct') else {name, FRONT}
    require(len(nodes) == len(names) and {n['name'] for n in nodes} == names, 'Managed proxy outbounds changed')
    exit_node = next(n for n in nodes if n['name'] == name)
    if marker.get('direct'):
        require('dialer-proxy' not in exit_node, 'Single static proxy must dial its endpoint directly')
    else:
        front_node = next(n for n in nodes if n['name'] == FRONT)
        require(exit_node.get('dialer-proxy') == FRONT and not front_node.get('dialer-proxy'), 'Managed chain direction changed')
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
        require(endpoint.get('dialer-proxy') == FRONT and
                proxies.get(FRONT, {}).get('dialer-proxy', '') == '' and FRONT in proxies,
                'Live chain direction changed')
    if 'static_exit' in plan['chain']:
        require(endpoint.get('udp') is False, 'Live static proxy must be TCP only')
