"""Parse, validate, and stage Secret Gist Android profiles for Windows import."""
from __future__ import annotations

import copy
import datetime
import ipaddress
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

import config_health
import managed_chain
import node_region
import region_profile
from atomic_files import replace_file
from safe_yaml import loads

SOURCES_FILE = 'gist-import-sources.json'
STAGING_DIR = 'gist-imports'
STAGING_TTL = 1800
MAX_BYTES = 4_000_000
REGION_COMMENT_RE = re.compile(r'^#\s*ccn-region:\s*([A-Za-z]{2})\s*$')
OWNER_RE = r'[A-Za-z0-9-]{1,39}'
GIST_ID_RE = r'(?:[a-f0-9]{32}|[a-f0-9]{20}|[0-9]{1,20})'
PAGE_PATH_RE = re.compile(rf'^/({OWNER_RE})/({GIST_ID_RE})/?$')
RAW_PATH_RE = re.compile(rf'^/({OWNER_RE})/({GIST_ID_RE})/raw(?:/[a-f0-9]{{40}})?(?:/([^/]+))?/?$')
UID_RE = re.compile(r'^ManagedChain[a-f0-9]{12}$')
TOKEN_RE = re.compile(r'^[a-f0-9]{32}$')
UDP_GUARD_RE = re.compile(r'^AND,\(\(NETWORK,udp\),\((.+)\)\),REJECT$')
AUTO_GROUP_OPTIONS = (
    'type', 'url', 'interval', 'lazy', 'timeout', 'max-failed-times',
    'expected-status', 'filter', 'exclude-filter', 'exclude-type', 'tolerance', 'disable-udp',
)
TOPOLOGY_LABELS = {
    'single': '单一节点配置',
    'chain': '链式代理配置',
    'chain_auto': '自动前置链式代理配置',
    'static_chain': '静态 IP 出口链式代理',
    'static_chain_auto': '自动前置 + 静态 IP 出口链式代理',
    'static_direct': '单一静态 IP 配置',
}


class GistImportError(ValueError):
    pass


class _SafeGistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        parsed = urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname != 'gist.githubusercontent.com':
            raise GistImportError('Secret Gist 返回了未授权的重定向地址。')
        return super().redirect_request(req, fp, code, msg, headers, target)


def _clean_text(value, limit=200):
    return (isinstance(value, str) and value == value.strip() and 0 < len(value) <= limit
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _is_domain(host):
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def _sanitize_resolvers(resolvers):
    if isinstance(resolvers, str):
        resolvers = [resolvers]
    if not isinstance(resolvers, list) or not resolvers:
        raise GistImportError('节点 DNS 解析器格式无效。')
    values = []
    for resolver in resolvers:
        if not isinstance(resolver, str) or not resolver.startswith(('https://', 'tls://')):
            raise GistImportError('节点 DNS 仅支持 https:// 或 tls:// 加密解析器。')
        address, _, fragment = resolver.partition('#')
        flags = [part for part in fragment.split('&') if '=' in part
                 and part.split('=', 1)[0] not in ('disable-qtype-64', 'disable-qtype-65')]
        values.append(address + '#DIRECT&' + '&'.join(flags + ['disable-qtype-64=true', 'disable-qtype-65=true']))
    return values


def _validate_proxy_node(node):
    if not isinstance(node, dict) or node.get('type') not in managed_chain.AUTO_PROXY_TYPES:
        raise GistImportError('导入的节点缺少受支持的代理协议类型。')
    if not _clean_text(node.get('server'), 255):
        raise GistImportError('导入的节点服务器地址无效。')
    port = node.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise GistImportError('导入的节点端口无效。')


def parse_gist_url(url):
    """Validate and normalize a Secret Gist page or raw URL without echoing it on error."""
    if not isinstance(url, str):
        raise GistImportError('请输入有效的 GitHub Secret Gist 链接。')
    text = url.strip()
    if not text or len(text) > 512 or any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in text):
        raise GistImportError('请输入有效的 GitHub Secret Gist 链接。')
    parsed = urlsplit(text)
    if parsed.scheme != 'https' or parsed.query or parsed.username or parsed.password or parsed.port is not None:
        raise GistImportError('请输入有效的 HTTPS GitHub Secret Gist 链接。')
    if parsed.hostname == 'gist.github.com':
        match = PAGE_PATH_RE.fullmatch(parsed.path)
        if not match:
            raise GistImportError('请输入有效的 GitHub Secret Gist 页面链接。')
        owner, gist_id = match.groups()
        if any(marker in (parsed.fragment or '').lower() for marker in ('ios-shadowrocket.conf', 'ios-shadowrocket-conf')):
            raise GistImportError('请使用 Android (android-clash-meta.yaml) 配置或 Gist 页面地址；iOS CONF 格式不适用于 Windows 导入。')
    elif parsed.hostname == 'gist.githubusercontent.com':
        match = RAW_PATH_RE.fullmatch(parsed.path)
        if not match:
            raise GistImportError('请输入有效的 GitHub Secret Gist Raw 链接。')
        owner, gist_id, filename = match.groups()
        if filename == 'ios-shadowrocket.conf':
            raise GistImportError('请使用 Android (android-clash-meta.yaml) 配置或 Gist 页面地址；iOS CONF 格式不适用于 Windows 导入。')
        if filename is not None and filename != 'android-clash-meta.yaml':
            raise GistImportError('请使用 Android (android-clash-meta.yaml) 配置或 Gist 页面地址。')
    else:
        raise GistImportError('只支持从 GitHub Secret Gist (gist.github.com / gist.githubusercontent.com) 导入。')
    return {
        'canonical_url': f'https://gist.github.com/{owner}/{gist_id}',
        'raw_url': f'https://gist.githubusercontent.com/{owner}/{gist_id}/raw/android-clash-meta.yaml',
        'gist_hint': f'{owner}/…{gist_id[-6:]}',
    }


