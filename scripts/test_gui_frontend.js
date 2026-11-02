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
    overview() {}, network() {}, browser() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    browserHasDraft: () => true, updatesHasDraft: () => true,
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout() {},
    console,
  });
  const refreshContext = vm.createContext({ overview() {}, network() {}, browser() {}, snapshots() {},
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
  const raceContext = vm.createContext({ overview() {}, network() {}, browser() {}, snapshots() {},
    updates() {}, logs() {}, help() {}, api: {}, ui: {}, console });
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
  console.log('GUI task lifecycle offline checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
