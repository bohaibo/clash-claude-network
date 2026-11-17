// Offline DOM and API-contract regressions. No browser, network or real timers.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
class Element {
  constructor(tag, doc) { this.tagName = tag; this.doc = doc; this.children = []; this.attrs = {}; this.handlers = {}; this.dataset = {}; this.style={}; this.scrollTop = 0; this.scrollLeft=0; this.scrollHeight=doc.fixtureHeight || 500; this.clientHeight=100; this.disabled = false; }
  append(...nodes) { for (const node of nodes) { if (!node) continue; node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set textContent(text) { this.text = String(text); this.children = []; }
  get isConnected() { return this === this.doc.root || Boolean(this.parentNode?.isConnected); }
  focus() { this.doc.activeElement = this; }
  scrollIntoView() { this.scrolledIntoView = true; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  matches(selector) {
    if (selector.startsWith('.')) return (this.className || '').split(' ').includes(selector.slice(1));
    if (selector.startsWith('#')) return this.attrs.id === selector.slice(1) || this.id === selector.slice(1);
    const attribute = selector.match(/^\[([^=]+)="([^"]*)"\]$/);
    return attribute ? this.attrs[attribute[1]] === attribute[2] : this.tagName === selector;
  }
  querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function environment(page) {
  const document = {activeElement: null, createElement: tag => new Element(tag, document),
    createTextNode: text => { const node = new Element('#text', document); node.textContent = text; return node; },
    getElementById: id => document.root?.querySelector('#' + id)};
  const context = vm.createContext({document, Node: Element, icon: () => document.createElement('svg'),
    setTimeout: () => 0, clearTimeout() {}, console,
    overview() {}, network() {}, snapshots() {}, updates() {}, logs() {}, help() {},
    refreshSoftwareControl() {}, api: {}, updatesHasDraft: () => false});
  const strip = text => text.replace(/^import .*;\r?\n/gm, '').replace(/export /g, '').replace(/init\(\);\s*$/, '');
  const uiSource = fs.readFileSync('gui/js/ui.js', 'utf8');
  const names = [...uiSource.matchAll(/export function (\w+)/g)].map(match => match[1]);
  vm.runInContext(strip(uiSource) + '\nglobalThis.ui = {' + names.join(',') + '};', context);
  document.root = document.createElement('div');
  vm.runInContext(strip(fs.readFileSync(page, 'utf8')), context);
  return {document, context, expose: code => vm.runInContext(code, context)};
}
function byText(root, text) { return root.querySelectorAll('button').find(button => button.textContent === text); }
async function main() {
  const now = Date.now();
  const logEnv=environment('gui/js/app.js');
  const logHost=logEnv.document.createElement('div');logHost.id='log-inline';logEnv.document.root.append(logHost);
  logEnv.expose('globalThis.readLog=openLogInline;');
  const logRequests={};logEnv.context.api.getLog=name=>new Promise((resolve,reject)=>{logRequests[name]={resolve,reject};});
  const firstLog=logEnv.context.readLog('A'),secondLog=logEnv.context.readLog('B');
  logRequests.B.resolve({name:'B',text:'new selection'});await secondLog;
  logRequests.A.resolve({name:'A',text:'old selection'});await firstLog;
  assert(logHost.textContent.includes('new selection'));assert(!logHost.textContent.includes('old selection'));
  const failedOld=logEnv.context.readLog('C'),newest=logEnv.context.readLog('D');
  logRequests.D.resolve({name:'D',text:'latest selection'});await newest;
  logRequests.C.reject(new Error('old error'));await failedOld;
  assert(logHost.textContent.includes('latest selection'));assert(!logHost.textContent.includes('old error'));
  const state = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')) : {
    configured: true, last_run: {success: true, action: 'apply', network_tested: false, configuration_time: new Date(now - 1800000).toISOString()},
    verification: {available: true, success: true, source_matches_current: true, time: new Date(now - 3600000).toISOString()},
    plan: {node: 'Offline chosen'}, operation: {}};
  assert.equal(state.last_run.network_tested, false, 'API must retain explicit untested status');
  assert(Number.isFinite(Date.parse(state.last_run.configuration_time)), 'API must retain configuration time');
  const shell = environment('gui/js/app.js');
  for (const id of ['banners', 'top-chips', 'side-status']) { const node = shell.document.createElement('div'); node.id = id; shell.document.root.append(node); }
  shell.context.fixture = state;
  shell.expose('app.data=fixture;globalThis.probe={app,updateChrome,startJob};renderJobDock=()=>{};pollJob=()=>{};navigate=page=>{globalThis.destination=page;};');
  const app = shell.context.probe;
  app.updateChrome();
  assert(shell.document.getElementById('side-status').textContent.includes('本次配置未验收'));
  assert(shell.document.getElementById('top-chips').textContent.includes('本次配置未验收'));
  assert(!shell.document.getElementById('top-chips').querySelector('.badge-ok'), 'old success must not turn new configuration green');
  state.verification.time = new Date(now - 300000).toISOString();
  state.verification.coverage='full-network';
  app.updateChrome();
  assert(shell.document.getElementById('side-status').textContent.includes('验收通过'));
  for(const [coverage,label] of [['partial','部分阶段检查通过'],['connectivity','连通性检查通过'],['legacy','范围待核对'],['full-network','完整网络验收通过']]){
    state.verification.coverage=coverage;app.updateChrome();
    assert(shell.document.getElementById('side-status').textContent.includes(label));
    assert(shell.document.getElementById('top-chips').textContent.includes(label));
    if(coverage==='partial'||coverage==='legacy')assert(!shell.document.getElementById('top-chips').querySelector('.badge-ok'));
  }
  for(const operation of [{pending_tun:[{status:'failed'}]},{pending_firewall:true},{pending_rule_update:true},{settings_pending:[{}]},{reconfigure_active:true}]){
    state.operation=operation;app.updateChrome();
    assert(!shell.document.getElementById('top-chips').querySelector('.badge-ok'),'pending recovery cannot look healthy');
    assert(!shell.document.getElementById('side-status').textContent.includes('验收通过'));
  }
  state.operation={};
  const originalRun = state.last_run;
  state.last_run = {success:true, action:'snapshot'};
  app.updateChrome();
  assert(!shell.document.getElementById('top-chips').querySelector('.badge-ok'), 'a later snapshot must not let old acceptance become current evidence');
  assert(shell.document.getElementById('side-status').textContent.includes('仅有历史验收记录'));
  state.last_run = originalRun;
  state.operation.pending_rule_update = true;
  app.updateChrome();
  const banner = shell.document.getElementById('banners').children[0];
  assert(banner.textContent.includes('上次规则更新尚未恢复'));
  byText(banner, '去恢复上次更新').handlers.click();
  assert.equal(shell.context.destination, 'updates');
  let posts = 0;
  shell.context.api.startJob = async () => { posts++; return {id: 'fixture', state: 'running'}; };
  shell.context.ui.toast = () => {};
  for (const action of ['apply', 'supplement', 'restore', 'snapshot', 'audit', 'verify', 'update']) await app.startJob({action});
  assert.equal(posts, 0, 'pending recovery blocks conflicting requests before HTTP');
  await app.startJob({action: 'update-recover'});
  assert.equal(posts, 1, 'recovery channel remains available');
  app.app.jobId = 'running'; app.updateChrome();
  assert(!byText(shell.document.getElementById('banners'), '去恢复上次更新').disabled, 'navigation remains possible during a job');
  app.app.jobId=null;state.operation.pending_rule_update=false;
  state.operation.settings_pending=[{id:'a'.repeat(32)}];
  await app.startJob({action:'apply'});assert.equal(posts,1);
  await app.startJob({action:'settings-recover'});assert.equal(posts,2,'settings recovery remains available');
  app.app.jobId=null;state.operation.pending_tun=[{id:'b'.repeat(32),status:'failed'}];
  app.updateChrome();
  byText(shell.document.getElementById('banners'),'查看恢复记录').handlers.click();
  assert.equal(shell.context.destination,'logs');
  await app.startJob({action:'settings-recover'});assert.equal(posts,2,'TUN recovery pauses every project job');
  state.operation={};

  const dockEnv=environment('gui/js/app.js');
  const dock=dockEnv.document.createElement('div');dock.id='job-dock';dockEnv.document.root.append(dock);
  dockEnv.expose('globalThis.dockProbe={app,renderJobDock};');
  const dp=dockEnv.context.dockProbe;
  dp.app.job={id:'offline-a',action:'apply',state:'running',events:[{type:'step',title:'Offline step',index:1,total:2}],log:'Offline log'};
  dp.renderJobDock();
  dock.querySelector('.job-log').open=true;
  dock.querySelector('.log-block').scrollTop=180;dock.querySelector('.log-block').scrollLeft=24;
  dock.querySelector('.job-events').scrollTop=90;
  dp.app.job.log+='\nNew line';dp.renderJobDock();
  assert(dock.querySelector('.job-log').open,'polling preserves expanded logs');
  assert.equal(dock.querySelector('.log-block').scrollTop,180,'reading older lines keeps position');
  assert.equal(dock.querySelector('.log-block').scrollLeft,24);
  assert.equal(dock.querySelector('.job-events').scrollTop,90);
  dock.querySelector('.log-block').scrollTop=400;dockEnv.document.fixtureHeight=600;dp.app.job.state='success';dp.renderJobDock();
  assert.equal(dock.querySelector('.log-block').scrollTop,500,'bottom follows growing logs through completion');
  dp.app.job.id='offline-b';dp.renderJobDock();assert(!dock.querySelector('.job-log').open,'new jobs start with independent log state');
  dp.app.job.state='unverified';dp.app.job.elapsed=null;dp.renderJobDock();
  assert(dock.textContent.includes('结果待核实'));assert(dock.textContent.includes('耗时未知'));
  assert(dock.querySelector('.badge-warn'),'unknown results use a warning rather than a success badge');
  dp.app.job.elapsed=60;dp.app.job.elapsed_source='terminal-event';dp.renderJobDock();
  assert(dock.textContent.includes('已用时 60 秒'));assert(dock.textContent.includes('按终止记录计算'));

  const overview = environment('gui/js/pages/overview.js');
  overview.context.host = overview.document.root;
  overview.context.fixture = {subscriptions: [{uid: 'offline', nodes: ['Node A', 'Node B', 'Node C']}], regions: {}};
  overview.expose('choicesHost=host;nodesState.data=fixture;Object.assign(form,{subscription:"offline",node:"Node A",regionManual:true,acknowledged:true});renderImpact=()=>{};globalThis.probe={form,renderChoices,statusGrid};');
  const picker = overview.context.probe, ctx = {state: {configured: true, plan: {node: 'Node A'}}, busy: false};
  const historical = {...state, verification: {...state.verification, time: new Date(now - 3600000).toISOString()}};
  const grid = picker.statusGrid({state:historical});
  assert(grid.textContent.includes('网络未验收'));
  assert(grid.querySelector('.status-dot').className.includes('warn'), 'overview also separates old acceptance from current configuration');
  const recoveryGrid=picker.statusGrid({state:{...historical,operation:{pending_tun:[{status:'failed'}]}}});
  assert(recoveryGrid.textContent.includes('TUN 演练待恢复'));
  assert(recoveryGrid.querySelector('.status-dot').className.includes('bad'));
  picker.renderChoices(ctx);
  let list = overview.document.root.querySelector('.node-list');
  assert.equal(list.getAttribute('role'), 'radiogroup');
  assert.equal(list.getAttribute('aria-label'), '选择精确节点');
  list.scrollTop = 44;
  const second = list.children[1];
  second.focus(); second.handlers.click();
  list = overview.document.root.querySelector('.node-list');
  assert.equal(picker.form.node, 'Node B'); assert.equal(picker.form.acknowledged, false);
  assert.equal(overview.document.activeElement, list.querySelector('[aria-checked="true"]'));
  assert.equal(list.scrollTop, 44);
  assert(!second.isConnected, 'test exercises actual form replacement');
  for (const [key, expected] of [['ArrowDown','Node C'],['ArrowDown','Node A'],['End','Node C'],['Home','Node A'],['ArrowLeft','Node C']]) {
    let prevented = false;
    overview.document.activeElement.handlers.keydown({key, preventDefault() { prevented = true; }});
    list = overview.document.root.querySelector('.node-list');
    assert(prevented); assert.equal(picker.form.node, expected);
    assert.equal(overview.document.activeElement, list.querySelector('[aria-checked="true"]'));
    assert.equal(list.children.filter(node => node.getAttribute('tabindex') === '0').length, 1);
  }
  overview.document.activeElement.handlers.keydown({key:'Tab', preventDefault() { throw Error('Tab must keep native behavior'); }});
  ctx.state.operation = {pending_rule_update:true}; picker.form.acknowledged=true; picker.renderChoices(ctx);
  assert(overview.document.getElementById('start-apply').disabled);

  const updates = environment('gui/js/pages/updates.js');
  updates.expose('globalThis.page=render;');
  const pending = {state: {configured:true, operation:{pending_rule_update:true},update:{mode:'scheduled'}}, busy:false};
  updates.document.root.append(updates.context.page(pending));
  assert(byText(updates.document.root,'立即更新').disabled);
  assert(byText(updates.document.root,'保存更新方式').disabled);
  assert(!byText(updates.document.root,'恢复上次更新').disabled);
  const labels = updates.document.root.querySelectorAll('label');
  for (const [tag, type, text] of [['input','time','更新时间'],['select',null,'更新间隔'],['input','number','间隔天数']]) {
    const control = updates.document.root.querySelectorAll(tag).find(node => !type || node.getAttribute('type') === type);
    assert(control, text + ' field must exist');
    assert(labels.some(label => label.querySelectorAll(tag).includes(control) && label.textContent.includes(text)), text + ' needs an associated label');
  }
  updates.expose('rerenderMode=()=>{};globalThis.renderUpdates=render;');
  updates.document.root.querySelector('select').handlers.change({target:{value:'custom'}});
  updates.document.root.replaceChildren(updates.context.renderUpdates(pending));
  assert.equal(updates.document.root.querySelector('select').value,'custom','custom remains selected even when days equal a preset');
  updates.document.root.querySelector('select').handlers.change({target:{value:'7'}});
  updates.document.root.replaceChildren(updates.context.renderUpdates(pending));
  assert.equal(updates.document.root.querySelector('select').value,'7');
  const dayControl=updates.document.root.querySelectorAll('input').find(node=>node.getAttribute('type')==='number');
  dayControl.handlers.input({target:{value:'9'}});
  assert.equal(updates.document.root.querySelector('select').value,'custom','editing days switches the selector to custom');

  const guide=environment('gui/js/pages/help.js');
  guide.context.configurationDetails=ctx=>[guide.context.ui.el('p',{text:ctx.state.plan.node})];
  guide.context.softwareVersion=ctx=>guide.context.ui.button(ctx.state.software.status,{disabled:ctx.state.software.checking});
  guide.expose('globalThis.refreshGuide=refreshStatus;');
  const details=guide.document.createElement('details');details.open=true;
  for(const id of ['help-configuration','help-software']){const region=guide.document.createElement('div');region.id=id;details.append(region);}
  guide.document.root.append(details);
  const guideCtx={state:{plan:{node:'Offline A'},software:{status:'unchecked'}}};
  guide.context.refreshGuide(guideCtx);
  guideCtx.state.plan.node='Offline B';guideCtx.state.software={status:'checking',checking:true};
  guide.context.refreshGuide(guideCtx);
  assert(guide.document.root.textContent.includes('Offline B'));
  assert(guide.document.root.querySelector('button').disabled);
  guideCtx.state.software={status:'current',checking:false};guide.context.refreshGuide(guideCtx);
  assert.equal(guide.document.root.querySelector('button').textContent,'current');
  assert(!guide.document.root.querySelector('button').disabled);
  const stable=guide.document.root.querySelector('button');guide.context.refreshGuide(guideCtx);
  assert.equal(guide.document.root.querySelector('button'),stable,'unchanged polling preserves software DOM');
  assert(details.open,'refresh leaves the outer disclosure open');
  const network = environment('gui/js/pages/network.js');
  network.expose('globalThis.probe={actionsCard,diagnosticsCard};');
  pending.state.baseline = 'first'; pending.state.plan = {node:'Node A'};
  network.document.root.append(network.context.probe.actionsCard(pending),network.context.probe.diagnosticsCard(pending));
  for (const text of ['补充配置','恢复并重新配置','检查连通性','完整复核与演练']) assert(byText(network.document.root,text).disabled, text);
  assert(!byText(network.document.root,'去规则更新').disabled);
  const snapshots = environment('gui/js/pages/snapshots.js');
  snapshots.expose('globalThis.page=render;');
  pending.state.snapshots = [];
  snapshots.document.root.append(snapshots.context.page(pending));
  assert(byText(snapshots.document.root,'保存当前快照').disabled);
  const software = environment('gui/js/pages/software.js');
  software.expose('globalThis.packageCard=packageCard;globalThis.refreshPackage=refreshControl;');
  software.document.root.append(software.context.packageCard(pending));
  assert(byText(software.document.root,'生成 ZIP 安装包').disabled);
  pending.state.operation.pending_rule_update=false;
  software.context.refreshPackage(pending);
  assert(!byText(software.document.root,'生成 ZIP 安装包').disabled, 'preserved help page refreshes the export state without replacing the page');
  for(const operation of [{settings_pending:[{id:'a'.repeat(32)}]}, {pending_tun:[{id:'b'.repeat(32),status:'failed'}]}]) {
    pending.state.operation=operation;
    const blocked=network.context.probe.actionsCard(pending);
    assert(byText(blocked,'补充配置').disabled);
    assert(byText(blocked,'恢复并重新配置').disabled);
    const snap=snapshots.context.page(pending);
    assert(byText(snap,'保存当前快照').disabled);
    if(operation.settings_pending)assert(!byText(snap,'恢复原设置').disabled,'settings recovery must not block itself');
    const rules=updates.context.renderUpdates(pending);
    assert(byText(rules,'保存更新方式').disabled);assert(byText(rules,'立即更新').disabled);
    software.context.refreshPackage(pending);assert(byText(software.document.root,'生成 ZIP 安装包').disabled);
    ctx.state.operation=operation;picker.form.acknowledged=true;picker.renderChoices(ctx);
    assert(overview.document.getElementById('start-apply').disabled);
  }
  pending.state.operation={settings_pending:[{id:'a'.repeat(32)}],pending_rule_update:true};
  assert(byText(snapshots.context.page(pending),'恢复原设置').disabled);
  assert(!byText(updates.context.renderUpdates(pending),'恢复上次更新').disabled,'rules recover before settings when both are pending');
  const logPage=environment('gui/js/pages/logs.js');logPage.expose('globalThis.probe={verificationCard,render};');
  for(const coverage of ['partial','legacy','connectivity','full-network']){
    const card=logPage.context.probe.verificationCard({state:{verification:{available:true,success:true,coverage,stages:[{id:'tun-off',title:'TUN',tested:false}]}}});
    assert.equal(Boolean(card.open),coverage!=='full-network');
    if(coverage!=='full-network')assert(card.textContent.includes('不代表完整网络验收'));
    assert(card.textContent.includes('未测试'));
  }
  const recoveryLog=logPage.context.probe.render({state:{operation:{pending_tun:[{id:'b'.repeat(32),status:'failed'}]}}});
  assert(recoveryLog.textContent.includes('已报告恢复失败'));
  assert(recoveryLog.textContent.includes('.local/tun-drills/'+'b'.repeat(32)));
  console.log('Offline UI checks passed: API evidence, historical results, global recovery, blocked actions, node focus/radio keys and associated labels');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
