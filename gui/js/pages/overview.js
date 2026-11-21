import { icon } from '../icons.js';
import * as ui from '../ui.js';

const IMPACT = [
  { title: 'Clash 客户端', text: '为所选订阅关联独立的项目增强脚本与固定节点组；保留原自定义脚本和现有节点。' },
  { title: '严格出站防火墙', text: '保留四条项目放行并阻断 SharedAccess 服务出站。Mihomo 停止时国内公网也会断开。' },
  { title: '系统 IPv6', text: '全局关闭并关闭现有网卡的 IPv6 绑定；依赖 IPv6 的内网资源可能不可用，需要重启 Windows 才完全生效。' },
  { title: '系统自动定位', text: '通过 Windows 策略关闭系统定位，地图、天气等定位功能会受影响；恢复时还原原有策略。' },
  { title: '区域与时区', text: '关闭自动时区和自动校时，按所选节点地区设置系统国家、Windows 语言和区域页面中的区域格式（日期和数字）及时区。区域格式可能需注销后重新登录或重启生效；恢复会还原原格式和时间设置。长期关闭校时可能产生误差。' },
  { title: '代理清理', text: '清理系统、WinHTTP、环境变量与 Git/npm/Docker 的代理覆盖，普通流量交给 TUN；已打开的终端需要重启。' },
  { title: '常用浏览器 WebRTC', text: '备份 Chrome、Edge、Firefox 的受管策略；Firefox 锁定禁用，Chrome/Edge 限制路径并自动安装官方商店的 WebRTC Control。已有扩展直接复用，项目自带离线扩展保留备用。外部指纹浏览器自行管理。' },
];

const REGIONS = [
  ['SG', '新加坡 SG'], ['US', '美国 US'], ['JP', '日本 JP'], ['GB', '英国 GB'],
  ['DE', '德国 DE'], ['FR', '法国 FR'], ['NL', '荷兰 NL'], ['CA', '加拿大 CA'],
  ['AU', '澳大利亚 AU'], ['KR', '韩国 KR'], ['HK', '中国香港 HK'], ['TW', '中国台湾 TW'],
  ['IN', '印度 IN'], ['TH', '泰国 TH'], ['VN', '越南 VN'], ['MY', '马来西亚 MY'],
  ['ID', '印度尼西亚 ID'], ['PH', '菲律宾 PH'], ['NZ', '新西兰 NZ'], ['BR', '巴西 BR'],
  ['AR', '阿根廷 AR'], ['MX', '墨西哥 MX'], ['CL', '智利 CL'], ['ZA', '南非 ZA'],
  ['AE', '阿联酋 AE'], ['SA', '沙特 SA'], ['IL', '以色列 IL'], ['TR', '土耳其 TR'],
  ['RU', '俄罗斯 RU'], ['UA', '乌克兰 UA'], ['PL', '波兰 PL'], ['IT', '意大利 IT'],
  ['ES', '西班牙 ES'], ['PT', '葡萄牙 PT'], ['IE', '爱尔兰 IE'], ['SE', '瑞典 SE'],
  ['CH', '瑞士 CH'], ['FI', '芬兰 FI'], ['NO', '挪威 NO'], ['DK', '丹麦 DK'],
  ['AT', '奥地利 AT'], ['BE', '比利时 BE'], ['CZ', '捷克 CZ'], ['HU', '匈牙利 HU'],
  ['RO', '罗马尼亚 RO'], ['GR', '希腊 GR'], ['RS', '塞尔维亚 RS'], ['KZ', '哈萨克斯坦 KZ'],
  ['EG', '埃及 EG'], ['NG', '尼日利亚 NG'], ['KE', '肯尼亚 KE'], ['MA', '摩洛哥 MA'],
  ['MO', '中国澳门 MO'],
];

const form = {
  mode: '',
  frontSubscription: '',
  frontNode: '',
  frontGroup: false,
  chainName: '',
  staticProtocol: 'socks5',
  staticIp: '',
  staticPort: '',
  staticUser: '',
  staticPassword: '',
  staticSaved: '',
  node: '',
  subscription: '',
  region: '',
  search: '',
  frontSearch: '',
  exitSearch: '',
  acknowledged: false,
  initialized: false,
  regionAuto: null,
  regionManual: false,
};
const modeDrafts = { single: null, chain: null, static: null, staticDirect: null };
const draftFields = ['node','subscription','region','regionAuto','regionManual','search',
  'frontSubscription','frontNode','frontGroup','chainName','frontSearch','exitSearch',
  'staticProtocol','staticIp','staticPort','staticUser','staticPassword','staticSaved'];
const captureDraft = () => Object.fromEntries(draftFields.map(key => [key,form[key]]));
const emptyDraft = captureDraft();

let nodesState = { loading: false, error: '', data: null, selectionNotice: '' };
let rootNode = null;
let choicesHost = null;
let listHost = null;
let impactHost = null;

function operationsBlocked(ctx, settingsRecovery = false) { return ctx.busy || Boolean(ctx.state.operation?.pending_rule_update) || Boolean(ctx.state.operation?.pending_tun?.length) || (!settingsRecovery && Boolean(ctx.state.operation?.settings_pending?.length)); }

