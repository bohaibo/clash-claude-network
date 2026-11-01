import { icon } from '../icons.js';
import * as ui from '../ui.js';

const IMPACT = [
  { title: 'Clash 客户端', text: '为所选订阅关联独立的项目增强脚本与固定节点组；保留原自定义脚本和现有节点。' },
  { title: '严格出站防火墙', text: '保留四条项目放行并阻断 SharedAccess 服务出站。Mihomo 停止时国内公网也会断开。' },
  { title: '全浏览器 WebRTC', text: '设置 Chrome、Edge、Firefox 策略；可能影响网页语音和视频，Firefox 关闭 WebRTC。需重启浏览器，其他浏览器和实际泄露效果未验证。' },
  { title: '系统 IPv6', text: '全局关闭并关闭现有网卡的 IPv6 绑定；依赖 IPv6 的内网资源可能不可用，需要重启 Windows 才完全生效。' },
  { title: '系统自动定位', text: '通过 Windows 策略关闭系统定位，地图、天气等定位功能会受影响；恢复时还原原有策略。' },
  { title: '区域与时区', text: '关闭自动时区和自动校时，按所选节点地区设置系统国家、区域格式和时区；时间按时区显示。长期关闭校时可能产生误差，恢复会还原原状态。' },
  { title: '代理清理', text: '清理系统、WinHTTP、环境变量与 Git/npm/Docker 的代理覆盖，普通流量交给 TUN；已打开的终端需要重启。' },
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
  node: '',
  subscription: '',
  region: 'SG',
  search: '',
  acknowledged: false,
  initialized: false,
  regionAuto: null,
  regionManual: false,
};

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
    form.initialized = true;
  }

  rootNode = document.createElement('div');
  rootNode.className = 'stack';
  rootNode.append(ui.pageHeader({
    eyebrow: 'SETUP',
    title: state.configured ? '网络配置' : '一键配置',
    description: '选择一个订阅中已存在的完整节点。固定节点组只包含这个节点；未知海外流量走该节点，节点故障时不回落直连。',
    actions: [
      ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) }),
    ],
  }));
  rootNode.append(statusGrid(ctx));
  impactHost = document.createElement('div');
  rootNode.append(impactHost);
  renderImpact(ctx);
  rootNode.append(setupCard(ctx));
  return rootNode;
}

