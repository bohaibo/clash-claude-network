"""Local two-hop profiles and rule-update companions. No network I/O."""
import copy, hashlib, ipaddress, json, re, subprocess
from pathlib import Path
from checks import require
from safe_yaml import loads, dumps

GROUP = 'Claude-Overseas-Pinned'
FRONT = 'Chain-Front-Pinned'
REGISTRY = 'chain-profile.json'


def digest(value):
    return hashlib.sha256(value).hexdigest()


def profile_file(root, name):
    require(isinstance(name, str) and Path(name).name == name and name not in ('.', '..'),
            'Invalid chain profile filename')
    path = (Path(root) / 'profiles' / name).resolve()
    require(path.is_relative_to((Path(root) / 'profiles').resolve()), 'Invalid chain profile path')
    return path


def source_node(root, profiles, uid, filename, node):
    entry = next((i for i in profiles['items'] if i.get('uid') == uid), None)
    require(entry is not None and entry.get('type') == 'remote' and entry.get('file') == filename,
            'Chain source subscription changed; review required')
    value = loads(profile_file(root, filename).read_text(encoding='utf-8-sig'))
    matches = [p for p in value.get('proxies', []) if p.get('name') == node]
    require(len(matches) == 1, 'Exact chain source node missing or duplicated')
    require(not matches[0].get('dialer-proxy'), 'Nested source chains require explicit review')
    return matches[0]


def front_dns(root, profiles, record):
    node = source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node'])
    server = node.get('server', '')
    try:
        ipaddress.ip_address(server)
        return {}
    except ValueError:
        pass
    source = loads(profile_file(root, record['front_file']).read_bytes())
    resolvers = (source.get('dns') or {}).get('proxy-server-nameserver') or []
    require(isinstance(resolvers, list), 'Unsupported chain source node DNS; review required')
    values = []
    for resolver in resolvers:
        require(isinstance(resolver, str) and resolver.startswith(('https://', 'tls://')),
                'Unsupported chain source node DNS; review required')
        address, _, fragment = resolver.partition('#')
        # Resolve only the entry node through its source subscription's resolver.
        # Remove source-only proxy references and retain DNS transport options.
        flags = [p for p in fragment.split('&') if '=' in p and
                 p.split('=', 1)[0] not in ('disable-qtype-64', 'disable-qtype-65')]
        values.append(address + '#DIRECT&' + '&'.join(flags + ['disable-qtype-64=true', 'disable-qtype-65=true']))
    return {server: values} if values else {}


def render(base, record, root, profiles):
    """Embed exact source nodes so service-mode cores need no user-directory file access."""
    exit_node = source_node(root, profiles, record['source_uid'], record['source_file'], record['exit_node'])
    front_node = source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node'])
    require(base.get('mode') == 'rule' and base['rules'][-1] == 'MATCH,' + GROUP,
            'Chain requires reviewed pinned routing')
    result = copy.deepcopy(base)
    # Native client ports/controller credentials are merged by Clash when activated.
    for field in ('external-controller', 'external-controller-pipe', 'secret', 'external-controller-cors',
                  'external-ui', 'external-ui-url', 'external-ui-name', 'geox-url'):
        result.pop(field, None)
    result['allow-lan'] = False
    result['bind-address'] = '127.0.0.1'
    result['geo-auto-update'] = False
    node_dns = front_dns(root, profiles, record)
    if node_dns:
        require(bool(result.get('dns', {}).get('proxy-server-nameserver')),
                'Chain node DNS policy requires a proxy server resolver')
        result['dns'].setdefault('proxy-server-nameserver-policy', {}).update(node_dns)
    result['proxies'] = []
    result['proxy-providers'] = {
        'chain-front-source': {
            'type': 'inline', 'payload': [copy.deepcopy(front_node)],
            'filter': '^' + re.escape(record['front_node']) + '$',
            'health-check': {'enable': False}},
        'chain-exit-source': {
            'type': 'inline', 'payload': [copy.deepcopy(exit_node)],
            'filter': '^' + re.escape(record['exit_node']) + '$',
            'health-check': {'enable': False},
            'override': {'dialer-proxy': FRONT,
                         'proxy-name': [{'pattern': '^' + re.escape(record['exit_node']) + '$',
                                         'target': record['node']}]}}
    }
    # Missing/empty providers must never fall through Mihomo's default COMPATIBLE/DIRECT.
    result['proxy-groups'] = [
        {'name': GROUP, 'type': 'select', 'use': ['chain-exit-source'],
         'default-selected': record['node'], 'empty-fallback': 'REJECT'},
        {'name': FRONT, 'type': 'select', 'use': ['chain-front-source'], 'hidden': True,
         'default-selected': record['front_node'], 'empty-fallback': 'REJECT'}]
    used = {r.split(',')[1] for r in result['rules'] if r.startswith('RULE-SET,')}
    result['rule-providers'] = {k: v for k, v in result.get('rule-providers', {}).items() if k in used}
    require(used == set(result['rule-providers']) and
            all(p.get('type') == 'inline' for p in result['rule-providers'].values()),
            'Chain routing requires local inline rule sources')
    return result