export function render(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  if (!form.initialized) {
    if (plan.node) {
      const lastMode = !plan.chain ? 'single' : plan.chain.direct ? 'staticDirect' :
        plan.chain.static_exit ? 'static' : 'chain';
      if (plan.chain) loadChainSelection(ctx,plan.chain,plan.subscription);
      else Object.assign(form,{subscription:plan.subscription || '',node:plan.node});
      form.region = plan.region || '';
      form.regionManual = Boolean(form.region);
      modeDrafts[lastMode] = captureDraft();
      Object.assign(form,emptyDraft);
    }
    form.initialized = true;
  }

  rootNode = document.createElement('div');
  rootNode.className = 'stack setup-page';
  rootNode.append(ui.pageHeader({
    title: state.configured ? '网络配置' : '一键配置',
    actions: [
      ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: ctx.refreshManually }),
    ],
  }));
  rootNode.append(statusGrid(ctx));
  impactHost = document.createElement('div');
  rootNode.append(setupCard(ctx));
  rootNode.append(impactHost);
  renderImpact(ctx);
  return rootNode;
}

export function refreshStatus(ctx) {
  if (!rootNode?.isConnected) return;
  rootNode.querySelector('.tiles')?.replaceWith(statusGrid(ctx));
  renderImpact(ctx);
  updateStartButton(ctx);
  const reapply = rootNode.querySelector('#quick-reapply');
  if (reapply) reapply.disabled = operationsBlocked(ctx) || !ctx.state.plan?.node;
}

function statusGrid(ctx) {
  const state = ctx.state;
  const verification = state.verification || {};
  const awaitingVerification = state.last_run?.network_tested === false &&
    (!verification.available || (Date.parse(verification.time) || 0) < (ui.configurationTime(state) || Infinity));
  const verificationAge = (Date.now() - Date.parse(verification.time)) / 3600000;
  const configurationAt = ui.configurationTime(state);
  const verifiedFresh = verification.available && verification.success && !awaitingVerification &&
    (Number.isFinite(configurationAt) ? Date.parse(verification.time) >= configurationAt : verification.configuration_matches_current === true) &&
    ui.verificationMatches(state) && verificationAge >= 0 && verificationAge <= 24;
  const update = state.update || {};
  const recovery = ui.recoveryLabel(state.operation);
  const scopedPass = ['full-network', 'connectivity'].includes(verification.coverage);
  const tiles = [
    {
      label: '网络配置',
      value: recovery || (!state.configured ? '未配置' : state.last_run?.success === false ? '上次操作未完成' : '已配置'),
      tone: recovery ? 'bad' : !state.configured ? 'warn' : state.last_run?.success === false ? 'bad' : awaitingVerification ? 'warn' : verification.dns_failed ? 'bad' : verifiedFresh && scopedPass ? 'ok' : 'warn',
      detail: state.configured ? ('节点 ' + (state.plan.node || '—') + (awaitingVerification ? ' · 网络未验收' : verification.available ? ' · 上次验收 ' + ui.relativeTime(verification.time) : ' · 尚未验收')) : '首次配置会先保存原始快照',
      action: state.configured ? () => ctx.navigate('network') : null,
    },
    {
      label: '规则更新',
      value: update.mode === 'scheduled' ? ((Number(update.days || 1) === 1 ? '每天 ' : '每 ' + update.days + ' 天 ') + (update.time || '13:00')) : '手动',
      tone: update.last?.success ? 'ok' : update.last?.time ? 'warn' : 'neutral',
      detail: update.last?.time ? ('上次 ' + ui.relativeTime(update.last.time)) : '尚未更新',
      action: () => ctx.navigate('updates'),
    },
    {
      label: '上次验收',
      value: verification.available ? ui.verificationLabel(verification) : '待检查',
      tone: ui.verificationTone(verification),
      detail: verification.available ? ui.relativeTime(verification.time) + ' · ' + ui.verificationRelation(state) : '在网络环境按需点击检查',
      action: () => ctx.navigate('logs'),
    },
  ];
  return ui.el('div', { class: 'tiles' }, tiles.map((tile) => ui.el('button', {
    class: 'tile', type: 'button', onclick: tile.action || undefined,
  },
  ui.el('span', { class: 'tile-label' }, tile.label, ui.el('span', { class: 'status-dot ' + tile.tone })),
  ui.el('strong', { class: 'tile-value', text: tile.value }),
  ui.el('span', { class: 'tile-detail', text: tile.detail }))));
}

function setupCard(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  const configured = Boolean(state.configured);
  choicesHost = document.createElement('div');
  choicesHost.className = 'choice-area';

  const headerActions = configured
    ? ui.button('按当前记录重新配置', {
        variant: 'secondary',
        icon: 'refresh',
        disabled: operationsBlocked(ctx) || !plan.node,
        onClick: () => quickReapply(ctx),
      })
    : null;
  if (headerActions) headerActions.id = 'quick-reapply';

  const currentBand = configured ? ui.el('div', { class: 'current-band' },
    ui.el('div', {}, ui.el('strong', { text: plan.node || '—' })),
    plan.chain ? ui.el('p',{class:'muted small',text:proxyRoute(plan.chain)+' · 完整防护状态请查看网络环境。'}) : null,
    ui.el('div', { class: 'current-meta' },
      ui.pill('订阅 ' + (plan.subscription ? plan.subscription.slice(0, 8) : '—'), 'neutral'),
      ui.pill('出口 ' + (plan.region || 'SG'), 'neutral'),
      ui.helpTip('恢复点：' + (plan.snapshot || '尚未记录'), '当前恢复点'))) : null;

  const modes = ui.field('配置方式', ui.select([
    {value:'',label:'请选择配置方式'},
    {value:'single',label:'单一节点配置'},
    {value:'chain',label:'链式代理配置'},
    {value:'static',label:'静态 IP 出口链式代理'},
    {value:'staticDirect',label:'单一静态 IP 配置'},
  ], form.mode, value => switchMode(ctx,value), {id:'configuration-mode'}),
  '四种方式可以同时保留。在 Clash Verge 的「订阅」页启用要使用的配置。');
  const card = ui.card({
    title: configured ? '更换配置或重新配置' : '连接配置',
    help: '每份配置固定一个海外出口。配置会请求管理员权限。',
    actions: headerActions,
  }, currentBand, modes, choicesHost);

  renderChoices(ctx);
  if (form.mode) loadChoices(ctx);
  return card;
}

