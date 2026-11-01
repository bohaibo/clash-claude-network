const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const expected = {
  region: (params.get('region') || '').toUpperCase(),
  language: params.get('lang') || '',
  timezone: params.get('tz') || '',
};
const status = { env: 'pending', egress: 'pending', rtc: 'pending', fingerprint: 'pending' };
const rtcState = { candidates: [], publicIps: [], localIps: [], verdict: 'pending', evaluated: false };
let egressState = null;
let busy = { rtc: false, egress: false };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chip(id, text, tone) {
  const node = $(id);
  if (!node) return;
  node.textContent = text;
  node.className = 'status-chip ' + (tone || '');
}

function mark(tone) {
  return el('span', 'mark ' + (tone || ''));
}

function row(name, value, tone) {
  const wrap = el('div', 'kv-row');
  wrap.append(el('dt', '', name));
  const dd = el('dd');
  if (tone) dd.append(mark(tone));
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value === undefined || value === null || value === '' ? '—' : String(value);
  wrap.append(dd);
  return wrap;
}

function renderExpected() {
  const host = $('expected');
  const parts = [];
  if (expected.region) parts.push('预期出口 ' + expected.region);
  if (expected.language) parts.push('预期语言 ' + expected.language);
  if (expected.timezone) parts.push('预期时区 ' + expected.timezone);
  parts.push('资料目录隔离');
  host.replaceChildren(...parts.map((text) => el('span', '', text)));
}

function languageMatch() {
  if (!expected.language) return null;
  const wanted = expected.language.toLowerCase();
  const language = (navigator.language || '').toLowerCase();
  const languages = (navigator.languages || []).map((x) => x.toLowerCase());
  return language === wanted || languages.includes(wanted);
}

function renderEnvironment() {
  const intl = Intl.DateTimeFormat().resolvedOptions();
  const offset = -new Date().getTimezoneOffset() / 60;
  const match = languageMatch();
  const tone = match === null ? 'info' : match ? 'ok' : 'warn';
  status.env = tone;
  chip('env-status', match === null ? '已读取' : match ? '语言一致' : '语言需检查', tone);
  const rows = [
    row('网页语言', navigator.language || '—', tone),
    row('语言列表', (navigator.languages || []).join(', ') || '—'),
    row('Intl locale', intl.locale || '—'),
    row('时区', intl.timeZone || '—', expected.timezone ? (intl.timeZone === expected.timezone ? 'ok' : 'warn') : null),
    row('时区偏移', 'UTC' + (offset >= 0 ? '+' : '') + offset),
    row('平台', navigator.platform || '—'),
    row('CPU 线程', navigator.hardwareConcurrency || '—'),
    row('设备内存', navigator.deviceMemory ? navigator.deviceMemory + ' GB（近似）' : '未提供'),
    row('屏幕', screen.width + ' × ' + screen.height + ' @' + (window.devicePixelRatio || 1) + 'x'),
    row('Cookie 可用', navigator.cookieEnabled ? '是' : '否'),
    row('WebDriver', navigator.webdriver ? '是（自动化标记）' : '否', navigator.webdriver ? 'warn' : 'ok'),
    row('User-Agent', navigator.userAgent),
  ];
  $('environment').replaceChildren(...rows);
  updateOverall();
}

async function sha256Short(text) {
  if (window.crypto && crypto.subtle) {
    const data = new TextEncoder().encode(text);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return Array.from(new Uint8Array(digest)).slice(0, 8).map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function webglInfo() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (!gl) return { available: false };
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      available: true,
      vendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    };
  } catch {
    return { available: false };
  }
}

async function renderFingerprint() {
  const canvas = document.createElement('canvas');
  canvas.width = 260;
  canvas.height = 70;
  const context = canvas.getContext('2d');
  let canvasValue = '不可用';
  if (context) {
    context.fillStyle = '#f6f6f3';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = '#151514';
    context.font = '16px "Microsoft YaHei", sans-serif';
    context.fillText('Claude sandbox check 中文', 12, 28);
    context.fillStyle = 'rgba(21,21,20,.55)';
    context.fillRect(12, 38, 200, 12);
    try {
      canvasValue = await sha256Short(canvas.toDataURL());
    } catch {
      canvasValue = '不可用';
    }
  }
  const gl = webglInfo();
  const rows = [
    row('Canvas 哈希', canvasValue),
    row('WebGL 可用', gl.available ? '是' : '否', gl.available ? 'ok' : 'warn'),
    row('WebGL 厂商', gl.vendor || '—'),
    row('WebGL 渲染器', gl.renderer || '—'),
    row('色彩深度', screen.colorDepth + ' bit'),
    row('设备像素比', String(window.devicePixelRatio || 1)),
  ];
  $('fingerprint').replaceChildren(...rows);
  status.fingerprint = 'info';
  chip('fp-status', '已读取', 'info');
  updateOverall();
}