def fetch_gist_bytes(raw_url):
    """Fetch android-clash-meta.yaml over direct or unauthenticated local Clash HTTP proxy."""
    parsed = urlsplit(raw_url)
    if parsed.scheme != 'https' or parsed.hostname != 'gist.githubusercontent.com':
        raise GistImportError('只允许从 gist.githubusercontent.com 下载配置。')
    routes = [{}]
    try:
        import client
        config = client.load(client.root_path() / 'clash-verge.yaml') or {}
        if not config.get('authentication'):
            for key in ('mixed-port', 'port'):
                port = config.get(key)
                if type(port) is int and 0 < port < 65536:
                    route = {'https': f'http://127.0.0.1:{port}'}
                    if route not in routes:
                        routes.append(route)
    except (OSError, ValueError, RuntimeError, KeyError, yaml.YAMLError):
        pass

    for route in routes:
        for _ in range(2):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(route), _SafeGistRedirectHandler())
            request = urllib.request.Request(raw_url, headers={'User-Agent': 'clash-claude-network'})
            try:
                with opener.open(request, timeout=15) as response:
                    raw = response.read(MAX_BYTES + 1)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise GistImportError('找不到该 Secret Gist 或其中缺少 android-clash-meta.yaml 文件，请核对链接。') from None
                continue
            except (OSError, urllib.error.URLError):
                continue
            if len(raw) > MAX_BYTES:
                raise GistImportError('Secret Gist 配置文件体积超过限制（4 MB）。')
            return raw
    raise GistImportError('无法下载 Secret Gist 配置：直连及可用的本机 Clash HTTP 代理均未成功。请检查网络连接与链接是否正确。') from None


