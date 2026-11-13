import { icon } from '../icons.js';
import * as ui from '../ui.js';

const FLOW = [
  ['节点连接与真实局域网', '窄范围例外、真实私有地址、已审核的路由器/本地服务域名保持直连。'],
  ['拒绝公网 IPv6', '在代理前阻断公网 IPv6，避免绕过 TUN 的路径。'],
  ['Claude 优先', 'Claude 进程、官方入站 IP、核心与补充域名先于国内规则进入固定节点组。'],
  ['海外域名优先', 'geolocation-!cn 先于国内白名单进入固定海外节点，对应 DNS 同样走海外出口。'],
  ['国内域名', '已审核的国内域名名单先转换为规则；不使用未经审核的远程关键词。'],
  ['国内 IP 与 GEOIP,CN', '国内 IP 段与 GeoIP 规则使用 no-resolve，避免额外 DNS 泄漏。'],
  ['MATCH 固定海外组', '未知海外流量进入固定节点组，不回落 DIRECT。'],
];

export function render(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  const verification = state.verification || {};
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    eyebrow: 'NETWORK',
    title: '网络环境',
    description: '查看当前固定节点、分流顺序、上次验收结果与高级维护动作。这里不会修改浏览器设置。',
    actions: [ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) })],
  }));

  if (!plan.exists) {
    root.append(ui.card({ title: '还没有配置记录' },
      ui.empty('尚未配置网络', '完成一次配置后，这里会显示固定节点、回滚点和验收结果。', ui.button('去一键配置', { variant: 'ink', onClick: () => ctx.navigate('overview') }))));
    return root;
  }

  root.append(configCard(ctx));
  if (state.verification?.dns_failed) {
    root.append(ui.note('上次验收的 DNS A/64/65 未通过，可能存在 DNS 泄漏。请点击「重新检查连通性」；若仍失败，恢复关联快照并重新配置。', 'bad'));
  }
  const health = state.preview?.current?.health;
  root.append(ui.card({ title: '当前配置一致性', subtitle: (health?.scope || '正在读取；未验证') + '。规则或版本不一致请重新配置；系统地区或新增网卡请补充配置。' },
    ...(health?.checks || []).map(row => ui.note(row.title + '：' + (row.status === 'passed' ? '一致' : row.status === 'failed' ? '不一致，请' + row.action : '未验证') + '；当前 ' + (row.title === '配置生成版本' ? (row.current ? '已记录版本' : '旧版未记录') : String(row.current ?? '未知')) + ' → ' + (row.title === '配置生成版本' ? '当前软件版本' : String(row.target)), row.status === 'passed' ? 'info' : 'warn'))));
  root.append(flowCard());
  root.append(verificationCard(ctx));
  root.append(actionsCard(ctx));
  return root;
}

function configCard(ctx) {
  const state = ctx.state;
  const plan = state.plan || {};
  const service = state.service || {};
  return ui.card({
    title: '当前固定配置',
    subtitle: state.configured ? '当前运行配置中检测到项目分流标记；这不等于 DNS/出口仍然通过验收。' : '存在配置记录，但当前运行配置未检测到项目标记；可能已恢复。',
    actions: !state.configured ? ui.badge('未生效', 'warn')
      : state.last_run?.success === false ? ui.badge('上次操作未完成', 'bad')
        : state.verification?.dns_failed ? ui.badge('DNS 验收失败', 'bad')
          : state.verification?.available && state.verification.success ? ui.badge('历史验收通过', 'ok')
            : ui.badge('配置已写入', 'warn'),
  },
  ui.kv([
    ['固定节点', plan.node || '—'],
    ['订阅', plan.subscription || '—'],
    ['出口地区', plan.region || 'SG'],
    ['回滚点', plan.snapshot || '—'],
    ['首次恢复点', state.baseline || '—'],
    ['保持独立', '本项目的固定节点组只包含所选节点，不包含 DIRECT 或自动轮换'],
    ['Clash 服务', service.service || '未知'],
    ['Clash 客户端', service.app_running ? '运行中' : '未运行'],
    ['内核路径', plan.core_program || '—'],
  ]),
  !state.configured ? ui.note('补充配置和规则更新要求当前运行配置仍关联项目脚本。请先重新配置，或从「快照与恢复」恢复到关联快照。', 'warn') : null);
}

