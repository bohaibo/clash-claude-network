// Offline VM fixtures: no browser, sockets, DNS, or live services.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const crypto = require('crypto');
const base = 'assets/webrtc-addon/';
const manifest = JSON.parse(fs.readFileSync(base + 'manifest.json', 'utf8'));
const key = Buffer.from(manifest.key, 'base64');
const id = [...crypto.createHash('sha256').update(key).digest().subarray(0, 16)]
  .map(n => String.fromCharCode(97 + (n >> 4), 97 + (n & 15))).join('');
assert.match(fs.readFileSync('scripts/webrtc_system.py', 'utf8'), new RegExp(id));
for (const frame of ['main', 'child', 'fallback']) {
  const context = vm.createContext({ DOMException, RTCPeerConnection: function Original() {} });
  const script = fs.readFileSync(base + 'block.js', 'utf8');
  vm.runInContext(script, context);
  for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'mozRTCPeerConnection']) {
    assert.throws(() => vm.runInContext(`new ${name}()`, context), { name: 'NotAllowedError' }, frame);
  }
  vm.runInContext('RTCPeerConnection = function Bypass() {}', context);
  assert.throws(() => vm.runInContext('new RTCPeerConnection()', context), { name: 'NotAllowedError' });
}
const applied = [];
const hooks = [];
const context = vm.createContext({ console, chrome: {
  privacy: { network: { webRTCIPHandlingPolicy: { set(value, done) { applied.push(value); done(); } } } },
  runtime: { onInstalled: { addListener(fn) { hooks.push(fn); } }, onStartup: { addListener(fn) { hooks.push(fn); } } },
} });
vm.runInContext(fs.readFileSync(base + 'background.js', 'utf8'), context);
hooks.forEach(fn => fn());
assert.equal(applied.length, 3);
assert.ok(applied.every(x => x.value === 'disable_non_proxied_udp' && x.scope === 'regular'));
console.log('Offline WebRTC add-on behavior passed');

// The setup preview must show both layers and their combined configuration.
const ui = new Proxy({}, { get: (_, name) => (...args) => name === 'el'
  ? { tag: args[0], props: args[1], children: args.slice(2).flat(Infinity) }
  : { tag: name, props: args[0], children: args.slice(1).flat(Infinity) } });
const preview = vm.createContext({ ui, icon() {} });
const source = fs.readFileSync('gui/js/pages/overview.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
vm.runInContext(source + `
  impactHost = { replaceChildren(...nodes) { globalThis.output = nodes; } };
  globalThis.renderPreview = renderImpact;
`, preview);
preview.renderPreview({ state: { preview: { current: { webrtc: { browsers: [
  {browser: 'Chrome', policy_configured: true, installation: {state: 'installed', label: '已安装'}, configuration_state: '已配置'},
  {browser: 'Edge', policy_configured: true, extension_requested: true, installation: {state: 'missing', label: '已安排自动安装'}, configuration_state: '已配置'},
  {browser: 'Firefox', policy_configured: true, configuration_state: '已配置'},
] } } } } });
const text = node => !node || typeof node !== 'object' ? '' : (node.props?.text || '') + (node.children || []).map(text).join('');
const rows = [];
function collect(node) {
  if (!node || typeof node !== 'object') return;
  if (node.props?.class === 'config-item') rows.push(node.children.map(text));
  (node.children || []).forEach(collect);
}
preview.output.forEach(collect);
assert.ok(rows.some(row => row[0].startsWith('Chrome WebRTC 策略') && row[1] === '已配置' && row[2] === row[1]));
assert.ok(rows.some(row => row[0] === 'Chrome WebRTC 配置' && row[1] === '已配置' && row[2] === row[1]));
assert.ok(rows.some(row => row[0] === 'Edge WebRTC 配置' && row[1] === '已配置' && row[2] === row[1]));
assert.ok(!rows.some(row => row[0] === 'Firefox WebRTC 扩展'));
assert.doesNotMatch(preview.output.map(text).join(''), /不代表|不等于|实际阻断未验证/);
console.log('Offline WebRTC setup preview passed');
