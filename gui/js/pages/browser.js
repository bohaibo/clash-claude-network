import { icon } from '../icons.js';
import * as ui from '../ui.js';

let saving = false;
let revision = 0;
let currentCtx = null;
export function hasDraft() { return draft.dirty || saving; }

const draft = { engine: '', language: '', dirty: false };

export function render(ctx) {
  currentCtx = ctx;
  const state = ctx.state;
  const browser = state.browser || {};
  const settings = browser.settings || {};
  if (!draft.engine || (!draft.dirty && !saving)) {
    draft.engine = settings.engine || 'auto';
    draft.language = settings.language || 'zh-SG';
  }
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    eyebrow: 'SANDBOX',
    title: '浏览器沙箱',
    description: 'Claude 使用独立的浏览器资料目录启动。语言和登录数据保留在独立资料中；WebRTC 保护也会通过机器级策略覆盖 Chrome、Edge、Firefox 的普通窗口。',
    actions: [ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) })],
  }));
  root.append(ui.note('这是资料目录级隔离，不是操作系统级沙箱，也不是指纹伪装。仅 WebRTC 策略覆盖受支持的普通浏览器；不会自动关闭浏览器，实际防泄露效果需检测。', 'info'));
  root.append(ui.note('一键配置会按节点地区设置系统时区和沙箱浏览器语言，让 Claude 桌面与沙箱浏览器读取同一时区；语言需重新打开沙箱，系统时区通常需重启。', 'info'));
  root.append(statusCard(ctx, browser));
  root.append(settingsCard(ctx, browser));
  root.append(isolationCard(browser));
  root.append(actionsCard(ctx, browser));
  if (browser.environment?.restricted) {
    root.prepend(ui.note(browser.environment.message, 'bad'));
  }
  return root;
}

function statusCard(ctx, browser) {
  const engine = browser.engine || {};
  const profile = browser.profile || {};
  const def = browser.default || {};
  const defaultLabel = def.is_default ? ui.badge('沙箱为默认', 'ok')
    : def.registered ? ui.badge('已注册，待系统确认', 'warn')
      : ui.badge('否', 'neutral');
  return ui.card({
    title: '独立浏览器',
    subtitle: engine.name ? (engine.name === 'chrome' ? 'Google Chrome' : 'Microsoft Edge') + ' · 独立资料目录' : '未找到可用的 Chrome 或 Edge',
    actions: browser.running ? ui.badge('运行中', 'ok') : ui.badge('未运行', 'neutral'),
  },
  ui.kv([
    ['引擎', engine.name ? (engine.name === 'chrome' ? 'Chrome' : 'Edge') : '不可用'],
    ['资料目录', profile.path ? '.local/' + profile.path.replace(/\\/g, '/') : '.local/browser/profile'],
    ['目录大小', browser.running ? '运行中不计算' : ui.formatBytes(profile.bytes || 0)],
    ['地区环境', browser.region_profile ? (browser.region_profile.label + ' · ' + browser.settings.language + ' · ' + browser.region_profile.iana) : '尚未设置'],
    ['登录状态', profile.exists ? '保留在独立资料目录中' : '尚未创建，首次打开后登录'],
    ['默认浏览器', defaultLabel],
  ]),
  !engine.name ? ui.note('需要安装 Chrome 或 Edge 才能打开沙箱浏览器。本项目不会自动下载浏览器。', 'warn') : null,
  browser.running && browser.pids?.length ? ui.el('p', { class: 'muted small', text: '当前资料进程：' + browser.pids.join(', ') }) : null);
}

