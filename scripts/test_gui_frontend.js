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
  const jobRefreshes = [];
  class SessionError extends Error {}
  const context = vm.createContext({
    api: { getJob: (...args) => getJob(...args), SessionError },
    ui: { toast: (...args) => messages.push(args) },
    recordRefresh: force => jobRefreshes.push(force),
    software() {}, overview() {}, network() {}, browser() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    browserHasDraft: () => true, updatesHasDraft: () => true,
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout() {},
    console,
  });
  const refreshContext = vm.createContext({ software() {}, overview() {}, network() {}, browser() {}, snapshots() {},
    updates() {}, logs() {}, help() {}, api: {}, ui: {}, console,
    document: { visibilityState: 'hidden' }, clearTimeout() {},
    browserHasDraft: () => true, updatesHasDraft: () => true });
  vm.runInContext(source + `
    renderJobDock = () => {};
    updateChrome = () => {};
    renderFatal = message => { app.fatal = message; };
    refresh = async force => recordRefresh(force);
    globalThis.test = { app, ctx, resumeJob, pollJob, startJob };
  `, context);
  const t = context.test;
  getJob = async () => ({ id: 'done', state: 'success', action: 'verify' });
  t.resumeJob('done');
  await new Promise(setImmediate);
  assert.equal(t.app.jobId, null, 'resumed completed job must release busy state');
  assert.deepEqual(jobRefreshes, [true], 'the completed job refreshes state once');
  t.app.jobId = 'active';
  getJob = async () => { throw new Error('offline'); };
  await t.pollJob();
  assert.equal(t.app.jobId, 'active', 'connection loss must not permit duplicate operations');
  assert.match(t.app.jobConnectionError, /连接中断/);
  assert.equal(timers.at(-1).ms, 3000);
  getJob = async () => ({ id: 'active', state: 'running' });
  await t.pollJob();
  assert.equal(t.app.jobConnectionError, '', 'successful polling clears warning');
  assert.deepEqual(jobRefreshes, [true], 'running jobs only read their own progress');
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
  let guideRefreshes=0;
  refreshContext.refreshHelp=()=>{guideRefreshes++;};
  // Use the same live stack shape as a rendered guide; its static DOM stays in place.
  content.firstElementChild.classList={contains:name=>name==='stack'};
  vm.runInContext("app.page='help';refreshPage();",refreshContext);
  assert.equal(guideRefreshes,1,'refreshing the guide updates its dynamic status');
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
  const host = { rows: [], get childNodes() { return this.rows; }, replaceChildren(...rows) { this.rows = rows; } };
  const domNode = props => ({ ...props, isEqualNode(other) { return JSON.stringify(this) === JSON.stringify(other); } });
  const statePolls = [];
  const posts = [];
  let acceptJob;
  const bannerContext = vm.createContext({
    software() {}, overview() {}, network() {}, browser() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    document: { visibilityState: 'visible', getElementById: () => host }, console,
    setTimeout: (fn, ms) => statePolls.push({fn, ms}), clearTimeout() {},
    api: { SessionError, startJob: payload => { posts.push(payload); return new Promise(r => acceptJob = r); } },
    ui: {
      el: (tag, props, ...children) => domNode({ tag, props, children }),
      note: (content, tone) => domNode({ content, tone }),
      button: (label, options) => ({ label, ...options }),
      formatTime: time => time,
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
    globalThis.banner = { app, renderBanners, refresh, refreshManually };
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
  const collecting = {configured: false, collection: {
    update: {refreshing: true, stale: true}, service: {refreshing: true, stale: false},
  }};
  let resolveState;
  bannerContext.api.getState = () => new Promise(resolve => { resolveState = resolve; });
  banner.app.data = null;
  const initialRead = banner.refresh(true);
  assert.ok(loader(), 'initial request shows the top loader before state exists');
  resolveState(collecting);
  await initialRead;
  assert.equal(loader(), undefined, 'a bounded read that returns incomplete results stops the spinner');
  assert.match(host.rows[0].content, /本次读取未完成/);
  assert.equal(statePolls.length, 0, 'an incomplete read does not schedule another status request');
  const manual = banner.refreshManually();
  assert.equal(loader().props.role, 'status');
  assert.match(loader().children[1].props.text, /规则更新和 Clash 服务/);
  const cached = {configured: false, collection: {}};
  resolveState(cached);
  await manual;
  assert.equal(loader(), undefined, 'manual feedback ends with the same request');
  assert.equal(statePolls.length, 0, 'manual refresh does not start polling');
  bannerContext.api.getState = async () => { throw new Error('fixture read failed'); };
  await banner.refresh();
  assert.equal(banner.app.data, cached, 'failed request keeps the previous state');
  assert.match(host.rows[0].content, /当前显示上次成功读取的状态/);
  assert.equal(banner.app.stateLoading, false, 'failed request also clears loading state');
  assert.equal(loader(), undefined, 'failed request does not leave a stuck spinner');
  bannerContext.api.getState = async () => ({configured: false, collection: {
    update: {refreshing: true, error: 'fixture collector failed', read_at: 'fixture time'},
  }});
  await banner.refresh();
  assert.match(host.rows[0].content, /fixture collector failed.*fixture time/);
  assert.equal(loader(), undefined, 'retrying a failed collector keeps its warning visible');
  bannerContext.api.getState = async () => ({configured: false, collection: {}});
  await banner.refresh();
  assert.equal(host.rows.length, 0, 'successful results dismiss stale warnings');
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
  assert.deepEqual(buttons.map(b => b.label), ['完整网络验收']);
  await buttons[0].onClick();
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].action, 'audit');
  assert.equal(jobs[0].confirm, 'AUDIT');
  diagnosticCtx.busy = true;
  await buttons[0].onClick();
  assert.equal(jobs.length, 1, 'busy state blocks another audit');
  const configured = {configured: true, plan: {}, last_run: {success: true, action: 'apply',
    network_tested: false, configuration_time: '2026-11-13T20:00:00+08:00'},
    verification: {available: true, success: true, time: '2026-11-13T19:00:00+08:00'}};
  networkContext.pageTest.configCard({state: configured});
  assert.equal(badges.at(-1), '已配置');
  configured.verification.time = '2026-11-13T21:00:00+08:00';
  networkContext.pageTest.configCard({state: configured});
  assert.equal(badges.at(-1), '已配置');
  // After startup, heartbeat and visibility events never read system state.
  const startupPosts = [];
  const intervals = [], listeners = {};
  let startupReads = 0, heartbeats = 0;
  const startupContext = vm.createContext({overview() {}, network() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    api: { bootstrap: async () => {}, heartbeat: async () => { heartbeats++; },
      getState: async () => { startupReads++; return {}; },
      post: async (...args) => { startupPosts.push(args); return {status: 'available'}; } }, ui:{}, console,
    location: {hash: ''}, window: {addEventListener() {}},
    document: {visibilityState: 'visible', getElementById: () => null,
      addEventListener: (event, listener) => { listeners[event] = listener; }},
    setInterval: fn => intervals.push(fn)});
  vm.runInContext(source + `
    bootShell = () => {};
    renderPage = () => {};
    refreshPage = () => {};
    updateChrome = () => {};
    globalThis.startup = {init, checkSoftware, app};
  `, startupContext);
  await startupContext.startup.init();
  await new Promise(setImmediate);
  assert.equal(intervals.length, 1);
  intervals[0]();
  startupContext.document.visibilityState = 'hidden';
  listeners.visibilitychange();
  startupContext.document.visibilityState = 'visible';
  listeners.visibilitychange();
  assert.equal(heartbeats, 3);
  assert.equal(startupReads, 1);
  assert.equal(startupPosts.length, 1);
  assert.equal(startupPosts[0][1].wait, true);
  assert.equal(startupContext.startup.app.softwareCheckStarted, true);
  assert.equal(startupContext.startup.app.data.software.status, 'available');
  await startupContext.startup.checkSoftware();
  assert.equal(startupPosts.length, 2);
  assert.equal(startupReads, 1, 'explicit software checks do not read network status');
  // The comparison uses matching state labels, with installation evidence in a note.
  let impact;
  const previewUi = new Proxy({}, {get: (_, name) => (...args) => ({name, args, replaceChildren() {}})});
  const overviewContext = vm.createContext({ui: previewUi, Intl, Date,
    host: {replaceChildren: value => { impact = value; }}});
  const overviewSource = fs.readFileSync('gui/js/pages/overview.js', 'utf8')
    .replace(/^import .*;\r?\n/gm, '').replace(/export /g, '');
  vm.runInContext(overviewSource + `
    impactHost = host;
    form.mode = 'single';
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
  assert.ok(walk(impact).some(n => n.name === 'helpTip' && n.args?.[0]?.includes('安装记录：仅 1/3')), 'installation evidence remains available in contextual help');
  assert.ok(!JSON.stringify(impact).includes('配置完成'));
  let chainChildren;
  overviewContext.chainHost = { replaceChildren(...children) {
    chainChildren = children.map(child => typeof child === 'object' && child !== null ? child : String(child));
  } };
  vm.runInContext(`
    choicesHost = chainHost;
    impactHost = null;
    form.mode = 'chain';
    form.frontNode = 'fixture-hk';
    globalThis.renderChain = (region, manual) => {
      form.region = region;
      form.regionManual = manual;
      renderChainChoices({ state: {}, busy: false });
    };
  `, overviewContext);
  overviewContext.renderChain('SG', false);
  assert.ok(!chainChildren.includes('null'), 'hidden region hints must not appear as text');
  assert.ok(chainChildren.some(child => child.args?.[1]?.text === '电脑 → fixture-hk → fixture-sg → 目标网站'));
  assert.ok(chainChildren.some(child => child.name === 'checkbox' && child.args[1].includes('我已核对两跳节点')));
  overviewContext.renderChain('', false);
  assert.ok(chainChildren.some(child => child.args?.[0] === '未识别最终出口地区，请手动选择。'));
  overviewContext.renderChain('SG', true);
  assert.ok(chainChildren.some(child => child.args?.[0] === '出口地区为手动选择'));
  console.log('GUI task lifecycle offline checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