def validate_spec(spec):
    """Re-validate a staged/submitted Gist import node topology spec."""
    if not isinstance(spec, dict):
        raise GistImportError('导入节点规格无效。')
    exit_node = copy.deepcopy(spec.get('exit_node'))
    front_nodes = copy.deepcopy(spec.get('front_nodes'))
    front_group = copy.deepcopy(spec.get('front_group'))
    node_dns = copy.deepcopy(spec.get('node_dns'))
    static_exit = copy.deepcopy(spec.get('static_exit'))
    if not isinstance(front_nodes, list) or not isinstance(node_dns, dict):
        raise GistImportError('导入节点规格字段缺失。')
    _validate_proxy_node(exit_node)

    if front_group is not None:
        if not isinstance(front_group, dict) or front_group.get('name') != managed_chain.FRONT:
            raise GistImportError('自动前置组名称无效。')
        if (front_group.get('type') not in managed_chain.AUTO_TYPES
                or front_group.get('url') not in managed_chain.HEALTH_URLS
                or type(front_group.get('interval')) is not int or front_group['interval'] <= 0
                or any(front_group.get(k) for k in ('use', 'include-all', 'include-all-proxies',
                                                    'include-all-providers', 'dialer-proxy'))
                or front_group.get('empty-fallback') not in (None, '', 'REJECT')):
            raise GistImportError('自动前置组参数不受支持。')
        members = front_group.get('proxies')
        if (not isinstance(members, list) or not members or not all(_clean_text(m) for m in members)
                or len(set(members)) != len(members) or len(members) != len(front_nodes)):
            raise GistImportError('自动前置组成员列表无效。')
        if exit_node.get('dialer-proxy') != managed_chain.FRONT:
            raise GistImportError('自动前置链式方向无效。')
        front_map = {}
        for node in front_nodes:
            _validate_proxy_node(node)
            name = node.get('name')
            if not _clean_text(name) or name in managed_chain.RESERVED_NAMES or node.get('dialer-proxy') or name in front_map:
                raise GistImportError('自动前置节点参数无效。')
            front_map[name] = node
        if set(front_map) != set(members):
            raise GistImportError('自动前置组成员与节点定义不匹配。')
        front_group = {k: front_group[k] for k in AUTO_GROUP_OPTIONS if k in front_group}
        front_group.update(name=managed_chain.FRONT, hidden=True, proxies=list(members), **{'empty-fallback': 'REJECT'})
        base_topology = 'chain_auto'
        front_label = f'自动前置组（{len(front_nodes)} 个节点）'
    elif front_nodes:
        if len(front_nodes) != 1 or exit_node.get('dialer-proxy') != managed_chain.FRONT:
            raise GistImportError('链式前置节点定义无效。')
        front_node = front_nodes[0]
        _validate_proxy_node(front_node)
        if front_node.get('dialer-proxy'):
            raise GistImportError('前置节点不允许再嵌套 dialer-proxy。')
        front_node['name'] = managed_chain.FRONT
        base_topology = 'chain'
        front_label = str(spec.get('front_label') or managed_chain.FRONT)
    else:
        if exit_node.get('dialer-proxy'):
            raise GistImportError('单节点配置不应包含 dialer-proxy。')
        base_topology = 'single'
        front_label = ''

    if static_exit is not None:
        if not isinstance(static_exit, dict):
            raise GistImportError('静态 IP 出口参数无效。')
        allowed_keys = {'name', 'dialer-proxy', 'type', 'server', 'port', 'udp', 'tls', 'username', 'password'}
        if set(exit_node) - allowed_keys:
            raise GistImportError('静态 IP 节点包含不支持的字段。')
        raw_type = exit_node.get('type')
        if raw_type not in ('socks5', 'http'):
            raise GistImportError('静态 IP 代理协议不受支持。')
        if raw_type == 'socks5' and (exit_node.get('udp') is not False or exit_node.get('tls')):
            raise GistImportError('静态 IP SOCKS5 节点参数无效。')
        if raw_type == 'http' and ('udp' in exit_node or ('tls' in exit_node and type(exit_node['tls']) is not bool)):
            raise GistImportError('静态 IP HTTP/HTTPS 节点参数无效。')
        protocol = 'socks5' if raw_type == 'socks5' else 'https' if exit_node.get('tls') is True else 'http'
        try:
            checked = managed_chain.static_exit({
                'protocol': protocol,
                'ip': exit_node.get('server'),
                'port': exit_node.get('port'),
                'username': exit_node.get('username', ''),
                'password': exit_node.get('password', ''),
            })
        except AssertionError as exc:
            raise GistImportError('静态 IP 代理参数无效。') from exc
        static_exit = {'protocol': checked['protocol'], 'ip': checked['ip'], 'port': checked['port']}
        topology = {'single': 'static_direct', 'chain': 'static_chain', 'chain_auto': 'static_chain_auto'}[base_topology]
    else:
        topology = base_topology

    direct_nodes = front_nodes if front_nodes else [exit_node]
    allowed_dns_hosts = {node['server'] for node in direct_nodes if _is_domain(node['server'])}
    clean_dns = {}
    for host, resolvers in node_dns.items():
        if not _clean_text(host, 255) or host not in allowed_dns_hosts:
            raise GistImportError('节点 DNS 策略包含非前置节点域名。')
        clean_dns[host] = _sanitize_resolvers(resolvers)

    default_name = str(spec.get('default_name') or exit_node.get('name') or '').strip()[:100]
    if not default_name:
        default_name = 'Gist导入配置'
    return {
        'exit_node': exit_node,
        'front_nodes': front_nodes,
        'front_group': front_group,
        'front_label': front_label,
        'node_dns': clean_dns,
        'static_exit': static_exit,
        'default_name': default_name,
        'topology': topology,
    }