function renderImpact(ctx) {
  if (!impactHost) return;
  impactHost.hidden = !form.mode;
  if (!form.mode) return;
  const preview = ctx.state.preview || {};
  const now = preview.current || {};
  const target = preview.regions?.[form.region] || {};
  const selected = form.mode === 'single' ? Boolean(form.node && (currentSubscription(ctx)?.nodes || []).includes(form.node)) : chainReady();
  const unknown = '未能确认';
  const pending = '选择节点后确定';
  const countryName = (code) => {
    try { return code ? new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(code) : unknown; }
    catch { return unknown; }
  };
  const languageName = (code) => {
    try { return code ? new Intl.DisplayNames(['zh-CN'], { type: 'language' }).of(code.split(',')[0]) : unknown; }
    catch { return unknown; }
  };
  const zoneName = (zone) => {
    try { return new Intl.DateTimeFormat('zh-CN', { timeZone: zone, timeZoneName: 'long' }).formatToParts(new Date()).find((p) => p.type === 'timeZoneName')?.value || unknown; }
    catch { return unknown; }
  };
  const proxyState = (value) => {
    if (!value || /读取失败|未能读取|未验证/.test(value)) return unknown;
    return /已设置|已配置静态/.test(value) ? '开启' : '关闭';
  };
  const after = (value) => selected ? value : pending;
  const rows = [
    ['系统定位', now.location_state, '关闭'],
    ['自动设置时区', now.auto_timezone_state, '关闭'],
    ['自动校准时间', now.auto_time_state, '关闭'],
    ['国家或地区', countryName(now.country_code), after(target.label)],
    ['区域格式（日期和数字）', languageName(now.culture), after(languageName(target.culture))],
    ['时区', now.timezone === 'China Standard Time' ? '中国标准时间' : zoneName(Object.values(preview.regions || {}).find((r) => r.timezone_id === now.timezone)?.iana), after(zoneName(target.iana))],
    ['IPv6 网络', now.ipv6_state, '关闭'],
    ['严格联网保护', now.firewall_state, '开启'],
    ['Windows 手动代理', now.proxy_state, '关闭'],
    ['Windows 后台服务代理', proxyState(now.winhttp), '关闭'],
    ['环境变量代理', proxyState(now.environment), '关闭'],
    ['Git 代理', proxyState(now.git), '关闭'],
    ['npm 代理', proxyState(now.npm), '关闭'],
    ['Docker 代理', proxyState(now.docker), '关闭'],
    ['网站连接方式', now.routing_simple, '按规则分流'],
    ['国外连接节点', now.fixed_node || unknown, after(form.mode === 'single' ? form.node : form.chainName)],
    ['DNS 解析', now.dns_simple, '开启'],
    ['DNS 额外过滤', now.dns_filter_simple, '开启'],
    ['接管应用网络', now.tun_simple, '开启'],
    ['防止绕过代理', now.strict_route_simple, '开启'],
  ];
  let readTime = '未读取';
  if (preview.read_at) readTime = new Date(preview.read_at).toLocaleTimeString('zh-CN', { hour12: false });
  const valueBadge = (value) => {
    const text = String(value || unknown);
    const tone = text === '开启' ? 'on' : text === '关闭' ? 'off'
      : /未能确认|尚未|未发现|未完整|部分/.test(text) ? 'unknown'
      : text === pending ? 'pending' : 'text';
    return ui.el('span', { class: 'config-value config-value-' + tone, text });
  };
  const groups = [
    ['系统与地区', rows.slice(0, 7)],
    ['网络保护', [...rows.slice(7, 9),
      ['DNS 防泄露', now.dns_protection, '开启', '防止域名查询绕过代理'],
      ...(now.webrtc?.browsers || ['Chrome', 'Edge', 'Firefox'].map(browser => ({ browser }))).flatMap(row => [
        [row.browser + ' WebRTC 策略', row.policy_configured === true ? '已配置' : row.policy_configured === false ? '未完整配置' : unknown,
          '已配置', row.browser === 'Firefox' ? '锁定关闭 WebRTC' : '限制非代理 UDP'],
        ...(row.browser === 'Firefox' ? [] : [[row.browser + ' WebRTC 扩展',
          row.extension_requested || row.installation?.state === 'installed' ? '已配置' : '待配置自动安装',
          '已配置', '静默安装，已有扩展复用。安装记录：' + (row.installation?.label || '尚未读取')]]),
        [row.browser + ' WebRTC 配置', row.configuration_state || '正在读取', '已配置'],
      ])], 'Chrome/Edge 使用策略与扩展两层保护，配置时自动安排扩展安装；Firefox 使用原生禁用策略。'],
    ['代理设置', rows.slice(9, 15), '关闭之前分散在各处的代理设置，统一由 TUN 模式接管网络。'],
    ['网站访问', rows.slice(15)],
  ];
  impactHost.replaceChildren(ui.disclosure({ title: '配置变更详情', key: 'setup-impact',
    class: 'config-preview-card' },
    ui.el('p', { class: 'muted small', text: '读取于 ' + readTime + ' · 地区识别仅依据节点名称，实际出口未验证。' }),
    ui.el('div', { class: 'config-legend', 'aria-label': '状态颜色说明' },
      valueBadge('开启'), valueBadge('关闭'), valueBadge('未能确认')),
    ui.el('table', { class: 'config-table', 'aria-label': '当前状态与配置后的对照' },
      ui.el('thead', {}, ui.el('tr', {},
        ui.el('th', { scope: 'col', text: '配置项目' }),
        ui.el('th', { scope: 'col', text: '当前状态' }),
        ui.el('th', { scope: 'col', class: 'config-target-heading', text: '配置后' }))),
      groups.map(([title, items, description]) => ui.el('tbody', {},
        ui.el('tr', { class: 'config-group' }, ui.el('th', { colSpan: 3, scope: 'rowgroup' },
          ui.el('span', { text: title }), description ? ui.helpTip(description, title + '说明') : null)),
        items.map(([label, current, desired, description]) => ui.el('tr', { class: 'config-item' },
          ui.el('th', { scope: 'row' }, ui.el('span', { class: 'heading-with-help' }, ui.el('span', { text: label }), description ? ui.helpTip(description, label + '说明') : null)),
          ui.el('td', { 'data-label': '当前' }, valueBadge(current)),
          ui.el('td', { 'data-label': '配置后', class: 'config-target' }, valueBadge(desired))))))),
    ui.el('p', { class: 'muted small', text: '外部指纹浏览器由用户自行管理。' })));
}

