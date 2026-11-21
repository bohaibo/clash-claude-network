// Offline action routing; no browser, network, or live system operations.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('gui/js/pages/network.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/export function /g, 'function ');
const clipboard = [];
const requests = [];
let confirmUpload = false;
const ui = {
  card: (props, ...children) => ({ props, children, dataset: {} }),
  disclosure: (props, ...children) => ({ props, children }),
  el: (tag, props, ...children) => ({ tag, props, children, replaceChildren(...values) { this.children = values; } }),
  note: (text) => ({ text }), helpTip: () => null,
  button: (label, options) => ({ label, ...options }),
  input: (value) => ({ value }), field: (label, input) => ({ field: label, children: [input] }),
  select: (options, value) => ({options, value}),
  confirmDialog: async () => confirmUpload,
  toast: () => {},
};
const context = vm.createContext({ ui, URL,
  document: {getElementById: () => null},
  navigator: {clipboard: {writeText: async text => clipboard.push(text)}},
  fetch: async (href, options) => {
    requests.push({href, mode: options.mode});
    return {ok: true, text: async () => 'offline fixture'};
  },
});
vm.runInContext(source + '\nObject.assign(globalThis, {actionsCard, mobileExportCard, gistRawBase});', context);
const overview = fs.readFileSync('gui/js/pages/overview.js', 'utf8');
assert.match(overview, /rootNode\.append\(mobileExportCard\(ctx\)\);\s*return rootNode;/);
assert.match(overview, /refreshMobileExportCard\(ctx\);/);
assert.doesNotMatch(source, /root\.append\(mobileExportCard\(ctx\)\)/);
const navigations = [];
const ctx = { state: { configured: true, plan: { node: 'Chosen' }, baseline: 'first',
  collection: { preview: { stale: false, refreshing: false } },
  preview: { current: { health: { recommendation: { reason: 'fresh recommendation' } } } } },
  busy: false, navigate: page => navigations.push(page) };
function flatten(node) {
  if (Array.isArray(node)) return node.flatMap(flatten);
  return node ? [node, ...(node.children || []).flatMap(flatten)] : [];
}
let nodes = flatten(context.actionsCard(ctx));
assert.ok(nodes.some(node => node.text === 'fresh recommendation'));
nodes.find(node => node.label === '去规则更新').onClick();
nodes.find(node => node.label === '去一键配置').onClick();
assert.deepEqual(navigations, ['updates', 'overview']);
ctx.state.collection.preview.stale = true;
nodes = flatten(context.actionsCard(ctx));
assert.ok(!nodes.some(node => node.text === 'fresh recommendation'));
ctx.busy = true;
nodes = flatten(context.actionsCard(ctx));
assert.ok(nodes.filter(node => node.label).every(node => node.disabled));
console.log('Action scope UI checks passed');