def parse_gist_profile(raw_bytes):
    """Parse and audit android-clash-meta.yaml into a safe Windows import spec and region hint."""
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) > MAX_BYTES:
        raise GistImportError('Secret Gist 配置文件体积无效。')
    try:
        text = bytes(raw_bytes).decode('utf-8-sig')
    except UnicodeDecodeError:
        raise GistImportError('Secret Gist 配置不是有效的 UTF-8 文本。') from None

    region_hint = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith('#'):
            break
        match = REGION_COMMENT_RE.fullmatch(stripped)
        if match:
            code = match.group(1).upper()
            try:
                if region_profile.get(code)['known']:
                    region_hint = {'code': code, 'source': 'gist'}
            except ValueError:
                pass
            break

    try:
        config = loads(text)
    except yaml.YAMLError:
        raise GistImportError('Secret Gist 配置 YAML 解析失败。') from None
    if not isinstance(config, dict) or config.get('mode') != 'rule':
        raise GistImportError('该 Gist 不是有效的规则分流配置（缺少 mode: rule）。')
    if config.get('proxy-providers') or config.get('listeners') or config.get('tunnels'):
        raise GistImportError('出于安全边界限制，不支持导入包含 proxy-providers、listeners 或 tunnels 的配置。')

    raw_proxies = config.get('proxies')
    raw_groups = config.get('proxy-groups')
    rules = config.get('rules')
    if not isinstance(raw_proxies, list) or not raw_proxies or not isinstance(raw_groups, list) or not raw_groups:
        raise GistImportError('Gist 配置缺少有效的节点或代理组定义。')
    if not isinstance(rules, list) or not rules or not all(isinstance(rule, str) for rule in rules):
        raise GistImportError('Gist 配置缺少有效的分流规则。')

    nodes = {}
    groups = {}
    for entry in raw_proxies:
        if not isinstance(entry, dict) or not _clean_text(entry.get('name')):
            raise GistImportError('Gist 节点名称无效。')
        name = entry['name']
        if name in nodes:
            raise GistImportError('Gist 中存在重名节点，无法确认固定路径。')
        nodes[name] = entry
    for entry in raw_groups:
        if not isinstance(entry, dict) or not _clean_text(entry.get('name')):
            raise GistImportError('Gist 代理组名称无效。')
        name = entry['name']
        if name in nodes or name in groups:
            raise GistImportError('Gist 中存在重名代理组或节点，无法确认固定路径。')
        groups[name] = entry

    pinned = groups.get(config_health.GROUP)
    if (not pinned or pinned.get('type') != 'select'
            or not isinstance(pinned.get('proxies'), list) or len(pinned['proxies']) != 1
            or not isinstance(pinned['proxies'][0], str)
            or any(pinned.get(k) for k in ('use', 'include-all', 'include-all-proxies',
                                           'include-all-providers', 'dialer-proxy'))):
        raise GistImportError('Gist 配置缺少本项目固定代理组 Claude-Overseas-Pinned。')

    exit_name = pinned['proxies'][0]
    if exit_name not in nodes or exit_name in managed_chain.RESERVED_NAMES:
        raise GistImportError('Gist 固定代理组未指向有效具体节点。')
    exit_node = copy.deepcopy(nodes[exit_name])
    _validate_proxy_node(exit_node)

    front_ref = exit_node.get('dialer-proxy')
    front_nodes = []
    front_group = None
    front_label = ''
    if front_ref is None:
        pass
    elif not _clean_text(front_ref):
        raise GistImportError('固定出口引用的前置节点名称无效。')
    elif front_ref in nodes:
        if front_ref == exit_name:
            raise GistImportError('链式代理的前置与后置不能为同一节点。')
        front_node = copy.deepcopy(nodes[front_ref])
        if front_node.get('dialer-proxy'):
            raise GistImportError('暂不支持三级以上嵌套链式代理。')
        _validate_proxy_node(front_node)
        front_label = front_ref
        front_node['name'] = managed_chain.FRONT
        exit_node['dialer-proxy'] = managed_chain.FRONT
        front_nodes = [front_node]
    elif front_ref in groups:
        if front_ref == config_health.GROUP or front_ref in (managed_chain.RESERVED_NAMES - {managed_chain.FRONT}):
            raise GistImportError('前置代理组使用了保留名称。')
        group = groups[front_ref]
        members = group.get('proxies')
        if (group.get('type') not in managed_chain.AUTO_TYPES
                or group.get('url') not in managed_chain.HEALTH_URLS
                or type(group.get('interval')) is not int or group['interval'] <= 0
                or any(group.get(k) for k in ('use', 'include-all', 'include-all-proxies',
                                              'include-all-providers', 'dialer-proxy'))
                or group.get('empty-fallback') not in (None, '', 'REJECT')
                or not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members)
                or len(set(members)) != len(members)):
            raise GistImportError('自动前置组参数不受支持。')
        for member in members:
            if member not in nodes or member == exit_name or member in managed_chain.RESERVED_NAMES:
                raise GistImportError('自动前置组包含无效或冲突的节点。')
            member_node = nodes[member]
            if member_node.get('dialer-proxy'):
                raise GistImportError('自动前置组成员不允许包含嵌套 dialer-proxy。')
            _validate_proxy_node(member_node)
            front_nodes.append(copy.deepcopy(member_node))
        policy = {k: group[k] for k in AUTO_GROUP_OPTIONS if k in group}
        front_group = dict(policy, name=managed_chain.FRONT, hidden=True, proxies=list(members),
                           **{'empty-fallback': 'REJECT'})
        exit_node['dialer-proxy'] = managed_chain.FRONT
        front_label = f'自动前置组（{len(front_nodes)} 个节点）'
    else:
        raise GistImportError('固定出口引用的前置节点或代理组不存在。')

    if rules[-1] != 'MATCH,' + config_health.GROUP or sum(r.startswith('MATCH,') for r in rules) != 1:
        raise GistImportError('Gist 配置缺少固定代理兜底规则。')
    has_udp_fallback = len(rules) >= 2 and rules[-2] == 'NETWORK,udp,REJECT'
    has_udp_guards = any(UDP_GUARD_RE.fullmatch(rule) for rule in rules[:-1])
    static_exit_meta = None
    if has_udp_fallback:
        for index, rule in enumerate(rules[:-1]):
            parts = rule.split(',')
            if len(parts) >= 3 and parts[2] == config_health.GROUP:
                condition = ','.join(parts[:2] + parts[3:])
                expected_guard = f'AND,((NETWORK,udp),({condition})),REJECT'
                if index == 0 or rules[index - 1] != expected_guard:
                    raise GistImportError('静态 IP 配置缺少匹配的 UDP 阻断规则。')
        static_exit_meta = {'protocol': exit_node.get('type'), 'ip': exit_node.get('server'), 'port': exit_node.get('port')}
    elif has_udp_guards:
        raise GistImportError('Gist 配置的 UDP 阻断规则不完整。')

    dns = config.get('dns') or {}
    if not isinstance(dns, dict):
        raise GistImportError('Gist DNS 配置无效。')
    raw_node_dns = dns.get('proxy-server-nameserver-policy') or {}
    if not isinstance(raw_node_dns, dict):
        raise GistImportError('Gist 节点 DNS 策略格式无效。')
    direct_nodes = front_nodes if front_nodes else [exit_node]
    direct_hosts = {node['server'] for node in direct_nodes if _is_domain(node['server'])}
    node_dns = {}
    for host in sorted(direct_hosts):
        if host in raw_node_dns:
            node_dns[host] = _sanitize_resolvers(raw_node_dns[host])

    if region_hint is None:
        inferred = node_region.infer(exit_name)
        if inferred and inferred.get('code'):
            region_hint = {'code': inferred['code'], 'source': 'name'}

    default_name = exit_name[:100].strip() or 'Gist导入配置'
    spec = validate_spec({
        'exit_node': exit_node,
        'front_nodes': front_nodes,
        'front_group': front_group,
        'front_label': front_label,
        'node_dns': node_dns,
        'static_exit': static_exit_meta,
        'default_name': default_name,
    })
    return spec, region_hint