async function loadChoices(ctx) {
  if (!choicesHost) return;
  if (nodesState.loading) return;
  nodesState.loading = true;
  renderChoices(ctx);
  try {
    const data = await ctx.loadNodes(true);
    const firstLoad = !nodesState.data;
    nodesState.data = data;
    nodesState.error = '';
    const previousSubscription=form.subscription;
    const rows = (data.subscriptions || []).filter(row => !row.chain);
    for (const draft of Object.values(modeDrafts)) {
      if (draft) reconcileSubscriptions(draft, rows);
    }
    nodesState.selectionNotice = reconcileSubscriptions(form, rows) ? '原订阅已不可用，请重新选择订阅和节点。' : '';
    const hasNode = (uid, node) => rows.some(row => row.uid === uid && row.nodes.includes(node));
    if ((form.node && !hasNode(form.subscription, form.node)) ||
        (form.mode !== 'single' && form.frontNode && !hasFrontChoice(rows))) {
      form.acknowledged = false;
    }
    if (form.mode==='single') {
      const planned=rows.find(row=>row.uid===data.plan_subscription);
      if (!form.subscription) form.subscription=planned?.uid || rows.find(row=>row.uid===data.active_uid)?.uid || rows[0]?.uid || '';
      if (!previousSubscription && !form.node && planned && planned.uid===form.subscription && planned.nodes.includes(data.plan_node)) {
        form.node=data.plan_node;
        if (data.plan_region) { form.region=data.plan_region;form.regionManual=true; }
      }
    }
    if (firstLoad && form.mode === 'chain' && !form.chainName && data.chain_defaults) loadChainSelection(ctx,data.chain_defaults);
  } catch (error) {
    nodesState.error = error.message || '读取订阅失败。';
  } finally {
    nodesState.loading = false;
    renderChoices(ctx);
  }
}

function reconcileSubscriptions(draft, rows) {
  let changed = false;
  for (const [subscription, node] of [['subscription','node'], ['frontSubscription','frontNode']]) {
    if (draft[subscription] && !rows.some(row => row.uid === draft[subscription])) {
      draft[subscription]='';
      draft[node]='';
      draft.acknowledged=false;
      changed=true;
    }
  }
  if (!draft.subscription) {
    draft.node='';
    draft.regionAuto=null;
    if (!draft.regionManual) draft.region='';
  }
  return changed;
}

function hasFrontChoice(rows) {
  const row = rows.find(r => r.uid === form.frontSubscription);
  return (form.frontGroup ? row?.front_groups : row?.nodes)?.includes(form.frontNode);
}
function regionOptions() {
  return [{value:'',label:'请选择出口地区'}, ...REGIONS.map(([value,label])=>({value,label}))];
}

function currentSubscription(ctx) {
  const subscriptions = (nodesState.data?.subscriptions || []).filter(row=>!row.chain);
  return subscriptions.find((row) => row.uid === form.subscription) || subscriptions[0] || null;
}

function loadChainSelection(ctx, saved, uid) {
  form.subscription=saved.exit_subscription || '';form.node=saved.exit_node || '';
  form.frontSubscription=saved.front_subscription || '';form.frontNode=saved.front_node || '';form.chainName=saved.name;
  form.frontGroup=saved.front_group === true;
  form.staticSaved=saved.static_exit ? uid : '';
  form.regionManual=false;form.regionAuto=null;form.acknowledged=false;
  if (saved.static_exit) {
    form.region = uid === nodesState.data?.plan_subscription ? nodesState.data?.plan_region || '' : '';
  } else applyRegionHint(ctx,form.node);
}

