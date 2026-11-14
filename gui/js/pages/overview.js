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
  mode: 'single',
  frontSubscription: '',
  frontNode: '',
  chainName: '',
  node: '',
  subscription: '',
  region: 'SG',
  search: '',
  acknowledged: false,
  initialized: false,
  regionAuto: null,
  regionManual: false,
};
const modeDrafts = { single: null, chain: null };
const draftFields = ['node','subscription','region','regionAuto','regionManual','search',
  'frontSubscription','frontNode','chainName'];

let nodesState = { loading: false, error: '', data: null };
let subscriptionUid = '';
let rootNode = null;
let choicesHost = null;
let listHost = null;
let impactHost = null;

export function render(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  if (!form.initialized) {
    form.region = plan.region || 'SG';
    form.subscription = plan.subscription || '';
    form.node = plan.node || '';
    if (plan.chain) {
      form.mode='chain';loadChainSelection(ctx,plan.chain);
    }
    form.initialized = true;
  }

  rootNode = document.createElement('div');
  rootNode.className = 'stack setup-page';
  rootNode.append(ui.pageHeader({
    title: state.configured ? '网络配置' : '一键配置',
    actions: [
      ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) }),
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
}

function statusGrid(ctx) {
  const state = ctx.state;
  const verification = state.verification || {};
  const awaitingVerification = state.last_run?.network_tested === false &&
    (!verification.available || (Date.parse(verification.time) || 0) < (Date.parse(state.last_run.configuration_time) || Infinity));
  const update = state.update || {};
  const tiles = [
    {
      label: '网络配置',
      value: !state.configured ? '未配置' : state.last_run?.success === false ? '上次操作未完成' : '已配置',
      tone: !state.configured ? 'warn' : state.last_run?.success === false ? 'bad' : awaitingVerification ? 'warn' : verification.dns_failed ? 'bad' : verification.available && verification.success ? 'ok' : 'warn',
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
      value: verification.available ? (verification.success ? '历史通过' : '历史未通过') : '待检查',
      tone: verification.available ? (verification.success ? 'ok' : 'bad') : 'neutral',
      detail: verification.available ? ui.relativeTime(verification.time) : '在网络环境按需点击检查',
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
        disabled: ctx.busy || !plan.node,
        onClick: () => quickReapply(ctx),
      })
    : null;

  const currentBand = configured ? ui.el('div', { class: 'current-band' },
    ui.el('div', {}, ui.el('strong', { text: plan.node || '—' })),
    plan.chain ? ui.el('p',{class:'muted small',text:'链式：'+plan.chain.front_node+' → '+plan.chain.exit_node+'；完整防护状态请查看网络环境。'}) : null,
    ui.el('div', { class: 'current-meta' },
      ui.pill('订阅 ' + (plan.subscription ? plan.subscription.slice(0, 8) : '—'), 'neutral'),
      ui.pill('出口 ' + (plan.region || 'SG'), 'neutral'),
      ui.helpTip('恢复点：' + (plan.snapshot || '尚未记录'), '当前恢复点'))) : null;

  const card = ui.card({
    title: configured ? '更换节点或重新配置' : '选择节点',
    help: '从已有订阅选择精确节点；固定节点组只包含所选节点，节点故障不回落直连。配置会请求管理员权限。',
    actions: headerActions,
  }, currentBand, choicesHost);

  loadChoices(ctx);
  return card;
}

function renderImpact(ctx) {
  if (!impactHost) return;
  const preview = ctx.state.preview || {};
  const now = preview.current || {};
  const target = preview.regions?.[form.region] || {};
  const selected = Boolean(form.node && (currentSubscription(ctx)?.nodes || []).includes(form.node));
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
    ['国外连接节点', now.fixed_node || unknown, after(form.node)],
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
  choicesHost.replaceChildren(ui.el('div', { class: 'loading-inline' }, ui.el('span', { class: 'spinner' }), '正在读取订阅与节点…'));
  try {
    const data = await ctx.loadNodes(true);
    nodesState.data = data;
    nodesState.error = '';
    if (form.mode==='single') {
      const singles=(data.subscriptions || []).filter(row=>!row.chain);
      const planned=singles.find(row=>row.uid===data.plan_subscription);
      if (!form.subscription) form.subscription=planned?.uid || singles.find(row=>row.uid===data.active_uid)?.uid || singles[0]?.uid || '';
      if (!form.node && planned?.uid===form.subscription && planned.nodes.includes(data.plan_node)) form.node=data.plan_node;
    }
    renderChoices(ctx);
  } catch (error) {
    nodesState.error = error.message || '读取订阅失败。';
    choicesHost.replaceChildren(ui.note(nodesState.error, 'bad'),
      ui.button('重试', { variant: 'secondary', size: 'small', onClick: () => loadChoices(ctx) }));
  } finally {
    nodesState.loading = false;
  }
}

function currentSubscription(ctx) {
  const subscriptions = (nodesState.data?.subscriptions || []).filter(row=>!row.chain);
  return subscriptions.find((row) => row.uid === form.subscription) || subscriptions[0] || null;
}

function loadChainSelection(ctx, saved) {
  form.subscription=saved.exit_subscription;form.node=saved.exit_node;
  form.frontSubscription=saved.front_subscription;form.frontNode=saved.front_node;form.chainName=saved.name;
  form.regionManual=false;form.regionAuto=null;form.acknowledged=false;
  applyRegionHint(ctx,form.node);
}

function switchMode(ctx, value) {
  if (value===form.mode) return;
  modeDrafts[form.mode]=Object.fromEntries(draftFields.map(key=>[key,form[key]]));
  form.mode=value;form.acknowledged=false;
  if (modeDrafts[value]) Object.assign(form,modeDrafts[value]);
  else if (value==='chain' && nodesState.data?.chain_defaults) loadChainSelection(ctx,nodesState.data.chain_defaults);
  else {
    form.node='';form.subscription='';form.frontSubscription='';form.frontNode='';form.chainName='';
    form.region='SG';form.regionAuto=null;form.regionManual=false;form.search='';
  }
  renderChoices(ctx);
}

function renderChoices(ctx) {
  if (!choicesHost) return;
  const data = nodesState.data;
  const subscriptions = (data?.subscriptions || []).filter(row=>!row.chain);
  const mode = ui.field('配置方式', ui.select([
    {value:'single',label:'单一节点配置'},
    {value:'chain',label:'链式代理配置'},
  ], form.mode, value => switchMode(ctx,value)), '两种配置可以先后添加并同时保留，切换表单不会清空另一种方式的选择。');
  if (form.mode==='chain') {
    if (!ctx.state.capabilities?.managed_chain) {
      choicesHost.replaceChildren(mode,ui.note('软件后台仍是旧版本。请点击退出界面后，重新运行「一键网络.cmd」，再创建链式代理。','warn'));
      return;
    }
    renderChainChoices(ctx,mode);return;
  }
  if (form.node && !form.regionAuto && !form.regionManual) applyRegionHint(ctx, form.node);
  if (!subscriptions.length) {
    choicesHost.replaceChildren(ui.note('当前 Clash Verge 中没有可读取的远程订阅。请先在 Clash 更新订阅；本项目不会创建或猜测节点。', 'warn'));
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
  const filter = form.search.trim().toLowerCase();
  const filtered = filter ? nodeNames.filter((name) => name.toLowerCase().includes(filter)) : nodeNames;

  const subSelect = ui.select(
    subscriptions.map((row) => ({ value: row.uid, label: row.name + '（' + row.nodes.length + ' 个节点）' })),
    form.subscription,
    (value) => {
      form.subscription = value;
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
    oninput: (event) => { form.search = event.target.value; renderNodeList(ctx, filtered); },
  });

  listHost = document.createElement('div');
  listHost.className = 'node-list';
  const columns = ui.el('div', { class: 'choice-grid' },
    ui.field('订阅', subSelect),
    ui.field('出口地区', ui.select(REGIONS.map(([value, label]) => ({ value, label })), form.region, (value) => {
      form.region = value;
      form.regionAuto = null;
      form.regionManual = true;
      form.acknowledged = false;
      renderChoices(ctx);
    }), '从节点名自动填入地区提示；实际出口可通过独立的连通性检查核对，可随时手动修改。'),
  );

  const selected = ui.el('div', { class: 'selected-node' },
    ui.el('span', { class: 'muted small', text: '已选节点' }),
    ui.el('strong', { text: form.node || '尚未选择节点' }),
    form.node ? ui.pill('出口 ' + form.region, 'neutral') : null,
    form.regionAuto ? ui.pill('自动识别 ' + form.regionAuto.code, 'ok') : null);

  const ack = ui.checkbox(form.acknowledged, '我已确认所选节点，并了解配置会修改网络与系统设置。', (value) => {
    form.acknowledged = value;
    updateStartButton(ctx);
  });

  const start = ui.button(stateText(ctx), {
    variant: 'ink',
    icon: 'play',
    disabled: !form.node || !form.acknowledged || ctx.busy,
    onClick: () => beginApply(ctx),
  });
  start.id = 'start-apply';

  const toolbar = ui.el('div', { class: 'choice-toolbar' }, search, ui.el('span', { class: 'muted', text: filtered.length + ' / ' + nodeNames.length + ' 个节点' }));
  const hint = planNodeMissing
    ? ui.note('上次使用的节点已不在当前订阅中。请重新选择；本项目不会自动替换节点。', 'warn')
    : null;

  const missingHint = form.node && !selectedInside
    ? ui.note('所选节点不在当前订阅中。请从上方列表重新选择；不会自动替换。', 'warn')
    : null;
  const regionHint = form.node && !form.regionAuto && !form.regionManual
    ? ui.note('未识别出口地区，请手动选择。', 'warn') : null;
  choicesHost.replaceChildren(...[
    mode,
    columns,
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
  renderNodeList(ctx, filtered);
  renderImpact(ctx);
  updateStartButton(ctx);
}

function renderNodeList(ctx, filtered) {
  if (!listHost) return;
  const current = currentSubscription(ctx);
  const all = current?.nodes || [];
  const query = form.search.trim().toLowerCase();
  const names = query ? all.filter((name) => name.toLowerCase().includes(query)) : all;
  if (!names.length) {
    listHost.replaceChildren(ui.el('p', { class: 'muted pad', text: query ? '没有匹配的节点。' : '这个订阅没有可直接选择的节点。' }));
    return;
  }
  listHost.replaceChildren(...names.slice(0, 800).map((name) => ui.el('button', {
    class: 'node-row' + (name === form.node ? ' selected' : ''),
    type: 'button',
    title: name,
    onclick: () => {
      form.node = name;
      form.acknowledged = false;
      form.regionManual = false;
      applyRegionHint(ctx, name);
      renderChoices(ctx);
    },
  }, ui.el('span', { class: 'node-name', text: name }), name === form.node ? icon('check', 16) : icon('chevron', 14))));
  if (names.length > 800) {
    listHost.append(ui.el('p', { class: 'muted pad', text: '仅显示前 800 个节点，请用搜索缩小范围。' }));
  }
}

function applyRegionHint(ctx, name) {
  if (!name) {
    form.regionAuto = null;
    return;
  }
  const hint = nodesState.data?.regions?.[form.subscription]?.[name] || null;
  if (hint?.code) {
    form.region = hint.code;
    form.regionAuto = hint;
  } else {
    form.regionAuto = null;
  }
}

function updateStartButton(ctx) {
  const button = document.getElementById('start-apply');
  if (!button) return;
  const current = currentSubscription(ctx);
  const inside = Boolean(current && (current.nodes || []).includes(form.node));
  button.disabled = !form.node || !inside || (form.mode==='chain' && !chainReady()) || !form.acknowledged || ctx.busy;
  button.title = form.node && !inside ? '所选节点不在当前订阅中，请重新选择。' : '';
}

function stateText(ctx) {
  return form.mode==='chain' ? '配置此链式代理' : '配置此单一节点';
}

async function beginApply(ctx) {
  if (form.mode==='chain' && !ctx.state.capabilities?.managed_chain) {
    ui.toast('请先退出并重新打开软件，加载链式代理功能。','warn');return;
  }
  if (!form.node) return;
  if (!form.acknowledged) {
    ui.toast('请先勾选确认。', 'warn');
    return;
  }
  const subscription = currentSubscription(ctx);
  const chain=form.mode==='chain' ? {name:form.chainName.trim(),exit_subscription:form.subscription,
    exit_node:form.node,front_subscription:form.frontSubscription,front_node:form.frontNode} : null;
  if (chain && !chainReady()) return;
  const ok = await ctx.confirm({
    title: '开始配置网络？',
    subtitle: (chain ? chain.name+'：电脑 → '+chain.front_node+' → '+chain.exit_node : form.node) + ' · 最终出口 ' + form.region,
    description: '生成并校验网络规则，应用系统设置和三款常用浏览器的 WebRTC 策略，不自动运行联网检查或故障演练；外部指纹浏览器自行管理。',
    items: IMPACT,
    note: 'Mihomo 停止时所有公网（包括国内网站）都会短暂断开；请先保存正在进行的网络工作。',
    noteType: 'warn',
    confirmText: '确认配置',
    requireCheck: '我已保存工作，并了解严格防火墙、IPv6、定位、自动时间和国家/区域格式/时区改动的影响。',
  });
  if (!ok) return;
  ctx.startJob({
    action: 'apply',
    node: form.node,
    subscription: subscription?.uid || form.subscription,
    region: form.region,
    ...(chain ? {chain} : {}),
    confirm: 'APPLY',
  });
}

function chainReady() {
  const rows=(nodesState.data?.subscriptions || []).filter(r=>!r.chain);
  return Boolean(form.chainName.trim() && rows.some(r=>r.uid===form.subscription && r.nodes.includes(form.node)) &&
    rows.some(r=>r.uid===form.frontSubscription && r.nodes.includes(form.frontNode)) &&
    !(form.subscription===form.frontSubscription && form.node===form.frontNode));
}

function renderChainChoices(ctx,mode) {
  const rows=(nodesState.data?.subscriptions || []).filter(r=>!r.chain);
  const getSub=uid=>rows.find(r=>r.uid===uid);
  const subOptions=[{value:'',label:'请选择原订阅'},...rows.map(r=>({value:r.uid,label:r.name}))];
  const picker=(label,side)=>{
    const front=side==='front';const uid=front?form.frontSubscription:form.subscription;
    const node=front?form.frontNode:form.node;
    return ui.el('div',{class:'choice-grid'},
      ui.field(label+'订阅',ui.select(subOptions,uid,value=>{
        if(front){form.frontSubscription=value;form.frontNode='';}else{form.subscription=value;form.node='';}
        form.acknowledged=false;renderChoices(ctx);
      })),
      ui.field(label+'节点',ui.select([{value:'',label:'请选择精确节点'},...(getSub(uid)?.nodes || []).map(n=>({value:n,label:n}))],node,value=>{
        if(front)form.frontNode=value;else{form.node=value;form.regionManual=false;applyRegionHint(ctx,value);}
        form.acknowledged=false;renderChoices(ctx);
      })));
  };
  let acknowledgement;
  const name=ui.input(form.chainName,{'aria-label':'链式节点名称',placeholder:'请输入新链式节点名称',oninput:event=>{
    form.chainName=event.target.value;form.acknowledged=false;
    const check=acknowledgement?.querySelector?.('input');if(check)check.checked=false;
    updateStartButton(ctx);
  }});
  acknowledgement=ui.checkbox(form.acknowledged,'我已核对两跳节点及最终出口，并了解将应用完整网络防护。',value=>{form.acknowledged=value;updateStartButton(ctx);});
  const start=ui.button(stateText(ctx),{variant:'ink',icon:'play',disabled:!chainReady() || !form.acknowledged || ctx.busy,onClick:()=>beginApply(ctx)});
  start.id='start-apply';
  const saved=(nodesState.data?.subscriptions || []).filter(row=>row.chain);
  const selected=saved.find(row=>row.chain.name===form.chainName && row.chain.front_subscription===form.frontSubscription &&
    row.chain.front_node===form.frontNode && row.chain.exit_subscription===form.subscription && row.chain.exit_node===form.node);
  const savedPicker=ui.field('链式配置',ui.select([{value:'',label:'新建链式配置'},...saved.map(row=>({value:row.uid,label:row.chain.name}))],selected?.uid || '',value=>{
    const row=saved.find(row=>row.uid===value);
    if(row)loadChainSelection(ctx,row.chain);
    else{form.chainName='';form.frontSubscription='';form.frontNode='';form.acknowledged=false;}
    renderChoices(ctx);
  }),'已有链式配置可再次选择；新建链式配置会保留已配置的单一节点。');
  choicesHost.replaceChildren(mode,
    savedPicker,
    ui.note('原订阅和原节点保持不变。链式配置使用与普通配置相同的分流、TUN、DNS、IPv6、防火墙及受管 WebRTC 防护；任一跳故障均不切换到直连。','neutral'),
    ui.note('流量条按两份订阅的剩余额度取较小者，显示该账户的已用量/总量，并采用较早到期日。使用 Clash 缓存数据，缺少数据不显示；不是此链路独占的实时用量。','info'),
    picker('前置（第一跳）','front'),picker('后置 / 最终出口（第二跳）','exit'),
    ui.el('div',{class:'choice-grid'},ui.field('链式节点名称',name),
      ui.field('最终出口地区',ui.select(REGIONS.map(([value,label])=>({value,label})),form.region,value=>{
        form.region=value;form.regionManual=true;form.acknowledged=false;renderChoices(ctx);
      }),'以第二跳所在地区为准；不会使用前置节点的地区。')),
    ui.el('p',{class:'muted',text:'电脑 → '+(form.frontNode || '前置节点')+' → '+(form.node || '最终出口节点')+' → 目标网站'}),
    acknowledgement,start);
  renderImpact(ctx);
}

async function quickReapply(ctx) {
  const plan = ctx.state.plan || {};
  if (!plan.node) return;
  const ok = await ctx.confirm({
    title: '按当前记录重新配置？',
    subtitle: plan.node + ' · 出口 ' + (plan.region || 'SG'),
    description: '沿用上次记录的精确节点、订阅和地区，生成校验后重新应用配置，不先恢复最早状态。节点不存在时停止；只更新名单请去规则更新，新增网卡或地区设置偏差请使用补充配置。不自动运行联网检查或故障演练。',
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
