"""Managed two-hop sources for the normal Windows apply/restore lifecycle."""
import copy, hashlib, ipaddress, json, re, uuid
from pathlib import Path
import chain_profile
from checks import require
from safe_yaml import loads, dumps

REGISTRY = 'managed-chains.json'
FRONT = chain_profile.FRONT


def traffic(profiles, record):
    """Use the remaining-quota bottleneck's account counters, never invented chain usage."""
    sources = []
    for key in ('front_uid', 'source_uid'):
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
            'source_uid': limiting['uid'], 'shared_subscription': record['front_uid'] == record['source_uid']}


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
    return {'name': record['node'], 'exit_subscription': record['source_uid'],
            'exit_node': record['exit_node'], 'front_subscription': record['front_uid'],
            'front_node': record['front_node']}


def resolve(root, profiles, request, local):
    require(isinstance(request, dict), 'Invalid chain selection')
    name = request.get('name')
    require(isinstance(name, str) and name == name.strip() and 0 < len(name) <= 100 and
            not any(ord(c) < 32 for c in name) and name not in
            ('DIRECT', 'REJECT', 'GLOBAL', 'Claude-Overseas-Pinned', FRONT, 'Chain-Exit'),
            'Invalid chain name')
    record = {'root': str(Path(root).resolve()), 'node': name, 'name': name}
    for side, uidkey, filekey, nodekey in [('exit', 'source_uid', 'source_file', 'exit_node'),
                                           ('front', 'front_uid', 'front_file', 'front_node')]:
        uid, node = request.get(side + '_subscription'), request.get(side + '_node')
        matches = [i for i in profiles['items'] if i.get('type') == 'remote' and i.get('uid') == uid]
        require(len(matches) == 1 and isinstance(node, str) and node, 'Exact chain source subscription missing')
        record.update({uidkey: uid, filekey: matches[0]['file'], nodekey: node})
        chain_profile.source_node(root, profiles, uid, matches[0]['file'], node)
    require((record['source_uid'], record['exit_node']) != (record['front_uid'], record['front_node']),
            'A chain cannot use the same node twice')
    known = records(root, local)
    matches = [r for uid, r in known.items() if selection(r) == request and
               any(i.get('uid') == uid for i in profiles['items'])]
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
    require(record.get('source_fingerprint') == chain_profile.source_fingerprint(root, profiles, record),
            'Managed chain source parameters changed; update rules or reconfigure')


def source(root, profiles, record):
    """Clone only the exact two nodes, preserving the original subscriptions byte for byte."""
    exit_node = copy.deepcopy(chain_profile.source_node(root, profiles, record['source_uid'], record['source_file'], record['exit_node']))
    front_node = copy.deepcopy(chain_profile.source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node']))
    exit_node.update(name=record['node'], **{'dialer-proxy': FRONT})
    front_node['name'] = FRONT
    original = loads(chain_profile.profile_file(root, record['source_file']).read_bytes())
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
    return {'mode': 'rule', 'ipv6': False, 'proxies': [exit_node, front_node],
            'proxy-groups': [{'name': 'Chain-Exit', 'type': 'select', 'proxies': [record['node']]}],
            'proxy-providers': {}, 'rule-providers': providers, 'rules': lan + ['MATCH,Chain-Exit'],
            'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'fake-ip',
                    'default-nameserver': ['223.5.5.5'], 'proxy-server-nameserver': domestic,
                    'proxy-server-nameserver-policy': chain_profile.front_dns(root, profiles, record),
                    'nameserver': ['https://1.1.1.1/dns-query#Chain-Exit&disable-qtype-64=true&disable-qtype-65=true']},
            'x-portable-chain': selection(record)}


def audit(config, original):
    if not original.get('x-portable-chain'):
        return
    name = original['x-portable-chain']['name']
    nodes = config.get('proxies', [])
    require(len(nodes) == 2 and {n['name'] for n in nodes} == {name, FRONT}, 'Managed chain outbounds changed')
    exit_node = next(n for n in nodes if n['name'] == name)
    front_node = next(n for n in nodes if n['name'] == FRONT)
    require(exit_node.get('dialer-proxy') == FRONT and not front_node.get('dialer-proxy'), 'Managed chain direction changed')
    require(config.get('dns', {}).get('proxy-server-nameserver-policy', {}) ==
            original['dns'].get('proxy-server-nameserver-policy', {}), 'Managed chain node DNS changed')


def input_files(record):
    return ['profiles/' + record['source_file'], 'profiles/' + record['front_file']]


def refreshed(record, text, root, profiles):
    value = dict(record, sha256=chain_profile.digest(text.encode('utf-8')),
                 source_fingerprint=chain_profile.source_fingerprint(root, profiles, record))
    value.pop('legacy', None)
    return value


def assert_live(plan, response):
    if not plan.get('chain'):
        return
    proxies = response.get('proxies', {})
    group = proxies.get('Claude-Overseas-Pinned', {})
    node = plan['node']
    require(group.get('all') == [node] and group.get('now') == node, 'Live chain exit group changed')
    require(proxies.get(node, {}).get('dialer-proxy') == FRONT and
            proxies.get(FRONT, {}).get('dialer-proxy', '') == '' and FRONT in proxies,
            'Live chain direction changed')