function settingsCard(ctx, browser) {
  const available = browser.available || {};
  const engineOptions = [
    { value: 'auto', label: '自动（优先 Chrome，其次 Edge）' },
    { value: 'chrome', label: 'Chrome' + (available.chrome ? '' : '（未安装）') },
    { value: 'edge', label: 'Edge' + (available.edge ? '' : '（未安装）') },
  ];
  const languages = browser.languages || ['zh-SG', 'en-SG', 'zh-CN', 'en-US', 'en-GB', 'ja-JP'];
  const languageOptions = languages.map((value) => ({ value, label: value + (value === 'zh-SG' ? '（新加坡简体）' : value === 'en-SG' ? '（新加坡英语）' : '') }));
  const save = ui.button('保存浏览器设置', {
    variant: 'ink',
    disabled: browser.running || !draft.dirty || ctx.busy || saving,
    onClick: () => saveSettings(ctx),
  });
  save.id = 'save-browser-settings';
  const card = ui.card({ title: '启动设置', subtitle: '修改引擎或语言需要先关闭沙箱浏览器；设置只写独立资料目录。' },
    ui.el('div', { class: 'choice-grid' },
      ui.field('引擎', ui.select(engineOptions, draft.engine, (value) => { draft.engine = value; draft.dirty = true; revision++; markDirty(); }, { disabled: browser.running }), '自动模式只在本机已安装的浏览器中选择。'),
      ui.field('网页语言', ui.select(languageOptions, draft.language, (value) => { draft.language = value; draft.dirty = true; revision++; markDirty(); }, { disabled: browser.running }), '同时写入启动参数和资料首选项；网页的 Intl 默认 locale 仍可能不同。'),
    ),
    ui.el('div', { class: 'form-foot' },
      ui.el('p', { class: 'muted small', text: '固定启用：关闭浏览器独立 DoH、限制 WebRTC 非代理 UDP、关闭 QUIC、禁用浏览器同步与登录。' }),
      save),
    browser.running ? ui.note('沙箱浏览器正在运行，修改引擎或语言前请先关闭。', 'warn') : null);
  card.dataset.preserveKey = 'browser-settings';
  return card;
}

function isolationCard(browser) {
  return ui.card({ title: '隔离与限制', subtitle: '哪些是已实现的行为，哪些仍需自行验证。' },
    ui.el('div', { class: 'two-col' },
      ui.el('div', {},
        ui.el('h3', { text: '已实现' }),
        ui.el('ul', { class: 'check-list' },
          ui.el('li', {}, '独立 --user-data-dir，登录 Cookie 与默认浏览器分开'),
          ui.el('li', {}, '仅 WebRTC 使用可恢复的全浏览器策略，不编辑默认资料'),
          ui.el('li', {}, '启动参数限制 WebRTC 非代理 UDP，关闭浏览器独立 DoH 与 QUIC'),
          ui.el('li', {}, '网络由 Windows TUN 接管，不额外配置浏览器代理'),
          ui.el('li', {}, '可单独关闭、重置，不影响默认浏览器'))),
      ui.el('div', {},
        ui.el('h3', { text: '仍需实测' }),
        ui.el('ul', { class: 'check-list muted' },
          (browser.limitations || []).map((text) => ui.el('li', {}, text))))));
}