async function fetchJson(url, timeout) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { cache: 'no-store', signal: controller.signal, credentials: 'omit' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function checkEgress() {
  if (busy.egress) return;
  busy.egress = true;
  const button = $('check-egress');
  button.disabled = true;
  chip('egress-status', '检测中', 'info');
  $('egress').replaceChildren(row('状态', '正在请求出口信息…'));
  const endpoints = [
    { url: 'https://ipapi.co/json/', map: (data) => ({ ip: data.ip, country: data.country_code, city: data.city, region: data.region, org: data.org }) },
    { url: 'https://ipinfo.io/json', map: (data) => ({ ip: data.ip, country: data.country, city: data.city, region: data.region, org: data.org }) },
  ];
  let data = null;
  let lastError = '';
  for (const endpoint of endpoints) {
    try {
      const raw = await fetchJson(endpoint.url, 14000);
      data = endpoint.map(raw);
      if (data?.ip) break;
    } catch (error) {
      lastError = error?.name === 'AbortError' ? '请求超时' : (error?.message || '请求失败');
    }
  }
  busy.egress = false;
  button.disabled = false;
  if (!data?.ip) {
    status.egress = 'warn';
    chip('egress-status', '未检测', 'warn');
    $('egress').replaceChildren(
      row('状态', '第三方出口接口不可用', 'warn'),
      row('原因', lastError || '接口被拦截或限流'),
      row('预期地区', expected.region || '未设置'),
    );
    updateOverall();
    return;
  }
  egressState = data;
  const country = (data.country || '').toUpperCase();
  const regionTone = !expected.region ? 'info' : country === expected.region ? 'ok' : 'bad';
  status.egress = regionTone;
  chip('egress-status', regionTone === 'ok' ? '地区一致' : regionTone === 'bad' ? '地区不一致' : '已读取', regionTone);
  $('egress').replaceChildren(
    row('公网 IP', data.ip, regionTone),
    row('出口国家', country || '—', regionTone),
    row('城市/地区', [data.city, data.region].filter(Boolean).join(' · ') || '—'),
    row('网络组织', data.org || '—'),
    row('预期地区', expected.region || '未设置', expected.region ? (country === expected.region ? 'ok' : 'bad') : null),
  );
  if (rtcState.evaluated) evaluateRtc(rtcState.candidates);
  updateOverall();
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// browserleaks-style approach: ask several public STUN servers what address the
// browser appears to come from, then compare that address with the public exit.
const STUN_SERVERS = [
  'stun:stun.l.google.com:19302',
  'stun:stun1.l.google.com:19302',
  'stun:stun.cloudflare.com:3478',
  'stun:stun.nextcloud.com:443',
  'stun:stun.services.mozilla.com:3478',
];

async function collect(useStun) {
  const configuration = useStun
    ? { iceServers: STUN_SERVERS.map((urls) => ({ urls })) }
    : { iceServers: [] };
  const pc = new RTCPeerConnection(configuration);
  const candidates = [];
  pc.createDataChannel('probe');
  pc.onicecandidate = (event) => {
    if (!event.candidate) return;
    const value = event.candidate;
    candidates.push({
      type: value.type || 'unknown',
      protocol: value.protocol || 'unknown',
      address: value.address || '(hidden)',
      port: value.port || '',
    });
  };
  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      if (pc.iceGatheringState === 'complete') break;
      await wait(150);
    }
  } finally {
    pc.close();
  }
  return candidates;
}

function normalizeIp(value) {
  return String(value || '').trim().toLowerCase().replace(/^\[|\]$/g, '').replace(/^::ffff:/, '');
}

function isPrivateIp(value) {
  const address = normalizeIp(value);
  if (!address) return false;
  if (address.endsWith('.local')) return true;
  if (address === '::1') return true;
  if (address.includes(':')) return address.startsWith('fe80') || address.startsWith('fc') || address.startsWith('fd');
  const parts = address.split('.').map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 || parts[0] === 127 || (parts[0] === 192 && parts[1] === 168) ||
    (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 169 && parts[1] === 254);
}