function flowCard() {
  return ui.card({ title: '分流顺序', subtitle: '从最窄的例外到最终兜底；顺序用于避免国内域名与 Claude 流量经过不必要的代理。' },
    ui.el('ol', { class: 'flow' }, FLOW.map(([title, text], index) => ui.el('li', {},
      ui.el('span', { class: 'flow-index', text: String(index + 1).padStart(2, '0') }),
      ui.el('div', {}, ui.el('strong', { text: title }), ui.el('p', { text }))))));
}

function verificationCard(ctx) {
  const verification = ctx.state.verification || {};
  const body = [];
  if (!verification.available) {
    body.push(ui.empty('还没有验收结果', '完整配置成功后会自动运行网络验收，结果显示在这里。'));
  } else {
    if (!verification.source_matches_current) body.push(ui.note('该历史报告未对应当前软件版本，请重新配置后验收。', 'warn'));
    body.push(ui.el('div', { class: 'verification-head' },
      ui.badge(verification.success ? (verification.coverage === 'partial' ? '已记录阶段通过，验收不完整' : '历史验收通过') : '验收未通过', verification.success ? 'ok' : 'bad'),
      ui.el('span', { class: 'muted', text: '记录时间 ' + ui.formatTime(verification.time) + ' · ' + ({'full-network':'完整网络验收','connectivity':'仅连通性检查','partial':'部分阶段'}[verification.coverage] || '旧版记录，范围待核对') }),
      verification.egress?.country ? ui.pill(verification.egress.country + ' · ' + (verification.egress.city || ''), 'neutral') : null));
    body.push(ui.el('div', { class: 'check-grid' }, (verification.checks || []).map((check) => ui.el('div', { class: 'check-item' },
      ui.el('span', { class: 'check-mark ' + ui.statusTone(check.status) }, check.status === 'passed' ? icon('check', 14) : check.status === 'failed' ? icon('close', 14) : icon('info', 14)),
      ui.el('div', {}, ui.el('strong', { text: check.title }), ui.el('small', { text: ui.statusText(check.status) }))))));
    body.push(ui.el('div', { class: 'stage-grid' }, (verification.stages || []).map((stage) => ui.el('div', { class: 'stage-item' },
      ui.el('span', { class: 'status-dot ' + (stage.success ? 'ok' : stage.status === 'unverified' ? 'warn' : stage.tested ? 'bad' : 'warn') }),
      ui.el('div', {}, ui.el('strong', { text: stage.title }), ui.el('small', { text: stage.success ? '已通过' : stage.status === 'unverified' ? '未验证' : stage.tested ? '未通过' : '未记录' }))))));
    if (verification.not_tested?.length) {
      body.push(ui.note('以下项目仍未验证：' + verification.not_tested.join('、') + '。', 'warn'));
    }
    body.push(ui.el('p', { class: 'muted small', text: '验收只代表当时观测到的结果，不能证明 Claude 账号可用、浏览器指纹或所有应用路径。' }));
  }
  return ui.card({ title: '上次网络验收', actions: ui.button('查看日志', { variant: 'ghost', size: 'small', icon: 'list', onClick: () => ctx.navigate('logs') }) }, ...body);
}

function actionsCard(ctx) {
  const state = ctx.state;
  const unavailable = !state.configured;
  return ui.card({ title: '维护与恢复', subtitle: '这些动作会修改系统设置或短暂中断网络，执行前会再次确认。' },
    ui.el('div', { class: 'action-grid' },
      actionRow('补充配置', '安装新软件或新增网卡后，扫描新增出站放行规则和启用 IPv6 的网卡，并累积恢复记录。不会关闭浏览器或运行网络测试。',
        ui.button('补充配置', { variant: 'secondary', icon: 'shield', disabled: unavailable || ctx.busy, onClick: () => supplement(ctx) })),
      actionRow('重新检查连通性', '运行一次连通性验收（不模拟节点故障或停核），检查出口地区、DNS、IPv6 与工具出口。',
        ui.button('检查连通性', { variant: 'secondary', icon: 'refresh', disabled: unavailable || ctx.busy, onClick: () => verify(ctx) })),
      actionRow('完整防火墙复核与故障演练', '以管理员权限完整检查防火墙，分别演练节点故障、关闭 TUN 但保持内核运行、内核退出，并在恢复后复测。公网会短暂中断。',
        ui.button('完整复核与演练', { variant: 'secondary', icon: 'shield', disabled: unavailable || ctx.busy, onClick: () => audit(ctx) })),
      actionRow('恢复最早状态并重新配置', '先恢复固定首次恢复点并确认联网，再沿用当前精确节点完整配置。需要独立本机看护；失败或超时会尝试恢复网络。', 
        ui.button('恢复并重新配置', { variant: 'danger', icon: 'alert', disabled: !state.baseline || !state.plan.node || ctx.busy, onClick: () => reconfigure(ctx) })),
    ),
    ui.note('严格防火墙的含义：节点故障但内核仍在运行时，国内网站保持直连；内核停止时，所有公网（包括国内）都会被阻断。这是有意的保护行为。', 'info'));
}