function statusGrid(ctx) {
  const state = ctx.state;
  const verification = state.verification || {};
  const browser = state.browser || {};
  const update = state.update || {};
  const tiles = [
    {
      label: '网络配置',
      value: !state.configured ? '未配置' : state.last_run?.success === false ? '上次操作未完成' : verification.dns_failed ? 'DNS 验收失败' : verification.available && verification.success ? '验收通过' : '已写入',
      tone: !state.configured ? 'warn' : state.last_run?.success === false || verification.dns_failed ? 'bad' : verification.available && verification.success ? 'ok' : 'warn',
      detail: state.configured ? ('节点 ' + (state.plan.node || '—') + (verification.available ? ' · 上次验收 ' + ui.relativeTime(verification.time) : ' · 尚未验收')) : '首次配置会先保存原始快照',
      action: state.configured ? () => ctx.navigate('network') : null,
    },
    {
      label: '规则更新',
      value: update.mode === 'scheduled' ? ('每天 ' + (update.time || '13:00')) : '手动',
      tone: update.last?.success ? 'ok' : update.last?.time ? 'warn' : 'neutral',
      detail: update.last?.time ? ('上次 ' + ui.relativeTime(update.last.time)) : '尚未更新',
      action: () => ctx.navigate('updates'),
    },
    {
      label: '沙箱浏览器',
      value: browser.running ? '运行中' : browser.engine?.name ? '未运行' : '不可用',
      tone: browser.running ? 'ok' : browser.engine?.name ? 'neutral' : 'warn',
      detail: browser.engine?.name ? (browser.engine.name === 'chrome' ? 'Chrome 独立资料目录' : 'Edge 独立资料目录') : '需要安装 Chrome 或 Edge',
      action: () => ctx.navigate('browser'),
    },
    {
      label: '上次验收',
      value: verification.available ? (verification.success ? '通过' : '未通过') : '未验证',
      tone: verification.available ? (verification.success ? 'ok' : 'bad') : 'neutral',
      detail: verification.available ? ui.relativeTime(verification.time) : '配置完成后自动运行',
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
    ui.el('div', {}, ui.el('span', { class: 'eyebrow', text: 'CURRENT' }), ui.el('strong', { text: plan.node || '—' })),
    ui.el('div', { class: 'current-meta' },
      ui.pill('订阅 ' + (plan.subscription ? plan.subscription.slice(0, 8) : '—'), 'neutral'),
      ui.pill('出口 ' + (plan.region || 'SG'), 'neutral'),
      plan.snapshot ? ui.pill('回滚点 ' + plan.snapshot, 'neutral') : null)) : null;

  const card = ui.card({
    title: configured ? '更换节点或重新配置' : '选择节点',
    subtitle: configured
      ? '当前节点：' + (plan.node || '—') + ' · 出口 ' + (plan.region || 'SG')
      : '节点必须来自当前 Clash Verge 订阅；不会自动替换或猜测名称。',
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
    ['日期和数字格式', languageName(now.culture), after(languageName(target.culture))],
    ['时区', now.timezone === 'China Standard Time' ? '中国标准时间' : zoneName(Object.values(preview.regions || {}).find((r) => r.timezone_id === now.timezone)?.iana), after(zoneName(target.iana))],
    ['独立浏览器语言', /尚未设置/.test(now.browser_language || '') ? '尚未设置' : languageName(now.browser_language), after(languageName(target.browser_language))],
    ['IPv6 网络', now.ipv6_state, '关闭'],
    ['严格联网保护', now.firewall_state, '开启'],
    ['Windows 手动代理', now.proxy_state, '关闭'],
    ['Windows 后台服务代理', proxyState(now.winhttp), '关闭'],
    ['环境变量代理', proxyState(now.environment), '关闭'],
    ['Git 代理', proxyState(now.git), '关闭'],
    ['npm 代理', proxyState(now.npm), '关闭'],
    ['Docker 代理', proxyState(now.docker), '关闭'],
    ['网站连接方式', now.routing_simple, '国内直接连接，国外使用所选节点'],
    ['国外连接节点', '尚未核实', after(form.node)],
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
      ['WebRTC 防泄露（全浏览器）', now.webrtc_protection, '开启', '覆盖 Chrome、Edge、Firefox 普通窗口；需重启浏览器，其他浏览器未验证'],
      ['DNS 防泄露', now.dns_protection, '开启', '防止域名查询绕过代理']], '开启表示已写入保护策略；实际效果需检测，Edge 个人账号资料可能不采用策略。'],
    ['代理设置', rows.slice(9, 15), '关闭之前分散在各处的代理设置，统一由 TUN 模式接管网络。'],
    ['网站访问', rows.slice(15)],
  ];
  impactHost.replaceChildren(ui.card({ title: selected ? '配置后会' : '配置会发生什么',
    subtitle: '读取于 ' + readTime + ' · 选择节点后，国家、语言和时区会自动更新。',
    class: 'config-preview-card' },
    ui.el('div', { class: 'config-legend', 'aria-label': '状态颜色说明' },
      valueBadge('开启'), valueBadge('关闭'), valueBadge('未能确认')),
    ui.el('table', { class: 'config-table', 'aria-label': '当前状态与配置后的对照' },
      ui.el('thead', {}, ui.el('tr', {},
        ui.el('th', { scope: 'col', text: '配置项目' }),
        ui.el('th', { scope: 'col', text: '当前状态' }),
        ui.el('th', { scope: 'col', class: 'config-target-heading', text: '配置后' }))),
      groups.map(([title, items, description]) => ui.el('tbody', {},
        ui.el('tr', { class: 'config-group' }, ui.el('th', { colSpan: 3, scope: 'rowgroup' },
          ui.el('span', { text: title }), description ? ui.el('small', { class: 'config-explanation', text: description }) : null)),
        items.map(([label, current, desired, description]) => ui.el('tr', { class: 'config-item' },
          ui.el('th', { scope: 'row' }, ui.el('span', { text: label }), description ? ui.el('small', { class: 'config-explanation', text: description }) : null),
          ui.el('td', { 'data-label': '当前' }, valueBadge(current)),
          ui.el('td', { 'data-label': '配置后', class: 'config-target' }, valueBadge(desired))))))),
    ui.note('配置前会备份。配置时网络会短暂中断；代理核心停止运行时，所有外网连接都会被拦住。关闭自动校时后，时间可能逐渐出现误差。浏览器语言需重新打开后生效，部分设置需要重启电脑。Windows 界面语言保持不变。WebRTC 保护会修改 Chrome、Edge、Firefox 的浏览器策略，可能影响网页语音和视频通话；Firefox 将关闭 WebRTC。', 'warn')));

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
    if (data.plan_node && !form.node) form.node = data.plan_node;
    if (data.plan_subscription && !form.subscription) form.subscription = data.plan_subscription;
    if (!form.subscription) form.subscription = data.active_uid || data.subscriptions?.[0]?.uid || '';
    if (!form.node && data.plan_node) form.node = data.plan_node;
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
  const subscriptions = nodesState.data?.subscriptions || [];
  return subscriptions.find((row) => row.uid === form.subscription) || subscriptions[0] || null;
}