function evaluateRtc(candidates) {
  const srflx = candidates.filter((item) => item.type === 'srflx');
  const host = candidates.filter((item) => item.type === 'host');
  const relay = candidates.filter((item) => item.type === 'relay');
  const publicIps = [...new Set(srflx.map((item) => normalizeIp(item.address)).filter((ip) => ip && !isPrivateIp(ip)))];
  const localIps = [...new Set(host.map((item) => normalizeIp(item.address)).filter((ip) => ip && isPrivateIp(ip) && !ip.endsWith('.local')))];
  const mdns = host.some((item) => normalizeIp(item.address).endsWith('.local'));
  const egressIp = normalizeIp(egressState?.ip);
  let verdict = 'unknown';
  if (publicIps.length) {
    if (!egressIp) verdict = 'uncompared';
    else verdict = publicIps.some((ip) => ip !== egressIp) ? 'leak' : (localIps.length ? 'same-local' : 'same');
  } else if (relay.length) {
    verdict = 'relay-only';
  } else if (candidates.length) {
    verdict = localIps.length ? 'local' : 'no-public';
  } else {
    verdict = 'blocked';
  }
  rtcState.candidates = candidates;
  rtcState.publicIps = publicIps;
  rtcState.localIps = localIps;
  rtcState.verdict = verdict;
  rtcState.evaluated = true;
  const tones = { same: 'ok', 'same-local': 'warn', 'no-public': 'ok', local: 'warn', leak: 'bad', uncompared: 'warn', 'relay-only': 'warn', blocked: 'info', unknown: 'warn' };
  status.rtc = tones[verdict] || 'warn';
  const labels = {
    same: '与出口一致',
    'same-local': '出口一致/本地可见',
    'no-public': '未发现公网候选',
    local: '本地地址可见',
    leak: '检测到泄漏',
    uncompared: '无法比较',
    'relay-only': '仅中继候选',
    blocked: '未发现公网候选',
    unknown: '无法判断',
  };
  chip('rtc-status-chip', labels[verdict], status.rtc);
  $('rtc-status').textContent = verdictText(verdict, publicIps, localIps, mdns);
  renderRtcResults();
  updateOverall();
}

function verdictText(verdict, publicIps, localIps, mdns) {
  if (verdict === 'same') return 'STUN 看到的公网地址与当前出口 IP 相同，未发现 WebRTC 绕过代理。';
  if (verdict === 'same-local') return 'STUN 公网地址与出口一致，但 host 候选暴露了本地网卡地址（' + localIps.join(', ') + '）；这不是公网出口泄漏，但属于本地地址暴露。';
  if (verdict === 'leak') return 'STUN 返回了与出口不同的公网 IP；这属于 WebRTC 公网地址泄漏，需要检查代理/TUN/浏览器策略。';
  if (verdict === 'uncompared') return '发现了 WebRTC 公网候选，但公网出口未读取，暂时无法比较。';
  if (verdict === 'relay-only') return '只发现 TURN 中继候选；中继地址是服务器地址，不等同于你的出口 IP。';
  if (verdict === 'local') return '只发现本机 host 候选，未发现另一个公网 IP；但本地网卡地址可见（' + localIps.join(', ') + '）。';
  if (verdict === 'no-public') return '只发现本机/mDNS 候选，未发现 STUN 公网地址；未观察到公网 IP 泄漏。' + (mdns ? ' host 地址已用 mDNS 隐藏。' : '');
  if (verdict === 'blocked') return 'WebRTC 未返回任何候选；未观察到公网 IP 泄漏。可能是策略/网络阻断了 STUN，不能据此证明已完全阻断。';
  return '未收集到候选。可能是浏览器策略、网络或 STUN 阻断；这不能证明没有泄漏，也不能证明已阻断。';
}

function candidateNote(item, publicIps) {
  const address = normalizeIp(item.address);
  if (item.type === 'srflx') return publicIps.includes(address) ? 'STUN 公网地址' : 'STUN 候选';
  if (item.type === 'relay') return 'TURN 中继服务器';
  if (item.type === 'host') return address.endsWith('.local') ? 'mDNS 隐藏' : isPrivateIp(address) ? '本地网卡地址' : 'host';
  return '';
}