function switchMode(ctx, value) {
  if (value===form.mode) return;
  if (form.mode) modeDrafts[form.mode]=captureDraft();
  form.mode=value;
  form.acknowledged=false;
  nodesState.selectionNotice='';
  Object.assign(form,modeDrafts[value] || emptyDraft);
  if (!modeDrafts[value] && value === 'chain' && nodesState.data?.chain_defaults) {
    loadChainSelection(ctx,nodesState.data.chain_defaults);
  }
  if (value && !nodesState.data && !nodesState.loading) loadChoices(ctx);
  else renderChoices(ctx);
}

function renderChoices(ctx) {
  if (!choicesHost) return;
  if (!form.mode) {
    choicesHost.replaceChildren();
    renderImpact(ctx);
    return;
  }
  if (nodesState.loading) {
    choicesHost.replaceChildren(ui.el('div', { class:'loading-inline', role:'status' },
      ui.el('span', { class:'spinner' }), '正在读取 Clash 配置…'));
    return;
  }
  if (nodesState.error) {
    choicesHost.replaceChildren(ui.note(nodesState.error,'bad'),reloadButton(ctx));
    return;
  }
  const data = nodesState.data;
  const subscriptions = (data?.subscriptions || []).filter(row=>!row.chain);
  if (reconcileSubscriptions(form, subscriptions)) nodesState.selectionNotice='原订阅已不可用，请重新选择订阅和节点。';
  if (form.mode!=='single') {
    if (!supportsMode(ctx)) {
      choicesHost.replaceChildren(ui.note('软件后台仍是旧版本。请关闭所有管理窗口后，重新运行「一键网络.cmd」。','warn'));
      return;
    }
    renderChainChoices(ctx);return;
  }
  if (form.node && !form.regionAuto && !form.regionManual) applyRegionHint(ctx, form.node);
  if (!subscriptions.length) {
    choicesHost.replaceChildren(ui.note('当前没有可读取的远程订阅。可以使用单一静态 IP，或在 Clash Verge 导入订阅。', 'warn'), reloadButton(ctx));
    return;
  }
  const current = currentSubscription(ctx);
  if (!current) return;
  if (!form.subscription || !subscriptions.some((row) => row.uid === form.subscription)) {
    form.subscription = current.uid;
  }
  const nodeNames = current.nodes || [];
  const selectedInside = form.node && nodeNames.includes(form.node);
  const planNodeMissing = data?.plan_node && !(data.subscriptions || []).some((row) => row.nodes.includes(data.plan_node));
  const count = ui.el('span', {class: 'muted', 'aria-live': 'polite'});
  const subSelect = ui.select(
    subscriptions.map((row) => ({ value: row.uid, label: row.name + '（' + row.nodes.length + ' 个节点）' })),
    form.subscription,
    (value) => {
      form.subscription = value;
      nodesState.selectionNotice = '';
      if (!(currentSubscription(ctx)?.nodes || []).includes(form.node)) form.node = '';
      form.acknowledged = false;
      applyRegionHint(ctx, form.node);
      renderChoices(ctx);
    },
    { 'aria-label': '选择订阅' },
  );

  const search = ui.input(form.search, {
    type: 'search',
    placeholder: '搜索节点名称…',
    'aria-label': '搜索节点',
    oninput: (event) => { form.search = event.target.value; renderNodeList(ctx, count); },
  });

  listHost = document.createElement('div');
  listHost.className = 'node-list';
  listHost.setAttribute('role', 'radiogroup');
  listHost.setAttribute('aria-label', '选择精确节点');
  const columns = ui.el('div', { class: 'choice-grid' },
    ui.field('订阅', subSelect),
    ui.field('出口地区', ui.select(regionOptions(), form.region, (value) => {
      form.region = value;
      form.regionAuto = null;
      form.regionManual = Boolean(value);
      form.acknowledged = false;
      renderChoices(ctx);
    }), '从节点名自动填入地区提示，可随时手动修改。完整网络验收会核对实际出口。'),
  );

  const selected = ui.el('div', { class: 'selected-node' },
    ui.el('span', { class: 'muted small', text: '已选节点' }),
    ui.el('strong', { text: form.node || '尚未选择节点' }),
    form.node ? ui.pill(form.region ? '出口 ' + form.region : '出口地区待选', 'neutral') : null,
    form.regionAuto ? ui.pill('自动识别 ' + form.regionAuto.code, 'ok') : form.regionManual ? ui.pill('手动选择', 'neutral') : null);

  const ack = ui.checkbox(form.acknowledged, '我已确认所选节点，并了解配置会修改网络与系统设置。', (value) => {
    form.acknowledged = value;
    updateStartButton(ctx);
  });

  const start = ui.button(stateText(ctx), {
    variant: 'ink',
    icon: 'play',
    disabled: !form.node || !form.acknowledged || operationsBlocked(ctx),
    onClick: () => beginApply(ctx),
  });
  start.id = 'start-apply';

  const toolbar = ui.el('div', { class: 'choice-toolbar' }, search, count, reloadButton(ctx));
  const hint = planNodeMissing
    ? ui.note('上次使用的节点已不在当前订阅中。请重新选择；本项目不会自动替换节点。', 'warn')
    : null;

  const missingHint = form.node && !selectedInside
    ? ui.note('所选节点不在当前订阅中。请从上方列表重新选择；不会自动替换。', 'warn')
    : null;
  const regionHint = form.node && !form.regionAuto && !form.regionManual
    ? ui.note('未识别出口地区，请手动选择。', 'warn') : null;
  choicesHost.replaceChildren(...[
    columns,
    nodesState.selectionNotice ? ui.note(nodesState.selectionNotice, 'warn') : null,
    hint,
    toolbar,
    listHost,
    selected,
    regionHint,
    missingHint,
    ui.el('div', { class: 'ack-row' }, ack),
    ui.el('div', { class: 'choice-foot' },
      ui.helpTip('配置需要管理员权限，期间可能短暂中断网络。详细改动可展开下方对照表查看，执行前会再次确认。', '配置影响'),
      start),
  ].filter(Boolean));
  renderNodeList(ctx, count);
  renderImpact(ctx);
  updateStartButton(ctx);
}

