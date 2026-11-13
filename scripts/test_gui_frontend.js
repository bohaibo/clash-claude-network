// Offline behavior tests: no browser, HTTP requests, or real timers.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('gui/js/app.js', 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/init\(\);\s*$/, '');
async function main() {
  let getJob;
  const timers = [];
  const messages = [];
  class SessionError extends Error {}
  const context = vm.createContext({
    api: { getJob: (...args) => getJob(...args), SessionError },
    ui: { toast: (...args) => messages.push(args) },
    software() {}, overview() {}, network() {}, browser() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    browserHasDraft: () => true, updatesHasDraft: () => true,
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout() {},
    console,
  });
  const refreshContext = vm.createContext({ software() {}, overview() {}, network() {}, browser() {}, snapshots() {},
    updates() {}, logs() {}, help() {}, api: {}, ui: {}, console,
    browserHasDraft: () => true, updatesHasDraft: () => true });
  vm.runInContext(source + `
    renderJobDock = () => {};
    updateChrome = () => {};
    renderFatal = message => { app.fatal = message; };
    refresh = () => Promise.resolve();
    globalThis.test = { app, ctx, resumeJob, pollJob, startJob };
  `, context);
  const t = context.test;
  getJob = async () => ({ id: 'done', state: 'success', action: 'verify' });
  t.resumeJob('done');
  await new Promise(setImmediate);
  assert.equal(t.app.jobId, null, 'resumed completed job must release busy state');
  t.app.jobId = 'active';
  getJob = async () => { throw new Error('offline'); };
  await t.pollJob();
  assert.equal(t.app.jobId, 'active', 'connection loss must not permit duplicate operations');
  assert.match(t.app.jobConnectionError, /连接中断/);
  assert.equal(timers.at(-1).ms, 3000);
  getJob = async () => ({ id: 'active', state: 'running' });
  await t.pollJob();
  assert.equal(t.app.jobConnectionError, '', 'successful polling clears warning');
  const count = timers.length;
  getJob = async () => { throw new SessionError('expired'); };
  await t.pollJob();
  assert.equal(t.app.fatal, 'expired');
  assert.equal(timers.length, count, 'expired sessions must stop retrying');
  getJob = async () => { const error = new Error(); error.status = 404; throw error; };
  await t.pollJob();
  assert.equal(t.app.jobId, null);

  // Exercise refresh coalescing independently, preserving the actual implementation.
  vm.runInContext(source + `
    let calls = 0;
    let release;
    refreshState = async () => { calls++; if (calls === 1) await new Promise(r => release = r); };
    globalThis.probe = { refresh, count: () => calls, release: () => release() };
  `, refreshContext);
  const probe = refreshContext.probe;
  const first = probe.refresh();
  const second = probe.refresh(true);
  assert.equal(first, second, 'overlapping refresh calls share one promise');
  assert.equal(probe.count(), 1);
  probe.release();
  await first;
  assert.equal(probe.count(), 2, 'forced refresh is queued once');
  // Verify a status refresh replaces the status card while retaining live inputs/logs.
  const makeCard = (id, form = false) => ({ id, dataset: { preserveKey: id },
    querySelector: () => null,
    querySelectorAll: () => [],
    replaceWith(node) { const index = fresh.children.indexOf(this); fresh.children[index] = node; },
  });
  const oldStatus = makeCard('status');
  const form = makeCard('update-settings', true);
  const log = makeCard('open-log');
  const newStatus = makeCard('status');
  const fresh = { children: [makeCard('new-warning'), newStatus, makeCard('update-settings', true), makeCard('open-log')] };
  const content = { querySelector: selector => fresh.children.find(node => selector.includes('"' + node.dataset.preserveKey + '"')),
    firstElementChild: { children: [oldStatus, form, log], querySelectorAll: () => [form, log],
    classList: { contains: name => name === 'stack' } } };
  refreshContext.document = { getElementById: () => content, activeElement: null };
  refreshContext.window = { scrollY: 100, scrollTo() {} };
  refreshContext.replacePage = () => { content.firstElementChild = fresh; };
  vm.runInContext("app.page = 'updates'; renderPage = replacePage; refreshPage();", refreshContext);
  assert.equal(fresh.children[1], newStatus);
  assert.equal(fresh.children[2], form, 'form survives inserted warnings through stable keys');
  assert.equal(fresh.children[3], log, 'open logs must survive status refresh');
  const raceContext = vm.createContext({ software() {}, overview() {}, network() {}, browser() {}, snapshots() {},
    updates() {}, logs() {}, help() {}, api: {}, ui: {}, console,
    document: { getElementById: () => null } });
  vm.runInContext(source + `
    let respond;
    api.getState = () => new Promise(resolve => respond = resolve);
    updateChrome = () => {};
    refreshPage = () => {};
    app.data = {browser: {settings: 'before'}};
    globalThis.race = { begin: () => refreshState(), respond: value => respond(value),
      save: value => ctx.acceptState('browser', value), read: () => app.data.browser };
  `, raceContext);
  const pending = raceContext.race.begin();
  raceContext.race.save({settings: 'new'});
  raceContext.race.respond({browser: {settings: 'old'}, operation: {}});
  await pending;
  assert.equal(raceContext.race.read().settings, 'new', 'old HTTP responses cannot replace saved state');
  // Banner action uses the real job entry, with no HTTP or browser.
  const host = { rows: [], replaceChildren(...rows) { this.rows = rows; } };
  const posts = [];
  let acceptJob;
  const bannerContext = vm.createContext({
    software() {}, overview() {}, network() {}, browser() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    document: { getElementById: () => host }, console,
    api: { SessionError, startJob: payload => { posts.push(payload); return new Promise(r => acceptJob = r); } },
    ui: {
      el: (tag, props, ...children) => ({ tag, props, children }),
      note: (content, tone) => ({ content, tone }),
      button: (label, options) => ({ label, ...options }),
      toast() {},
    },
  });
  vm.runInContext(source + `
    updateChrome = () => renderBanners();
    renderJobDock = () => {};
    pollJob = () => {};
    refreshPage = () => {};
    app.data = { configured: true, browser: {engine: {name: 'fixture'}},
      update: { mode: 'scheduled', last: {success: false, time: 'fixture', reason: 'fixture failure'} } };
    globalThis.banner = { app, renderBanners, refreshState };
  `, bannerContext);
  const banner = bannerContext.banner;
  const notices = () => host.rows.filter(row => row.content?.props?.class === 'note-with-action');
  const button = () => notices()[0].content.children[1];
  banner.renderBanners();
  assert.equal(button().label, '立即手动更新');
  assert.equal(button().disabled, false);
  const click = button().onClick();
  assert.equal(button().disabled, true, 'disable immediately while submitting');
  await button().onClick();
  assert.equal(posts.length, 1, 'duplicate click must not start a second update');
  assert.equal(posts[0].action, 'update');
  assert.equal(posts[0].confirm, 'UPDATE');
  acceptJob({id: 'fixture-update', state: 'running', action: 'update'});
  await click;
  assert.equal(button().disabled, true, 'running job keeps update action disabled');
  banner.app.jobId = null;
  banner.app.data.update.last = {success: false, time: 'fixture', reason: 'failed again'};
  banner.renderBanners();
  assert.equal(notices().length, 1, 'failure retains warning');
  banner.app.data.update.last = {success: true, skipped: true, time: 'fixture', reason: 'association changed'};
  banner.renderBanners();
  assert.equal(notices().length, 1, 'skipped update does not dismiss warning');
  banner.app.data.update.last = {success: true, time: 'fixture'};
  banner.renderBanners();
  assert.equal(notices().length, 0, 'successful refreshed result dismisses warning');
  banner.app.data.update.last = {success: false, time: 'fixture'};
  banner.app.data.configured = false;
  banner.renderBanners();
  assert.equal(button().disabled, true, 'unconfigured project cannot update');
  // Restored/unconfigured state must not raise warnings from old plan/evidence.
  banner.app.data = { configured: false, plan: {exists: true}, collection: {},
    verification: {available: true, success: false, dns_failed: true, time: '2026-11-13T15:16:14'} };
  banner.renderBanners();
  assert.equal(host.rows.length, 0, 'unconfigured state needs no historical configuration warning');
  const loader = () => host.rows.find(row => row.props?.class === 'note configuration-loading');
  banner.app.data.collection.preview = {refreshing: true, stale: true, error: 'old read error'};
  banner.renderBanners();
  assert.ok(loader(), 'background collection displays a loader instead of an old warning');
  assert.equal(loader().props.role, 'status');
  assert.ok(loader().children.some(child => child.props?.class === 'spinner'));
  assert.equal(host.rows.length, 1, 'collectors share one loading banner');
  banner.app.data.collection = {};
  banner.renderBanners();
  assert.equal(loader(), undefined, 'loader disappears after collection completes');
  let resolveState;
  bannerContext.api.getState = () => new Promise(resolve => { resolveState = resolve; });
  banner.app.data = null;
  const initialRead = banner.refreshState(true);
  assert.ok(loader(), 'initial request shows the top loader before state exists');
  resolveState({configured: false, collection: {}});
  await initialRead;
  assert.equal(loader(), undefined, 'successful request stops the spinner');
  bannerContext.api.getState = async () => { throw new Error('fixture read failed'); };
  await banner.refreshState(true);
  assert.equal(banner.app.stateLoading, false, 'failed request also clears loading state');
  assert.equal(loader(), undefined, 'failed request does not leave a stuck spinner');
  // Diagnostics are opt-in buttons; rendering must never dispatch a job.
  const buttons = [], badges = [], jobs = [];
  const diagnosticUi = new Proxy({}, {get: (_, name) => (...args) => {
    if (name === 'button') buttons.push({label: args[0], ...args[1]});
    if (name === 'badge') badges.push(args[0]);
    return {name, args};
  }});
  const networkContext = vm.createContext({ui: diagnosticUi, Date});
  const networkSource = fs.readFileSync('gui/js/pages/network.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
  vm.runInContext(networkSource + '\nglobalThis.pageTest={diagnosticsCard,configCard};', networkContext);
  const diagnosticCtx = {state: {configured: true}, busy: false,
    confirm: async () => true, startJob: value => jobs.push(value)};
  networkContext.pageTest.diagnosticsCard(diagnosticCtx);
  assert.equal(jobs.length, 0);
  await buttons.find(b => b.label === '检查连通性').onClick();
  await buttons.find(b => b.label === '完整复核与演练').onClick();
  assert.deepEqual(jobs.map(j => j.action), ['verify', 'audit']);
  const configured = {configured: true, plan: {}, last_run: {success: true, action: 'apply',
    network_tested: false, configuration_time: '2026-11-13T20:00:00+08:00'},
    verification: {available: true, success: true, time: '2026-11-13T19:00:00+08:00'}};
  networkContext.pageTest.configCard({state: configured});
  assert.equal(badges.at(-1), '已配置');
  configured.verification.time = '2026-11-13T21:00:00+08:00';
  networkContext.pageTest.configCard({state: configured});
  assert.equal(badges.at(-1), '已配置');
  // The comparison uses matching state labels, with installation evidence in a note.
  let impact;
  const previewUi = new Proxy({}, {get: (_, name) => (...args) => ({name, args})});
  const overviewContext = vm.createContext({ui: previewUi, Intl, Date,
    host: {replaceChildren: value => { impact = value; }}});
  const overviewSource = fs.readFileSync('gui/js/pages/overview.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
  vm.runInContext(overviewSource + `
    impactHost = host;
    form.node = 'fixture-sg';
    nodesState.data = {subscriptions: [{nodes: ['fixture-sg']}]};
    globalThis.renderPreview = renderImpact;
  `, overviewContext);
  overviewContext.renderPreview({state: {preview: {current: {
    fixed_node: 'fixture-sg', routing_simple: '按规则分流',
    webrtc: {browsers: ['Chrome', 'Edge', 'Firefox'].map(browser => ({browser,
      policy_configured: true, extension_requested: browser !== 'Firefox',
      configuration_state: '已配置',
      installation: browser === 'Firefox' ? null : {state: 'partial', label: '仅 1/3 份标准资料有安装记录'}
    }))}
  }}}});
  const walk = value => Array.isArray(value) ? value.flatMap(walk) : value && typeof value === 'object'
    ? [value, ...walk(value.args || [])] : [];
  const comparison = walk(impact).filter(n => n.name === 'el' && n.args[0] === 'tr' && n.args[1]?.class === 'config-item');
  const texts = n => walk(n).filter(x => x.name === 'el' && x.args[1]?.class?.startsWith('config-value ')).map(x => x.args[1].text);
  for (const row of comparison) {
    const label = walk(row).find(n => n.name === 'el' && n.args[0] === 'span' && n.args[1]?.text)?.args[1].text;
    if (/WebRTC|国外连接节点|网站连接方式/.test(label)) {
      const [current, target] = texts(row);
      assert.ok(current, label);
      assert.equal(current, target, label + ' must match after configuration');
    }
  }
  assert.ok(walk(impact).some(n => n.args?.[1]?.text?.includes('安装记录：仅 1/3')));
  assert.ok(!JSON.stringify(impact).includes('配置完成'));
  console.log('GUI task lifecycle offline checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