function renderRtcResults() {
  const host = $('rtc-results');
  if (!rtcState.evaluated) {
    host.replaceChildren(el('div', 'summary', '正在检测…'));
    return;
  }
  const tone = status.rtc === 'bad' ? 'bad' : status.rtc === 'warn' ? 'warn' : '';
  const summary = el('div', 'summary ' + tone);
  const labels = {
    same: 'WebRTC 公网 IP 与出口一致',
    'same-local': '公网一致，但本地地址可见',
    'no-public': '未发现 WebRTC 公网候选',
    local: '仅发现本地 host 候选',
    leak: '检测到 WebRTC 公网 IP 泄漏',
    uncompared: '发现公网候选，无法比较',
    'relay-only': '仅发现 TURN 中继候选',
    blocked: 'WebRTC 未返回候选',
    unknown: '未能完成 WebRTC 判断',
  };
  summary.append(el('strong', '', labels[rtcState.verdict] || 'WebRTC'));
  summary.append(el('p', '', verdictText(rtcState.verdict, rtcState.publicIps, rtcState.localIps, false)));
  const compare = el('div', 'rtc-compare');
  const rows = [
    ['公网出口 IP', egressState?.ip || '未读取', egressState?.ip ? 'ok' : 'warn'],
    ['WebRTC 公网 IP', rtcState.publicIps.length ? rtcState.publicIps.join(', ') : '未发现', rtcState.publicIps.length ? (rtcState.verdict === 'leak' ? 'bad' : 'ok') : ''],
    ['本地 host 地址', rtcState.localIps.length ? rtcState.localIps.join(', ') : '未发现', rtcState.localIps.length ? 'warn' : ''],
  ];
  for (const [name, value, rowTone] of rows) {
    const line = el('div', 'rtc-compare-row');
    line.append(el('span', '', name), el('strong', '', value), mark(rowTone));
    compare.append(line);
  }
  host.replaceChildren(summary, compare);
  if (rtcState.candidates.length) {
    const table = el('div', 'candidate-table');
    for (const item of rtcState.candidates) {
      const line = el('div', 'candidate');
      const tag = el('span', 'tag ' + (item.type === 'srflx' ? (rtcState.verdict === 'leak' ? 'bad' : 'ok') : item.type === 'host' ? 'ok' : 'warn'), item.type);
      line.append(tag, el('span', '', item.protocol), el('span', 'addr', item.address + (item.port ? ':' + item.port : '')), el('span', 'candidate-note', candidateNote(item, rtcState.publicIps)));
      table.append(line);
    }
    host.append(table);
  }
}

async function checkRtc(useStun) {
  if (busy.rtc) return;
  busy.rtc = true;
  $('rtc-host').disabled = true;
  $('rtc-stun').disabled = true;
  chip('rtc-status-chip', '检测中', 'info');
  $('rtc-status').textContent = useStun ? '正在通过多个 STUN 服务器收集公网候选（最多 8 秒）…' : '正在收集本机候选（最多 8 秒）…';
  $('rtc-results').replaceChildren(el('div', 'summary', '正在检测…'));
  try {
    const candidates = await collect(useStun);
    evaluateRtc(candidates);
  } catch (error) {
    status.rtc = 'warn';
    chip('rtc-status-chip', '未完成', 'warn');
    $('rtc-status').textContent = '检测失败：' + (error?.message || '浏览器拒绝了 WebRTC 调用');
    $('rtc-results').replaceChildren(el('div', 'summary warn', 'WebRTC 检测未完成；这不等于通过，也不等于泄漏。'));
  } finally {
    busy.rtc = false;
    $('rtc-host').disabled = false;
    $('rtc-stun').disabled = false;
    updateOverall();
  }
}

function updateOverall() {
  const values = Object.values(status);
  if (values.includes('pending')) {
    $('overall').className = 'overall';
    $('overall').replaceChildren(el('span', 'spinner'), el('span', '', '正在完成检测…'));
    return;
  }
  const bad = values.filter((x) => x === 'bad').length;
  const warn = values.filter((x) => x === 'warn').length;
  const info = values.filter((x) => x === 'info').length;
  const tone = bad ? 'bad' : warn ? 'warn' : 'ok';
  $('overall').className = 'overall ' + tone;
  const strong = el('strong', '', bad ? '有 ' + bad + ' 项需要处理' : warn ? '有 ' + warn + ' 项需要检查' : '关键项已读取');
  const rtcText = { same: '与出口一致', 'same-local': '本地地址可见', 'no-public': '无公网候选', local: '本地地址可见', leak: '检测到泄漏', uncompared: '无法比较', 'relay-only': '仅中继候选', blocked: '无公网候选', unknown: '无法判断' }[rtcState.verdict] || '检测中';
  const summary = el('span', '', '语言/时区 ' + (status.env === 'ok' ? '一致' : status.env === 'warn' ? '需检查' : '已读取')
    + ' · 出口 ' + (status.egress === 'ok' ? '一致' : status.egress === 'warn' ? '未检测/需检查' : status.egress === 'bad' ? '不一致' : '已读取')
    + ' · WebRTC ' + rtcText
    + (info ? ' · ' + info + ' 项仅作记录' : ''));
  $('overall').replaceChildren(strong, summary);
}

$('recheck').addEventListener('click', () => location.reload());
$('check-egress').addEventListener('click', checkEgress);
$('rtc-host').addEventListener('click', () => checkRtc(false));
$('rtc-stun').addEventListener('click', () => checkRtc(true));

renderExpected();
renderEnvironment();
renderFingerprint();
checkRtc(true);
checkEgress();