function actionsCard(ctx, browser) {
  const engineReady = Boolean(browser.engine?.name);
  const def = browser.default || {};
  const canRegister = def.can_register !== false;
  return ui.card({ title: '操作' },
    ui.el('div', { class: 'action-grid' },
      ui.el('div', { class: 'action-row' },
        ui.el('div', {}, ui.el('strong', { text: '打开沙箱浏览器' }), ui.el('p', { text: '在独立资料目录中打开空白页，不自动访问任何网站。' })),
        ui.button(browser.running ? '再开一个窗口' : '打开沙箱浏览器', { variant: 'ink', icon: 'external', disabled: !engineReady || ctx.busy, onClick: () => openBrowser(ctx) })),
      ui.el('div', { class: 'action-row' },
        ui.el('div', {}, ui.el('strong', { text: '打开环境自检页' }), ui.el('p', { text: '在独立浏览器中打开 IP／地区环境检测和 WebRTC 泄露检测两个网站。' })),
        ui.button('打开自检页', { variant: 'secondary', icon: 'shield', disabled: !engineReady || ctx.busy, onClick: () => openCheck(ctx) })),
      ui.el('div', { class: 'action-row' },
        ui.el('div', {}, ui.el('strong', { text: '关闭沙箱浏览器' }), ui.el('p', { text: '只关闭本项目的资料目录进程，不影响默认浏览器。' })),
        ui.button('关闭', { variant: 'secondary', icon: 'close', disabled: !browser.running || ctx.busy, onClick: () => closeBrowser(ctx) })),
      ui.el('div', { class: 'action-row' },
        ui.el('div', {}, ui.el('strong', { text: '设为默认浏览器' }), ui.el('p', { text: '注册“Claude 沙箱浏览器”并打开 Windows 默认应用设置。默认浏览器打开链接时会使用沙箱资料目录；Windows 要求你手动确认。' })),
        ui.button(def.is_default ? '已是默认浏览器' : '设为默认浏览器', { variant: 'secondary', icon: 'globeLock', disabled: !!def.is_default || !engineReady || !canRegister || ctx.busy, onClick: () => registerDefault(ctx) })),
      !canRegister ? ui.el('p', { class: 'muted small', text: def.restricted_reason || browser.environment?.message || '' }) : null,
      ui.el('div', { class: 'action-row' },
        ui.el('div', {}, ui.el('strong', { text: '重置沙箱资料' }), ui.el('p', { text: '永久删除此资料目录中的登录 Cookie、历史、扩展和设置；无法撤销，不会创建备份。' })),
        ui.button('重置资料', { variant: 'danger', icon: 'trash', disabled: browser.running || ctx.busy, onClick: () => resetBrowser(ctx) })),
    ));
}

function markDirty() {
  const button = document.getElementById('save-browser-settings');
  if (button) button.disabled = !draft.dirty || saving || currentCtx.busy || currentCtx.state.browser?.running;
}

const ENGINE_NAMES = { chrome: 'Chrome', edge: 'Edge' };

async function openBrowser(ctx) {
  return ctx.startJob({ action: 'browser-open', confirm: 'BROWSER-OPEN' });
}

async function openCheck(ctx) {
  return ctx.startJob({ action: 'browser-check', confirm: 'BROWSER-CHECK' });
}

async function closeBrowser(ctx) {
  return ctx.startJob({ action: 'browser-close', confirm: 'BROWSER-CLOSE' });
}

async function registerDefault(ctx) {
  return ctx.startJob({ action: 'browser-register', confirm: 'BROWSER-REGISTER' });
}

async function resetBrowser(ctx) {
  const ok = await ctx.confirm({
    title: '重置沙箱资料？',
    subtitle: '此操作不可撤销',
    description: '将永久删除独立资料目录中的 Claude 登录 Cookie、密码、书签、历史、扩展和设置，不会创建备份，也不会影响默认 Chrome / Edge。',
    confirmText: '永久重置',
    danger: true,
    requireCheck: '我知道这会删除沙箱中的登录状态，且无法撤销。',
  });
  if (!ok) return;
  try {
    await ctx.startJob({ action: 'browser-reset', confirm: 'DELETE' });
  } catch (error) {
    ui.toast(error.message || '重置失败。', 'bad');
  }
}

async function saveSettings(ctx) {
  if (saving || ctx.busy) return;
  saving = true;
  markDirty();
  const sentRevision = revision;
  try {
    const result = await ctx.api.post('/api/browser', { action: 'save-settings', engine: draft.engine, language: draft.language });
    ctx.acceptState('browser', result);
    if (revision === sentRevision) draft.dirty = false;
    ui.toast('浏览器设置已保存。', 'ok');
    await ctx.refresh(true);
  } catch (error) {
    ui.toast(error.message || '保存浏览器设置失败。', 'bad');
  } finally {
    saving = false;
    markDirty();
  }
}