function reloadButton(ctx) {
  return ui.button('重新读取订阅', {variant: 'secondary', size: 'small', icon: 'refresh', onClick: () => loadChoices(ctx)});
}

function matchNodes(nodes, search) {
  const query = search.trim().toLowerCase();
  return query ? nodes.filter(name => name.toLowerCase().includes(query)) : nodes;
}

function renderNodeList(ctx, count) {
  if (!listHost) return;
  const current = currentSubscription(ctx);
  const all = current?.nodes || [];
  const query = form.search.trim();
  const names = matchNodes(all, query);
  count.textContent = names.length + ' / ' + all.length + ' 个节点';
  if (!names.length) {
    listHost.replaceChildren(ui.el('p', { class: 'muted pad', text: query ? '没有匹配的节点。' : '这个订阅没有可直接选择的节点。' }));
    return;
  }
  const visible = names.slice(0, 800);
  const tabNode = visible.includes(form.node) ? form.node : visible[0];
  listHost.replaceChildren(...visible.map((name, index) => ui.el('button', {
    class: 'node-row' + (name === form.node ? ' selected' : ''),
    type: 'button', role: 'radio', 'aria-checked': String(name === form.node),
    tabindex: name === tabNode ? 0 : -1,
    title: name,
    onclick: () => selectNode(ctx, name),
    onkeydown: (event) => {
      let next;
      if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % visible.length;
      else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index + visible.length - 1) % visible.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = visible.length - 1;
      else return;
      event.preventDefault();
      selectNode(ctx, visible[next]);
    },
  }, ui.el('span', { class: 'node-name', text: name }), name === form.node ? icon('check', 16) : icon('chevron', 14))));
  if (names.length > 800) {
    listHost.append(ui.el('p', { class: 'muted pad', text: '仅显示前 800 个节点，请用搜索缩小范围。' }));
  }
}

function selectNode(ctx, name) {
  nodesState.selectionNotice = '';
  const scroll = listHost.scrollTop;
  form.node = name;
  form.acknowledged = false;
  applyRegionHint(ctx, name);
  renderChoices(ctx);
  listHost.scrollTop = scroll;
  const selected = listHost.querySelector('[aria-checked="true"]');
  selected?.focus({preventScroll: true});
  selected?.scrollIntoView({block: 'nearest'});
}

function applyRegionHint(ctx, name) {
  if (form.regionManual) return;
  const hint = name ? nodesState.data?.regions?.[form.subscription]?.[name] : null;
  form.regionAuto = hint?.code ? hint : null;
  form.region = form.regionAuto?.code || '';
}

function updateStartButton(ctx) {
  const button = document.getElementById('start-apply');
  if (!button) return;
  const current = currentSubscription(ctx);
  const inside = Boolean(current && (current.nodes || []).includes(form.node));
  const ready = form.mode === 'single' ? Boolean(form.node && inside) : chainReady();
  button.disabled = !form.region || !ready || !form.acknowledged || operationsBlocked(ctx);
  button.title = form.mode === 'single' && form.node && !inside ? '所选节点不在当前订阅中，请重新选择。' : '';
}

function stateText(ctx) {
  if (isStaticMode()) return '配置此静态 IP 出口';
  return form.mode === 'chain' ? '配置此链式代理' : '配置此单一节点';
}

function isStaticMode() {
  return form.mode === 'static' || form.mode === 'staticDirect';
}

function supportsMode(ctx) {
  if (form.mode === 'staticDirect') return ctx.state.capabilities?.static_direct;
  return form.mode === 'static' ? ctx.state.capabilities?.static_exit : ctx.state.capabilities?.managed_chain;
}

function proxyRoute(selection) {
  const exit = selection.static_exit?.ip || selection.exit_node;
  return ['电脑', selection.front_node, exit, '目标网站'].filter(Boolean).join(' → ');
}

function savedStatic() {
  return nodesState.data?.subscriptions?.find(row => row.uid === form.staticSaved && row.chain?.static_exit &&
    Boolean(row.chain.direct) === (form.mode === 'staticDirect'));
}

function staticEndpoint() {
  return {protocol:form.staticProtocol,ip:form.staticIp.trim(),port:Number(form.staticPort),
    username:form.staticUser,password:form.staticPassword};
}