def summarize(spec, *, region_hint=None, gist_hint=''):
    """Build a credential-free preview summary for the GUI."""
    topology = spec['topology']
    static_exit = spec.get('static_exit')
    return {
        'default_name': spec['default_name'],
        'topology': topology,
        'topology_label': TOPOLOGY_LABELS.get(topology, 'Gist 导入配置'),
        'front_label': spec.get('front_label') or '',
        'front_count': len(spec.get('front_nodes') or []),
        'front_group': spec.get('front_group') is not None,
        'exit_label': static_exit['ip'] if static_exit else spec['default_name'],
        'udp_blocked': static_exit is not None,
        'region_hint': region_hint,
        'gist_hint': gist_hint,
    }


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _prune_staging(folder):
    if not folder.is_dir():
        return
    cutoff = time.time() - STAGING_TTL
    for entry in folder.iterdir():
        if not entry.is_file() or entry.is_symlink() or not TOKEN_RE.fullmatch(entry.stem):
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
        except OSError:
            pass


def stage_preview(local, parsed_url, spec, region_hint, *, replace_uid=''):
    if replace_uid and not UID_RE.fullmatch(replace_uid):
        raise GistImportError('要更新的配置标识无效。')
    folder = Path(local).resolve() / STAGING_DIR
    folder.mkdir(parents=True, exist_ok=True)
    _prune_staging(folder)
    token = uuid.uuid4().hex
    record = {
        'created': time.time(),
        'canonical_url': parsed_url['canonical_url'],
        'gist_hint': parsed_url['gist_hint'],
        'replace_uid': replace_uid or '',
        'spec': spec,
    }
    _write_json(folder / (token + '.json'), record)
    return {
        'token': token,
        'replace_uid': replace_uid or '',
        'summary': summarize(spec, region_hint=region_hint, gist_hint=parsed_url['gist_hint']),
    }