function renderChoices(ctx) {
  if (!choicesHost) return;
  const data = nodesState.data;
  const subscriptions = data?.subscriptions || [];
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
  const planNodeMissing = data?.plan_node && !subscriptions.some((row) => row.nodes.includes(data.plan_node));
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
    }), '从节点名自动填入地区提示；实际出口由配置后的网络验收核对，可随时手动修改。'),
  );

  const selected = ui.el('div', { class: 'selected-node' },
    ui.el('span', { class: 'eyebrow', text: 'SELECTED' }),
    ui.el('strong', { text: form.node || '尚未选择节点' }),
    form.node ? ui.pill('出口 ' + form.region, 'neutral') : null,
    form.regionAuto ? ui.pill('自动识别 ' + form.regionAuto.code, 'ok') : null);

  const ack = ui.checkbox(form.acknowledged, '我已了解上述影响，并确认所选节点确实存在于该订阅中。', (value) => {
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
  const regionHint = form.node
    ? (form.regionAuto
        ? ui.note('出口地区已按节点名自动填为 ' + form.regionAuto.code + '。实际出口由配置后的网络验收核对；如识别错误可直接修改。配置时系统与沙箱浏览器会同步切换到该地区。', 'info')
        : form.regionManual
          ? ui.note('已手动选择出口地区 ' + form.region + '；配置时系统与沙箱浏览器会同步切换到该地区。', 'info')
          : ui.note('未能从节点名识别出口地区，请手动选择。实际出口由配置后的网络验收核对。', 'warn'))
    : null;
  choicesHost.replaceChildren(...[
    columns,
    hint,
    toolbar,
    listHost,
    selected,
    regionHint,
    missingHint,
    ui.el('div', { class: 'ack-row' }, ack),
    ui.el('div', { class: 'choice-foot' },
      ui.el('p', { class: 'muted', text: '配置会请求管理员权限。界面本身以普通用户运行；沙箱浏览器也不会被提权。' }),
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
  button.disabled = !form.node || !inside || !form.acknowledged || ctx.busy;
  button.title = form.node && !inside ? '所选节点不在当前订阅中，请重新选择。' : '';
}

function stateText(ctx) {
  return ctx.state.configured ? '使用所选节点重新配置' : '开始配置';
}

async function beginApply(ctx) {
  if (!form.node) return;
  if (!form.acknowledged) {
    ui.toast('请先勾选确认。', 'warn');
    return;
  }
  const subscription = currentSubscription(ctx);
  const ok = await ctx.confirm({
    title: '开始配置网络？',
    subtitle: form.node + ' · 出口 ' + form.region,
    description: '配置会关闭 Clash Verge、写入项目增强脚本、按节点地区应用系统与沙箱浏览器设置，并运行网络验收。界面与默认浏览器不会被关闭。WebRTC 保护将应用到 Chrome、Edge、Firefox，网页通话可能受影响；需重启浏览器。',
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
    confirm: 'APPLY',
  });
}

async function quickReapply(ctx) {
  const plan = ctx.state.plan || {};
  if (!plan.node) return;
  const ok = await ctx.confirm({
    title: '按当前记录重新配置？',
    subtitle: plan.node + ' · 出口 ' + (plan.region || 'SG'),
    description: '沿用上次配置记录的精确节点、订阅和出口地区；如果该节点已从当前订阅消失，配置会在写入前停止。',
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