async function beginApply(ctx) {
  if (!form.mode || operationsBlocked(ctx)) return;
  if (form.mode !== 'single' && !supportsMode(ctx)) {
    ui.toast('请先退出并重新打开软件，加载链式代理功能。','warn');return;
  }
  if (!form.region) return;
  if (!form.acknowledged) {
    ui.toast('请先勾选确认。', 'warn');
    return;
  }
  const staticMode = isStaticMode();
  const saved = staticMode ? savedStatic() : null;
  const region = form.region;
  let node = form.node;
  let subscription = form.subscription;
  let chain = null;
  if (form.mode === 'single') {
    const selected = currentSubscription(ctx);
    if (selected?.uid !== subscription || !selected.nodes.includes(node)) return;
  } else {
    if (!chainReady()) return;
    chain = {name:form.chainName.trim()};
    const direct = form.mode === 'staticDirect';
    if (direct) chain.direct = true;
    else {
      Object.assign(chain,{front_subscription:form.frontSubscription,front_node:form.frontNode});
      if (form.frontGroup) chain.front_group = true;
    }
    if (staticMode) {
      if (saved) [subscription,node] = [saved.uid,saved.chain.name];
      else if (direct) [subscription,node] = ['',chain.name];
      else [subscription,node] = [form.frontSubscription,form.frontNode];
      chain.static_exit = saved ? saved.chain.static_exit : staticEndpoint();
    } else Object.assign(chain,{exit_subscription:subscription,exit_node:node});
  }
  const ok = await ctx.confirm({
    title: '开始配置网络？',
    subtitle: (chain ? chain.name+' · '+proxyRoute(chain) : node) + ' · 最终出口 ' + region,
    description: '配置会重启 Clash 并核对本机配置。完成后可在「网络环境」运行完整网络验收。',
    items: IMPACT,
    note: '重启 Mihomo 时公网会短暂断开，请先保存正在进行的网络工作。',
    noteType: 'warn',
    confirmText: '确认配置',
    requireCheck: '我已保存工作，并了解配置会修改系统设置和短暂断网。',
  });
  if (!ok) return;
  ctx.startJob({
    action: 'apply',
    node,
    subscription,
    region,
    ...(chain && !saved ? {chain} : {}),
    confirm: 'APPLY',
  });
}

function chainReady() {
  const rows=(nodesState.data?.subscriptions || []).filter(r=>!r.chain);
  if (!form.chainName.trim()) return false;
  if (form.mode !== 'staticDirect' && !hasFrontChoice(rows)) return false;
  if (isStaticMode()) {
    if (form.staticSaved) return Boolean(savedStatic());
    const endpoint = staticEndpoint();
    const octets = endpoint.ip.split('.');
    return octets.length === 4 && octets.every(octet => /^(0|[1-9]\d{0,2})$/.test(octet) && Number(octet) < 256) &&
      /^[0-9]{1,5}$/.test(form.staticPort) && endpoint.port >= 1 && endpoint.port <= 65535 &&
      Boolean(endpoint.username) === Boolean(endpoint.password);
  }
  return rows.some(r=>r.uid===form.subscription && r.nodes.includes(form.node)) &&
    !(form.subscription===form.frontSubscription && form.node===form.frontNode);
}