def load_staged(local, token):
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise GistImportError('导入预览令牌无效，请重新读取 Secret Gist。')
    folder = (Path(local).resolve() / STAGING_DIR).resolve()
    path = (folder / (token + '.json')).resolve()
    if path.parent != folder or path.is_symlink() or not path.is_file():
        raise GistImportError('导入预览已过期或不存在，请重新读取 Secret Gist。')
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise GistImportError('导入预览记录已损坏，请重新读取 Secret Gist。') from None
    created = record.get('created')
    if not isinstance(created, (int, float)) or not 0 <= time.time() - created <= STAGING_TTL:
        path.unlink(missing_ok=True)
        raise GistImportError('导入预览已过期，请重新读取 Secret Gist。')
    spec = validate_spec(record.get('spec'))
    parsed = parse_gist_url(record.get('canonical_url'))
    replace_uid = str(record.get('replace_uid') or '')
    if replace_uid and not UID_RE.fullmatch(replace_uid):
        raise GistImportError('导入预览目标标识无效。')
    return {
        'canonical_url': parsed['canonical_url'],
        'gist_hint': parsed['gist_hint'],
        'replace_uid': replace_uid,
        'spec': spec,
    }


def preview_from_url(local, url, *, replace_uid=''):
    parsed = parse_gist_url(url)
    raw_bytes = fetch_gist_bytes(parsed['raw_url'])
    spec, region_hint = parse_gist_profile(raw_bytes)
    return stage_preview(local, parsed, spec, region_hint, replace_uid=replace_uid)


def load_saved_sources(local):
    path = Path(local) / SOURCES_FILE
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, dict):
        return {}
    clean = {}
    for uid, row in rows.items():
        if not isinstance(uid, str) or not UID_RE.fullmatch(uid) or not isinstance(row, dict):
            continue
        try:
            parsed = parse_gist_url(row.get('url'))
        except GistImportError:
            continue
        clean[uid] = {
            'url': parsed['canonical_url'],
            'gist_hint': str(row.get('gist_hint') or parsed['gist_hint']),
            'updated_at': str(row.get('updated_at') or ''),
        }
    return clean


def saved_source(local, uid):
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
        return None
    return load_saved_sources(local).get(uid)


def updated_saved_sources(local, uid, canonical_url, gist_hint, live_uids=None):
    rows = load_saved_sources(local)
    if live_uids is not None:
        rows = {k: v for k, v in rows.items() if k in live_uids}
    parsed = parse_gist_url(canonical_url)
    rows[uid] = {
        'url': parsed['canonical_url'],
        'gist_hint': gist_hint or parsed['gist_hint'],
        'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return rows


def save_source_mapping(local, uid, canonical_url, gist_hint, live_uids=None):
    rows = updated_saved_sources(local, uid, canonical_url, gist_hint, live_uids=live_uids)
    _write_json(Path(local) / SOURCES_FILE, rows)
    return rows