async function testSecretGist() {
  const id = 'a'.repeat(32);
  const page = 'https://gist.github.com/fixture-user/' + id;
  const rawBase = 'https://gist.githubusercontent.com/fixture-user/' + id + '/raw/';
  assert.equal(context.gistRawBase(page + '#file-android'), rawBase);
  assert.equal(context.gistRawBase(rawBase + 'b'.repeat(40) + '/filename.yaml'), rawBase);
  for (const invalid of ['http://gist.github.com/fixture-user/' + id, page + '/revisions',
    'https://gist.github.com@example.invalid/fixture-user/' + id,
    'https://user:pass@gist.github.com/fixture-user/' + id, 'https://gist.github.com/fixture-user/not-a-gist']) {
    assert.equal(context.gistRawBase(invalid), null);
  }
  const href = '/api/mobile-exports/' + id + '/android';
  const iosHref = '/api/mobile-exports/' + id + '/ios';
  const posts = [];
  let expectedGist = '';
  const auto = {state: {configured: true}, busy: false, acceptState: (key, value) => { auto.state[key] = value; },
    api: {post: async (path, payload) => {
    posts.push(path);
    if (path === '/api/mobile-export') return {android: href, ios: iosHref, omitted_geo_regex: 1};
    if (path === '/api/mobile-gist') {
      assert.equal(payload.id, id);
      assert.deepEqual([...payload.platforms], ['android', 'ios']);
      assert.equal(payload.confirm, true);
      assert.equal(payload.gist_url, expectedGist);
      return {gist_url: page, binding: {url: page, platforms: ['android', 'ios']}};
    }
  }}};
  const createLabel = '同步到 Secret Gist';
  const controls = () => flatten(context.mobileExportCard(auto));
  for (const confirmed of [false, true]) {
    vm.runInContext('mobileExport = null; gistLinks = {};', context);
    posts.length = 0;
    confirmUpload = confirmed;
    const initial = controls();
    const manual = initial.find(node => node.props?.title === '手动创建 Secret Gist');
    const button = initial.find(node => node.label === createLabel);
    assert.ok(!flatten(manual).includes(button));
    assert.ok(!initial.some(node => node.field === 'Gist 地址'));
    assert.equal(button.disabled, false);
    await button.onClick();
    assert.deepEqual(posts, confirmed ? ['/api/mobile-export', '/api/mobile-gist'] : ['/api/mobile-export']);
    const updated = controls();
    assert.ok(updated.some(node => node.props?.text === '下载 Android YAML'));
    assert.ok(updated.some(node => node.props?.text === '下载 iOS CONF'));
    assert.equal(updated.some(node => node.field === 'Android 订阅链接'), confirmed);
    assert.equal(updated.some(node => node.field === 'iOS 订阅链接'), confirmed);
  }
  posts.length = 0;
  expectedGist = page;
  await controls().find(node => node.label === createLabel).onClick();
  assert.deepEqual(posts, ['/api/mobile-export', '/api/mobile-gist']);
  assert.ok(controls().some(node => node.text?.includes('已覆盖原 Gist')));
  delete auto.state.mobile_gist;
  const unbound = controls();
  const previousGist = unbound.find(node => node.field === '已有 Secret Gist（可选）').children[0];
  previousGist.value = rawBase + 'b'.repeat(40) + '/android-clash-meta.yaml';
  await unbound.find(node => node.label === createLabel).onClick();
  assert.equal(auto.state.mobile_gist.url, page);
  const androidUrl = rawBase + 'android-clash-meta.yaml';
  assert.equal(controls().find(node => node.field === 'Android 订阅链接').children[0].value, androidUrl);
  await controls().find(node => node.label === '复制 Android 订阅链接').onClick();
  assert.deepEqual(clipboard, [androidUrl]);
  assert.deepEqual(requests, []);
  const manual = flatten(controls().find(node => node.props?.title === '手动创建 Secret Gist'));
  await manual.find(node => node.label === '打开 GitHub Gist').onClick();
  assert.equal(posts.at(-1), '/api/open-github-gist');
  await manual.find(node => node.label === '复制 Android YAML 内容').onClick();
  assert.deepEqual(clipboard, [androidUrl, 'offline fixture']);
  assert.deepEqual(requests, [{href, mode: 'same-origin'}]);
  const input = manual.find(node => node.field === 'Gist 地址').children[0];
  const makeLinks = manual.find(node => node.label === '生成订阅链接');
  input.value = 'https://example.invalid/';
  makeLinks.onClick();
  assert.ok(flatten(manual).some(node => node.text?.startsWith('请粘贴')));
  const help = controls().find(node => node.props?.title === '手动创建 Secret Gist');
  assert.ok(!flatten(help).some(node => node.label === createLabel));
  input.value = page;
  manual.find(node => node.field === 'Gist 中上传的文件').children[0].value = 'ios';
  makeLinks.onClick();
  assert.ok(!controls().some(node => node.field === 'Android 订阅链接'));
  assert.equal(controls().find(node => node.field === 'iOS 订阅链接').children[0].value,
    rawBase + 'ios-shadowrocket.conf');

  const subscriptionPost = auto.api.post;
  auto.api.post = async (path, payload) => {
    if (path === '/api/mobile-gist') throw new Error('模拟 Gist 上传失败');
    return subscriptionPost(path, payload);
  };
  await controls().find(node => node.label === createLabel).onClick();
  assert.ok(controls().some(node => node.text === '模拟 Gist 上传失败'));
  assert.ok(controls().some(node => node.props?.text === '下载 Android YAML'));
  assert.ok(controls().some(node => node.field === 'iOS 订阅链接'));
  auto.api.post = subscriptionPost;

  vm.runInContext('mobileExport = {android: "/api/mobile-exports/' + id + '/android", ios_error: "离线 iOS 限制"}; gistLinks = {};', context);
  assert.ok(controls().some(node => node.text === '离线 iOS 限制'));
  assert.ok(!controls().some(node => node.props?.text === '下载 iOS CONF'));
  auto.state.configured = false;
  const blocked = controls();
  assert.ok(blocked.find(node => node.label === createLabel).disabled);
  assert.ok(blocked.some(node => node.text === '完成一键配置后可以导出。'));
}

testSecretGist().then(() => console.log('Offline Secret Gist link checks passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
