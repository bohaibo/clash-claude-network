"""Export the pinned routing profile from local Clash files."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from config_health import GROUP
from safe_yaml import dumps, loads

FILES = {'android': 'android-clash-meta.yaml', 'ios': 'ios-shadowrocket.conf'}
POLICIES = {'DIRECT', 'REJECT', GROUP}
RULE_TYPES = {'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'IP-CIDR', 'IP-CIDR6', 'GEOIP', 'GEOSITE'}
UDP_GUARD = re.compile(r'AND,\(\(NETWORK,udp\),\(([^()]+)\)\),REJECT')
GIST_URL = re.compile(r'https://gist\.github\.com/([A-Za-z0-9-]{1,39})/([a-f0-9]{32}|[a-f0-9]{20}|[0-9]{1,20})')
IOS_ALIAS = re.compile(r'^' + re.escape(GROUP) + r' = (?:select|relay),CCN-([a-f0-9]{12})-1(?:,CCN-\1-2)?$', re.MULTILINE)


class ExportError(ValueError):
    pass


def mobile_scope(plan):
    fields = [plan.get('root'), plan.get('subscription_uid'), plan.get('node')]
    if not all(isinstance(value, str) and value for value in fields):
        return None
    return hashlib.sha256(json.dumps(fields).encode('utf-8')).hexdigest()


def gist_bindings(local):
    try:
        bindings = json.loads((Path(local) / 'mobile-gists.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(bindings, dict):
        raise ExportError('本机 Secret Gist 绑定记录无效。')
    return bindings


def gist_binding(local, plan):
    binding = gist_bindings(local).get(mobile_scope(plan), {})
    if (not isinstance(binding, dict) or (binding and
            (not isinstance(binding.get('url'), str) or not GIST_URL.fullmatch(binding['url'])
             or not isinstance(binding.get('platforms'), list) or not binding['platforms']
             or any(not isinstance(platform, str) or platform not in FILES for platform in binding['platforms'])))):
        raise ExportError('本机 Secret Gist 绑定记录无效。')
    return binding


def save_gist_binding(local, plan, binding):
    from client import write
    bindings = gist_bindings(local)
    bindings[mobile_scope(plan)] = binding
    write(Path(local) / 'mobile-gists.json', bindings)


def remove_gist_binding(local, plan):
    from client import write
    bindings = gist_bindings(local)
    bindings.pop(mobile_scope(plan), None)
    write(Path(local) / 'mobile-gists.json', bindings)


def mobile_revisions(local, plan, platforms=FILES):
    from config_health import configuration_revision
    from yaml import YAMLError

    revision = configuration_revision(local)
    if not revision or not mobile_scope(plan):
        return {}
    root = Path(plan['root'])
    try:
        index = loads((root / 'profiles.yaml').read_bytes())
        if index.get('current') != plan['subscription_uid']:
            return {}
        if plan.get('script_uid'):
            active = next((item for item in index.get('items', []) if item.get('uid') == index['current']), {})
            if (active.get('option') or {}).get('script') != plan['script_uid']:
                return {}
    except (OSError, ValueError, TypeError, AttributeError, YAMLError):
        return {}
    revisions = {platform: revision for platform in platforms}
    if 'ios' in revisions:
        digest = hashlib.sha256(revision.encode('ascii'))
        for name in ('geosite.dat', 'geoip.dat'):
            try:
                with (root / name).open('rb') as stream:
                    source = hashlib.file_digest(stream, 'sha256').digest()
            except OSError:
                source = b'unavailable'
            digest.update(name.encode('ascii') + source)
        revisions['ios'] = digest.hexdigest()
    return revisions


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def binding_status(local, plan, binding):
    if not binding:
        return {}
    revisions = mobile_revisions(local, plan, binding['platforms'])
    statuses = {}
    for platform in binding['platforms']:
        synced = binding.get('synced_revisions', {}).get(platform)
        attempt = binding.get('failed_attempts', {}).get(platform, {})
        revision = revisions.get(platform)
        if not revision:
            status = 'unknown'
        elif attempt.get('revision') == revision:
            status = 'failed'
        elif synced == revision:
            status = 'current'
        else:
            status = 'stale' if synced else 'unknown'
        statuses[platform] = {'status': status, 'reason': attempt.get('reason', '') if status == 'failed' else '',
                              'last_success': binding.get('last_success', {}).get(platform)}
    return {'url': binding['url'], 'platforms': binding['platforms'], 'ios_alias': binding.get('ios_alias'),
            'auto_sync': binding.get('auto_sync') is True, 'statuses': statuses}


def record_sync_failure(local, plan, binding, revisions, platforms, reason):
    if not binding:
        return
    failures = binding.setdefault('failed_attempts', {})
    for platform in platforms:
        failures[platform] = {'revision': revisions.get(platform), 'reason': reason}
    save_gist_binding(local, plan, binding)


def gist_auto_sync(local):
    try:
        enabled = json.loads((Path(local) / 'mobile-gist-sync.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True
    if type(enabled) is not bool:
        raise ExportError('手机订阅自动同步设置无效。')
    return enabled


def save_gist_auto_sync(local, enabled):
    from client import write
    write(Path(local) / 'mobile-gist-sync.json', enabled)


def _gist_api():
    gh = shutil.which('gh.exe')
    if not gh:
        raise ExportError('找不到 GitHub CLI。请先安装 gh 并登录 github.com，或使用手动教程。')
    env = dict(os.environ, GH_PROMPT_DISABLED='1')
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_DEBUG', 'DEBUG', 'GH_FORCE_TTY'):
        env.pop(key, None)
    def request(method, route, body=None, *, paginate=False):
        args = [gh, 'api', '--hostname', 'github.com', '--method', method, route]
        if paginate:
            args += ['--paginate', '--slurp']
        payload = None
        if body is not None:
            args += ['--input', '-']
            payload = json.dumps(body).encode('utf-8')
        try:
            response = subprocess.run(args, input=payload, capture_output=True, timeout=25, env=env,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise ExportError('GitHub 请求超时，请先到 GitHub Gist 核对同步结果。') from None
        except OSError:
            raise ExportError('无法启动 GitHub CLI。') from None
        if response.returncode:
            raise ExportError('GitHub CLI 未确认同步。请核对 Gist 地址、登录账号和 gist 权限。')
        try:
            return json.loads(response.stdout)
        except ValueError:
            raise ExportError('GitHub 返回的数据无法确认，请先到 GitHub Gist 核对。') from None

    return request


def _gist_url(gist):
    try:
        url = f"https://gist.github.com/{gist['owner']['login']}/{gist['id']}"
        if gist['public'] is False and GIST_URL.fullmatch(url) and isinstance(gist['files'], dict):
            return url
    except (TypeError, KeyError):
        pass
    raise ExportError('目标必须是当前 GitHub CLI 账号下的 Secret Gist。')


def find_mobile_gists():
    pages = _gist_api()('GET', 'gists?per_page=100', paginate=True)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ExportError('GitHub 返回的 Gist 列表无法识别。')
    results = []
    for page in pages:
        for gist in page:
            if not isinstance(gist, dict) or gist.get('public') is not False or not isinstance(gist.get('files'), dict):
                continue
            platforms = [platform for platform, name in FILES.items() if name in gist['files']]
            if platforms:
                results.append({'url': _gist_url(gist), 'platforms': platforms,
                                'updated_at': gist.get('updated_at', '')})
    return results


def _ios_alias(content):
    match = IOS_ALIAS.search(content or '')
    return match[1] if match else None


def sync_secret_gist(files, target):
    request = _gist_api()
    try:
        contents = {FILES[platform]: path.read_text(encoding='utf-8') for platform, path in files.items()}
    except (OSError, UnicodeError):
        raise ExportError('无法读取本次导出的配置，请重新生成。') from None

    ios_name = FILES['ios']
    alias = _ios_alias(contents.get(ios_name))
    if target:
        match = GIST_URL.fullmatch(target)
        if not match:
            raise ExportError('请输入有效的 GitHub Gist 地址。')
        owner, identifier = match.groups()
        route = 'gists/' + identifier
        account = request('GET', 'user')
        if not isinstance(account, dict) or str(account.get('login', '')).casefold() != owner.casefold():
            raise ExportError('目标必须是当前 GitHub CLI 账号下的 Secret Gist。')
        current = request('GET', route)
        if _gist_url(current).casefold() != target.casefold():
            raise ExportError('目标必须是当前 GitHub CLI 账号下的 Secret Gist。')
        if not current['files'].keys() & set(FILES.values()):
            raise ExportError('该 Gist 没有原有的手机配置文件，请核对地址和文件名。')
        previous = current['files'].get(ios_name, {})
        previous_alias = _ios_alias(previous.get('content'))
        if alias and previous.get('truncated') and not previous_alias:
            raise ExportError('无法确认 Gist 中现有 iOS 配置的节点名称，未覆盖原文件。')
        if alias and previous_alias and alias != previous_alias:
            # Keep aliases already used by manually imported Shadowrocket nodes.
            contents[ios_name] = re.sub(rf'(?m)(^CCN-|,CCN-|^# Before using, import the exact corresponding servers as CCN-|, CCN-){alias}(?=-[12]\b)',
                                        lambda match: match[1] + previous_alias, contents[ios_name])
            alias = previous_alias
        if ios_name not in contents:
            alias = previous_alias
        body = {'files': {name: {'content': content} for name, content in contents.items()}}
        gist = request('PATCH', route, body)
    else:
        body = {'description': 'Mobile routing profiles', 'public': False,
                'files': {name: {'content': content} for name, content in contents.items()}}
        gist = request('POST', 'gists', body)
    url = _gist_url(gist)
    if (target and url.casefold() != target.casefold()) or not contents.keys() <= gist['files'].keys():
        raise ExportError('GitHub 返回的 Gist 文件无法确认，请先在 GitHub 核对。')
    return {'url': url, 'ios_alias': alias, 'platforms': [platform for platform in FILES if FILES[platform] in gist['files']]}


def publish_gist(local, plan, files, target, revisions):
    previous = gist_binding(local, plan)
    binding = sync_secret_gist(files, target)
    if previous.get('url', '').casefold() != binding['url'].casefold():
        previous = {}
    binding['auto_sync'] = previous.get('auto_sync') is True
    binding['synced_revisions'] = {**previous.get('synced_revisions', {}),
                                  **{platform: revisions[platform] for platform in files}}
    binding['last_success'] = {**previous.get('last_success', {}), **dict.fromkeys(files, timestamp())}
    binding['failed_attempts'] = {platform: attempt for platform, attempt in previous.get('failed_attempts', {}).items()
                                  if platform not in files}
    save_gist_binding(local, plan, binding)
    return binding


def sync_gist_after_rule_update(local):
    """Run under the rule update lock, after local rule publication."""
    binding = revisions = {}
    due = []
    try:
        if not gist_auto_sync(local):
            return {'status': 'disabled'}
        plan = json.loads((Path(local) / 'plan.json').read_text(encoding='utf-8-sig'))
        binding = gist_binding(local, plan)
        if not binding:
            return {'status': 'unbound'}
        if binding.get('auto_sync') is not True:
            return {'status': 'disabled'}
        index = loads((Path(plan['root']) / 'profiles.yaml').read_bytes())
        if index.get('current') != plan['subscription_uid']:
            return {'status': 'inactive'}
        revisions = mobile_revisions(local, plan, binding['platforms'])
        if not revisions:
            raise ExportError('无法确认当前 Clash 配置版本。')
        synced = binding.get('synced_revisions', {})
        failures = binding.get('failed_attempts', {})
        due = [platform for platform, revision in revisions.items()
               if synced.get(platform) != revision or failures.get(platform, {}).get('revision') == revision]
        if not due:
            return {'status': 'unchanged'}
        with tempfile.TemporaryDirectory(prefix='.sync-', dir=export_directory(local)) as temp:
            destination = Path(temp)
            result = export(plan, local, destination=destination, platforms=due)
            if mobile_revisions(local, plan, binding['platforms']) != revisions:
                raise ExportError('生成手机配置期间 Clash 配置或规则库发生变化。')
            files = {platform: destination / result[platform].split('/')[3] / FILES[platform]
                     for platform in due if platform in result}
            if files:
                binding = publish_gist(local, plan, files, binding['url'], revisions)
            missing = set(due) - files.keys()
            if missing:
                reason = result.get('ios_error', '部分已绑定的手机配置无法导出。')
                record_sync_failure(local, plan, binding, revisions, missing, reason)
                return {'status': 'partial' if files else 'failed', 'reason': reason}
        return {'status': 'updated'}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ExportError) else '手机配置生成或 Gist 绑定记录处理失败。'
        if due:
            try:
                record_sync_failure(local, plan, binding, revisions, due, reason)
            except (OSError, ValueError):
                pass
        return {'status': 'failed', 'reason': reason}


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
    if kind not in fields:
        raise ExportError('Shadowrocket 导出暂不支持该代理协议。')
    if node.keys() - common - fields[kind] or node.get('network', 'tcp') not in ('tcp', ''):
        raise ExportError('Shadowrocket 导出暂不支持该节点的部分传输或协议参数。')
    if type(node.get('port')) is not int or not 1 <= node['port'] <= 65535:
        raise ExportError('节点端口格式无效。')
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
    proxy_lines = []
    for node in profile['proxies']:
        name = node['name']
        try:
            proxy_lines.append(aliases[name] + ' = ' + shadowrocket_proxy(node))
        except ExportError as exc:
            manual.append({'name': name, 'alias': aliases[name], 'reason': str(exc)})
        except (KeyError, TypeError, ValueError, OverflowError):
            manual.append({'name': name, 'alias': aliases[name], 'reason': '该节点的配置字段缺失或格式无效。'})
    if manual:
        # Keep the entire group in the client's manually managed node list.
        proxy_lines = []
        reasons = {item['name']: item['reason'] for item in manual}
        manual = [{'name': name, 'alias': alias, **({'reason': reasons[name]} if name in reasons else {})}
                  for name, alias in aliases.items()]
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


def export_directory(local):
    parent = Path(local).resolve() / 'mobile-exports'
    if parent.resolve() != parent:
        raise ExportError('本地导出目录已被重定向。')
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def prune_exports(local, *, keep=3, current=None, discard=None):
    parent = export_directory(local)
    candidates = []
    for folder in parent.iterdir():
        if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or folder.resolve() != folder or not folder.is_dir():
            continue
        entries = list(folder.iterdir())
        if all(path.name in FILES.values() and path.resolve() == path and path.is_file() for path in entries):
            candidates.append((folder, entries))
    candidates.sort(key=lambda entry: (entry[0].name == current, entry[0].stat().st_mtime_ns), reverse=True)
    obsolete = candidates[keep:] if discard is None else [entry for entry in candidates if entry[0].name == discard]
    for folder, entries in obsolete:
        for path in entries:
            path.unlink()
        folder.rmdir()
    return len(obsolete)


def export(plan, local, *, destination=None, platforms=FILES):
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
    files = {}
    if 'android' in platforms:
        files['android'] = dumps(profile, allow_unicode=True, sort_keys=False)
    result = {}
    if 'ios' in platforms:
        try:
            alias = gist_binding(local, plan).get('ios_alias')
        except (OSError, ValueError):
            alias = None
        alias_seed = alias if isinstance(alias, str) and re.fullmatch(r'[a-f0-9]{12}', alias) else mobile_scope(plan)
        try:
            files['ios'], result['omitted_geo_regex'], result['ios_manual'] = shadowrocket(profile, root, alias_seed)
        except ExportError as exc:
            result['ios_error'] = str(exc)
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError):
            result['ios_error'] = 'Shadowrocket 转换未完成，请检查本机 GeoSite、GeoIP 规则库和当前节点配置。'
    if [path.read_bytes() for path in inputs] != [profile_bytes, runtime_bytes]:
        raise ExportError('导出时 Clash 配置发生变化，请重试。')

    if not files:
        return result
    parent = destination if destination is not None else export_directory(local)
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=parent) as temp:
        folder = Path(temp)
        for platform, content in files.items():
            (folder / FILES[platform]).write_text(content, encoding='utf-8', newline='\n')
            result[platform] = '/api/mobile-exports/' + identifier + '/' + platform
        folder.rename(parent / identifier)
    return result