def source_fingerprint(root, profiles, record):
    nodes = [source_node(root, profiles, record['source_uid'], record['source_file'], record['exit_node']),
             source_node(root, profiles, record['front_uid'], record['front_file'], record['front_node'])]
    return digest(json.dumps({'nodes': nodes, 'front_dns': front_dns(root, profiles, record)},
                             sort_keys=True, ensure_ascii=False).encode('utf-8'))


def migrated(local, uid):
    path = Path(local) / 'managed-chains.json'
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    return uid in rows and not rows[uid].get('legacy')


def refresh_required(local, root, profiles, plan):
    registry = Path(local) / REGISTRY
    if not registry.exists():
        return False
    record = json.loads(registry.read_text(encoding='utf-8'))
    if migrated(local, record['uid']):
        return False
    if not any(i.get('uid') == record['uid'] for i in profiles['items']):
        return False
    require(Path(record['root']).resolve() == Path(root).resolve(), 'Chain belongs to another client')
    require(record['source_uid'] == plan['subscription_uid'] and record['exit_node'] == plan['node'],
            'Chain rule source changed; review required')
    return record.get('source_fingerprint') != source_fingerprint(root, profiles, record)


def prepare_update(local, root, profiles, plan, base):
    """Return a guarded companion write, leaving originals and the baseline untouched."""
    registry = Path(local) / REGISTRY
    if not registry.exists():
        return None
    record = json.loads(registry.read_text(encoding='utf-8'))
    if migrated(local, record['uid']):
        return None
    require(Path(record['root']).resolve() == Path(root).resolve(), 'Chain belongs to another client')
    require(record['source_uid'] == plan['subscription_uid'] and record['exit_node'] == plan['node'],
            'Chain rule source changed; review required')
    entry = next((i for i in profiles['items'] if i.get('uid') == record['uid']), None)
    if entry is None:
        return None  # Deleting a profile never silently recreates it.
    require(entry.get('type') == 'local' and entry.get('file') == record['file'],
            'Chain profile association changed; review required')
    require(entry.get('option', {}) == record.get('option', {}),
            'Chain profile has extra enhancements; review required')
    for filename, expected in record.get('enhancements', {}).items():
        require(digest(profile_file(root, filename).read_bytes()) == expected,
                'Chain profile enhancement changed; review required')
    target = profile_file(root, record['file'])
    before = target.read_bytes()
    require(digest(before) == record['sha256'], 'Chain profile changed; review required')
    config = render(base, record, root, profiles)
    text = dumps(config, allow_unicode=True, sort_keys=False)
    runtime = copy.deepcopy(config)
    effective = loads((Path(root) / 'clash-verge.yaml').read_bytes())
    for key in ('external-controller', 'external-controller-pipe', 'external-controller-cors',
                'secret', 'mixed-port', 'port', 'socks-port', 'authentication'):
        if key in effective:
            runtime[key] = copy.deepcopy(effective[key])
    updated = dict(record, sha256=digest(text.encode('utf-8')),
                   source_fingerprint=source_fingerprint(root, profiles, record))
    return {'record': updated, 'text': text, 'config': config, 'before': before,
            'runtime_text': dumps(runtime, allow_unicode=True, sort_keys=False),
            'registry_before': registry.read_bytes(), 'active': profiles['current'] == record['uid']}


def validate_native(config, core, root, folder):
    """Mihomo syntax validation only; no listeners, connectivity tests or health checks."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / 'candidate.yaml'
    candidate.write_text(dumps(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    result = subprocess.run([str(core), '-t', '-d', str(root), '-f', str(candidate)],
                            capture_output=True, timeout=45)
    (folder / 'validation.stdout.log').write_bytes(result.stdout)
    (folder / 'validation.stderr.log').write_bytes(result.stderr)
    require(result.returncode == 0, 'Chain native validation failed; inspect local diagnostics')