function actionRow(title, text, action) {
  return ui.el('div', { class: 'action-row' },
    ui.el('div', {}, ui.el('strong', { text: title }), ui.el('p', { text })),
    action);
}

async function supplement(ctx) {
  const ok = await ctx.confirm({
    title: '执行补充配置？',
    subtitle: '不重新选择节点，不关闭浏览器',
    description: '会扫描当前本地防火墙规则与全部网卡，禁用非项目的出站放行规则、关闭启用的 IPv6 绑定、系统定位、自动时区和自动校时，按所选节点地区设置国家/区域格式/时区，将改动记录到关联快照。入站规则保留。',
    items: [
      { title: '可能影响', text: '新软件、其他 VPN、Docker/WSL、远程控制和依赖 IPv6 的内网资源可能失效。' },
      { title: '不运行测试', text: '补充配置不会运行网络或断网测试；需要时请另行检查连通性。' },
    ],
    note: '需要已有完整配置记录和可校验的原始快照；缺失时会停止，不会覆盖首次基线。',
    confirmText: '补充配置',
    requireCheck: '我已了解补充配置会修改防火墙、网卡、定位、国家/时间设置。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'supplement', confirm: 'SUPPLEMENT' });
}

async function verify(ctx) {
  const ok = await ctx.confirm({
    title: '重新检查连通性？',
    description: '会读取当前运行配置并测试国内网站、海外出口地区、DNS A/64/65、TUN 接管、IPv6 阻断以及 Git/npm/UDP。不会模拟节点故障或停核。',
    confirmText: '开始检查',
  });
  if (!ok) return;
  ctx.startJob({ action: 'verify', confirm: 'VERIFY' });
}

async function audit(ctx) {
  const ok = await ctx.confirm({
    title: '完整复核防火墙并演练故障？',
    description: '将请求管理员权限，检查完整生效策略，分别模拟节点故障、关闭 TUN、内核退出，并验证恢复。公网和正在使用的连接会短暂中断，可能需要数分钟。',
    items: [{title: '独立恢复看护', text: 'TUN 演练前启用本机恢复看护；失败会记录恢复状态。'},
      {title: '权限或证据不足', text: '明确标为未验证，不沿用历史成功记录。'}],
    confirmText: '开始完整复核',
  });
  if (ok) ctx.startJob({action: 'audit', confirm: 'AUDIT'});
}

async function reconfigure(ctx) {
  const state = ctx.state;
  const ok = await ctx.confirm({
    title: '恢复最早状态并重新配置？',
    subtitle: '这是一个高风险恢复动作',
    description: '将先恢复固定首次恢复点并确认联网，再沿用配置记录中的精确节点、订阅和出口地区完整配置。独立本机看护会在失败、超时或主控退出时尝试恢复网络。',
    items: [
      { title: '会中断网络', text: '恢复和配置期间公网会短暂中断；正在下载、会议或远程控制可能断开。' },
      { title: '不会关闭浏览器', text: '默认浏览器都不会被关闭，但网页可能因网络中断失去连接。' },
      { title: '记录位置', text: '.local/reconfigure-runs 保存阶段、日志与恢复结果。' },
    ],
    note: '必须已有配置记录、可校验的首次恢复点和独立看护就绪；不满足时会在改动系统前停止。',
    confirmText: '确认恢复并重新配置',
    danger: true,
    requireCheck: '我已保存工作，并了解网络会短暂中断且恢复不一定成功。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'reconfigure', confirm: 'RECONFIGURE' });
}