function renderChainChoices(ctx) {
  const staticMode = isStaticMode();
  const direct = form.mode === 'staticDirect';
  const rows=(nodesState.data?.subscriptions || []).filter(r=>!r.chain);
  const getSub=uid=>rows.find(r=>r.uid===uid);
  const subOptions=[{value:'',label:'请选择原订阅'},...rows.map(r=>({value:r.uid,label:r.name}))];
  const picker=(label,side)=>{
    const front=side==='front';const uid=front?form.frontSubscription:form.subscription;
    const node=front?form.frontNode:form.node;
    const searchKey=front?'frontSearch':'exitSearch';
    const row=getSub(uid);
    const groups=front ? row?.front_groups || [] : [];
    const all=[...(row?.nodes || []),...groups];
    const available=front && node ? hasFrontChoice(rows) : all.includes(node);
    const count=ui.el('span',{class:'muted small','aria-live':'polite'});
    const control=ui.select([],node,value=>{
      if(front){form.frontNode=value;form.frontGroup=groups.includes(value);}else{form.node=value;applyRegionHint(ctx,value);}
      form.acknowledged=false;renderChoices(ctx);
    }, {'aria-label':label+'节点'});
    function updateChoices() {
      const results=matchNodes(all,form[searchKey]);
      count.textContent=results.length+' / '+all.length+' 项';
      const names=results.slice(0,800);
      if(all.includes(node) && !names.includes(node)) names.unshift(node);
      control.replaceChildren(
        ui.el('option',{value:'',text:!uid?'先选择订阅':results.length?(groups.length?'请选择节点或自动组':'请选择精确节点'):'没有匹配项'}),
        ...names.map(name=>ui.el('option',{value:name,text:groups.includes(name)?name+'（自动组）':name})));
      control.value=available?node:'';
      control.disabled=!names.length;
      if(results.length>800)count.textContent+=' · 请进一步缩小搜索范围';
    }
    const search=ui.input(form[searchKey],{type:'search',placeholder:'搜索节点名称…',
      'aria-label':'搜索'+label+'节点',disabled:!all.length,
      oninput:event=>{form[searchKey]=event.target.value;updateChoices();}});
    updateChoices();
    return ui.el('div',{class:'choice-grid'},
      ui.field(label+'订阅',ui.select(subOptions,uid,value=>{
        if(front){form.frontSubscription=value;form.frontNode='';form.frontGroup=false;}else{form.subscription=value;form.node='';}
        form[searchKey]='';
        form.acknowledged=false;renderChoices(ctx);
      })),
      ui.el('div',{class:'field'},search,ui.field(label+'节点',control),count,
        node && !available ? ui.note('原选项已不可用，请重新选择。','warn') : null));
  };
  let acknowledgement;
  const setField = (key,value) => {
    form[key]=value;form.acknowledged=false;
    const check=acknowledgement?.querySelector?.('input');if(check)check.checked=false;
    updateStartButton(ctx);
  };
  const name=ui.input(form.chainName,{placeholder:'请输入新配置名称',
    oninput:event=>setField('chainName',event.target.value)});
  const acknowledgementLabel = direct ? '我已核对静态代理出口，并了解将应用完整网络防护。' :
    '我已核对两跳节点及最终出口，并了解将应用完整网络防护。';
  acknowledgement=ui.checkbox(form.acknowledged,acknowledgementLabel,value=>{form.acknowledged=value;updateStartButton(ctx);});
  const start=ui.button(stateText(ctx),{variant:'ink',icon:'play',disabled:!form.region || !chainReady() || !form.acknowledged || operationsBlocked(ctx),onClick:()=>beginApply(ctx)});
  start.id='start-apply';
  const saved=(nodesState.data?.subscriptions || []).filter(row=>row.chain &&
    Boolean(row.chain.static_exit) === staticMode && Boolean(row.chain.direct) === direct);
  const selected=staticMode ? savedStatic() : saved.find(row=>row.chain.name===form.chainName &&
    row.chain.front_subscription===form.frontSubscription && row.chain.front_node===form.frontNode &&
    Boolean(row.chain.front_group)===form.frontGroup &&
    row.chain.exit_subscription===form.subscription && row.chain.exit_node===form.node);
  const savedPicker=ui.field('已保存配置',ui.select([{value:'',label:'新建配置'},...saved.map(row=>({value:row.uid,label:row.chain.name}))],selected?.uid || '',value=>{
    const row=saved.find(row=>row.uid===value);
    if(row)loadChainSelection(ctx,row.chain,row.uid);
    else {
      form.chainName='';form.frontSubscription='';form.frontNode='';form.frontGroup=false;form.acknowledged=false;
      form.staticSaved='';form.staticIp='';form.staticPort='';form.staticUser='';form.staticPassword='';
    }
    renderChoices(ctx);
  }));
  const field=(label,key,attrs={})=>ui.field(label,ui.input(form[key],{
    ...attrs,oninput:event=>setField(key,event.target.value),
  }));
  const endpoint = staticMode && !selected ? ui.el('div',{class:'choice-grid'},
    ui.field('代理协议',ui.select([{value:'socks5',label:'SOCKS5'},{value:'http',label:'HTTP'},
      {value:'https',label:'HTTPS'}],form.staticProtocol,value=>setField('staticProtocol',value))),
    field('公网静态 IPv4','staticIp',{placeholder:'填写供应商提供的 IP'}),
    field('代理端口','staticPort',{inputmode:'numeric',maxlength:5}),
    field('代理用户名（可选）','staticUser',{autocomplete:'off'}),
    field('代理密码（可选）','staticPassword',{type:'password',autocomplete:'new-password'}),
  ) : null;
  choicesHost.replaceChildren(...[
    reloadButton(ctx),
    savedPicker,
    ui.note(direct ? 'Clash 直接连接静态 IP 上的代理服务。出口失效时，海外流量中断。' :
      '链式配置使用与普通配置相同的分流和网络防护；任一跳故障时，海外流量中断。','neutral'),
    staticMode ? ui.note('静态 IP 必须已运行代理服务。海外 TCP 经静态出口，海外 UDP 被阻断，国内和局域网按原规则分流。' +
      (direct ? '' : '流量条显示前置订阅额度。'),'info') :
      ui.note('流量条按两份订阅的剩余额度取较小者，显示该账户的已用量/总量，并采用较早到期日。使用 Clash 缓存数据，缺少数据不显示；不是此链路独占的实时用量。','info'),
    !direct && (!staticMode || !selected) ? picker('前置（第一跳）','front') : null,
    staticMode ? endpoint : picker('后置 / 最终出口（第二跳）','exit'),
    ui.el('div',{class:'choice-grid'},!staticMode || !selected ? ui.field('配置名称',name) : null,
      ui.field('最终出口地区',ui.select(regionOptions(),form.region,value=>{
        form.region=value;form.regionManual=Boolean(value);form.regionAuto=null;form.acknowledged=false;renderChoices(ctx);
      }),direct ? '按供应商提供的出口地区选择。' : '按第二跳所在地区选择。')),
    ui.el('p',{class:'muted',text:proxyRoute({front_node:direct ? '' : form.frontNode || '前置节点',
      exit_node:staticMode ? selected?.chain.static_exit.ip || '静态 IP' : form.node || '最终出口节点'})}),
    form.node && !form.region ? ui.note('未识别最终出口地区，请手动选择。','warn') : null,
    form.regionManual ? ui.pill('出口地区为手动选择','neutral') : null,
    acknowledgement, start,
  ].filter(Boolean));
  renderImpact(ctx);
}

async function quickReapply(ctx) {
  if (operationsBlocked(ctx)) return;
  const plan = ctx.state.plan || {};
  if (!plan.node) return;
  const ok = await ctx.confirm({
    title: '按当前记录重新配置？',
    subtitle: plan.node + ' · 出口 ' + (plan.region || 'SG'),
    description: '沿用上次记录的精确节点、订阅和地区重新配置，写入确认后自动执行完整网络验收。验收包含故障演练，公网会短暂中断。',
    items: IMPACT,
    confirmText: '重新配置',
    requireCheck: '我已了解重新配置会关闭 Clash 并短暂中断网络。',
  });
  if (!ok) return;
  ctx.startJob({
    action: 'apply',
    node: plan.node,
    subscription: plan.subscription || '',
    region: plan.region || 'SG',
    confirm: 'APPLY',
  });
}
