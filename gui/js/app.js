import * as api from './api.js';
import { icon, logoMark } from './icons.js';
import * as ui from './ui.js';
import { render as overview, refreshStatus as refreshOverview } from './pages/overview.js';
import { render as network } from './pages/network.js';
import { render as snapshots } from './pages/snapshots.js';
import { render as updates, hasDraft as updatesHasDraft } from './pages/updates.js';
import { render as logs } from './pages/logs.js';
import { render as help } from './pages/help.js';
import { render as software } from './pages/software.js';

const PAGES = [
  { id: 'overview', label: '一键配置', icon: 'home', title: '一键配置', render: overview },
  { id: 'network', label: '网络环境', icon: 'network', title: '网络环境', render: network },
  { id: 'snapshots', label: '快照与恢复', icon: 'clock', title: '快照与恢复', render: snapshots },
  { id: 'updates', label: '规则更新', icon: 'refresh', title: '规则更新', render: updates },
  { id: 'software', label: '软件更新', icon: 'download', title: '软件更新与打包', render: software },
  { id: 'logs', label: '日志与验证', icon: 'list', title: '日志与验证', render: logs },
  { id: 'help', label: '使用说明', icon: 'help', title: '使用说明', render: help },
];

const ACTION_LABELS = {
  apply: '配置网络',
  supplement: '补充配置',
  restore: '恢复快照',
  snapshot: '保存快照',
  reconfigure: '恢复最早状态并重新配置',
  update: '更新分流规则',
  verify: '检查连通性',
  audit: '完整防火墙复核与故障演练',
  'settings-recover': '恢复原设置',
};

const app = {
  data: null,
  page: 'overview',
  job: null,
  jobId: null,
  jobTimer: null,
  nodes: null,
  nodesLoading: false,
  pendingLog: '',
  fatal: '',
  jobConnectionError: '',
  startingJob: false,
  stateLoading: false,
};
let shell = null;
let refreshPromise = null;
let forceRefreshPending = false;
let stateRevision = 0;

const ctx = {
  api,
  ui,
  get state() { return app.data; },
  get busy() { return Boolean(app.jobId) || app.startingJob || Boolean(app.data?.operation?.settings_busy); },
  get nodes() { return app.nodes; },
  navigate,
  refresh,
  startJob,
  toast: ui.toast,
  confirm: ui.confirmDialog,
  loadNodes,
  showLog,
  page: () => app.page,
  acceptState(section, value) {
    stateRevision++;
    app.data[section] = value;
  },
};

function h(tag, props, ...children) { return ui.el(tag, props, ...children); }

function bootShell() {
  const nav = h('nav', { class: 'nav', 'aria-label': '主要页面' },
    PAGES.map((page) => h('button', {
      class: 'nav-item',
      type: 'button',
      'data-page': page.id,
      onclick: () => navigate(page.id),
    }, icon(page.icon, 18), h('span', { text: page.label }))));

  const sidebar = h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' },
      logoMark(30),
      h('div', { class: 'brand-text' },
        h('strong', { text: 'Claude 网络' }),
        h('span', { text: 'Windows 网络环境' }))),
    nav,
    h('div', { class: 'sidebar-foot' },
      h('div', { class: 'side-status', id: 'side-status' }),
      h('div', { class: 'side-links' },
        h('button', { class: 'link-btn', type: 'button', onclick: () => showLog('gui/server.log') }, icon('terminal', 15), '界面日志'),
        h('button', { class: 'link-btn', type: 'button', onclick: shutdown }, icon('close', 15), '退出界面')),
      h('p', { class: 'side-note', text: '仅 Windows · 不安装或登录 Claude' })));

  const topbar = h('header', { class: 'topbar' },
    h('div', { class: 'topbar-title' },
      h('span', { class: 'topbar-eyebrow', text: 'CLAUDE NETWORK' }),
      h('h2', { id: 'page-title', text: '一键配置' })),
    h('div', { class: 'topbar-actions' },
      h('div', { class: 'chips', id: 'top-chips' }),
      ui.iconButton('refresh', '刷新状态', () => refresh(true))));

  const content = h('main', { class: 'content', id: 'content', tabindex: '-1' });
  shell = h('div', { class: 'app' },
    sidebar,
    h('div', { class: 'main' }, topbar, h('div', { id: 'banners' }), content),
    h('div', { id: 'job-dock', class: 'job-dock', hidden: true }),
    h('div', { id: 'toasts', class: 'toasts', 'aria-live': 'polite' }));
  document.getElementById('app').replaceWith(shell);
  updateNav();
  updateChrome();
}

function updateNav() {
  for (const button of document.querySelectorAll('.nav-item')) {
    button.classList.toggle('active', button.dataset.page === app.page);
    if (button.dataset.page === app.page) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  const page = PAGES.find((item) => item.id === app.page);
  const title = document.getElementById('page-title');
  if (title && page) title.textContent = page.title;
}

function updateChrome() {
  const data = app.data;
  if (!data) return;
  const verification = data.verification || {};
  const hasVerification = Boolean(verification.available);
  const verified = hasVerification && verification.success && !verification.dns_failed;
  const verifiedAt = verification.time ? new Date(verification.time).getTime() : 0;
  const verificationAge = verifiedAt ? (Date.now() - verifiedAt) / 3600000 : Infinity;
  const verifiedFresh = verified && verificationAge <= 24;
  const lastRun = data.last_run || {};
  const lastRunFailed = lastRun.success === false && Boolean(lastRun.action);
  const statusTone = !data.configured ? 'warn' : lastRunFailed ? 'bad' : verification.dns_failed ? 'bad' : verifiedFresh ? 'ok' : 'warn';
  const statusText = !data.configured ? '尚未配置网络'
    : lastRunFailed ? ('上次' + (ACTION_LABELS[lastRun.action] || '操作') + '未完成')
      : verification.dns_failed ? '配置已写入 · DNS 验收失败'
        : hasVerification && !verification.success ? '配置已写入 · 上次验收失败'
          : verifiedFresh ? '配置已写入 · 验收通过'
            : hasVerification ? '配置已写入 · 验收已过期' : '配置已写入 · 未验收';
  const chips = document.getElementById('top-chips');
  if (chips) {
    chips.replaceChildren(...[
      data.configured
        ? ui.badge(verification.dns_failed ? 'DNS 验收失败' : verifiedFresh ? '已配置' : '配置已写入', statusTone)
        : ui.badge('未配置', 'warn'),
      data.update?.mode === 'scheduled'
        ? ui.badge((Number(data.update.days || 1) === 1 ? '定时更新 ' : '每 ' + (data.update.days || 1) + ' 天 ') + (data.update.time || '13:00'), 'neutral')
        : ui.badge('手动更新', 'neutral'),
      app.jobId ? ui.badge('操作进行中', 'info') : null,
    ].filter(Boolean));
  }
  const side = document.getElementById('side-status');
  if (side) {
    side.replaceChildren(...[
      h('div', { class: 'side-status-row' },
        h('span', { class: 'status-dot ' + statusTone }),
        h('span', { text: statusText })),
      h('small', { text: data.configured ? '节点：' + (data.plan?.node || '—') : '从「一键配置」开始' }),
      data.configured ? h('small', { text: hasVerification ? '上次验收：' + ui.formatTime(verification.time) : '上次验收：无记录' }) : null,
      lastRunFailed ? h('small', { class: 'warn-text', text: '上次操作：' + (ACTION_LABELS[lastRun.action] || lastRun.action) + '未完成' }) : null,
      data.configured && verification.dns_failed ? h('small', { class: 'warn-text', text: 'DNS A/64/65 未通过，存在 DNS 泄漏风险' }) : null,
      data.app?.node === false ? h('small', { class: 'warn-text', text: '未找到 Node.js' }) : null,
    ].filter(Boolean));
  }
  renderBanners();
}

function renderBanners() {
  const host = document.getElementById('banners');
  if (!host) return;
  const rows = [];
  const labels = { preview: '系统设置', update: '规则更新', service: 'Clash 服务' };
  const collecting = Object.entries(app.data?.collection || {}).filter(([, status]) => status.refreshing);
  if (app.stateLoading || collecting.length) {
    rows.push(h('div', { class: 'note configuration-loading', role: 'status', 'aria-live': 'polite', 'aria-busy': 'true' },
      h('span', { class: 'spinner', 'aria-hidden': 'true' }),
      h('span', { text: app.stateLoading ? '正在读取本地配置…' : '正在读取' + collecting.map(([name]) => labels[name] || name).join('、') + '…' })));
  }
  if (!app.data) { host.replaceChildren(...rows); return; }
  const softwareUpdate = app.data.software || {};
  if (softwareUpdate.status === 'available') {
    rows.push(ui.note('发现软件新版本 ' + softwareUpdate.latest_version + '，请到「软件更新」查看。', 'info'));
  }
  for (const [name, status] of Object.entries(app.data.collection || {})) {
    if (!app.stateLoading && !status.refreshing && (status.error || status.stale)) {
      const last = status.read_at ? '上次读取：' + ui.formatTime(status.read_at) + '。' : '尚无成功读取记录。';
      rows.push(ui.note((labels[name] || name) + '：' + (status.error ||
        (status.refreshing ? '正在更新状态。' : '请刷新状态。')) + last, 'warn'));
    }
  }
  if (app.data.operation?.settings_pending?.length) {
    rows.push(ui.note('检测到未完成的设置保存，请到「快照与恢复」恢复原设置后再继续操作。', 'bad'));
  }
  if (app.data.admin) {
    rows.push(ui.note('界面以管理员权限运行，界面浏览器也会继承管理员权限。建议关闭后从普通用户终端重新运行「一键网络.cmd」。', 'warn'));
  }
  if (app.data.configured_error) rows.push(ui.note(app.data.configured_error, 'warn'));
  const lastRun = app.data.last_run || {};
  if (lastRun.success === false && lastRun.action) {
    rows.push(ui.note('上次' + (ACTION_LABELS[lastRun.action] || '操作') + '未完成：' + (lastRun.error || '请查看日志。'), 'bad'));
  }
  const verification = app.data.verification || {};
  const verificationAge = verification.time ? (Date.now() - new Date(verification.time).getTime()) / 3600000 : Infinity;
  if (app.data.configured && verification.dns_failed) {
    rows.push(ui.note('上次网络验收的 DNS A/64/65 未通过，可能存在 DNS 泄漏。请立即重新检查连通性；必要时恢复或重新配置。', 'bad'));
  } else if (app.data.configured && verification.available && verification.success === false) {
    rows.push(ui.note('上次网络验收未通过，请查看失败项目并重新检查。', 'warn'));
  } else if (app.data.configured && !verification.available) {
    rows.push(ui.note('请在「网络环境」完成网络验收。', 'info'));
  } else if (app.data.configured && verification.available && verification.success && verificationAge > 24) {
    rows.push(ui.note('上次网络验收已超过 24 小时；请重新检查 DNS/出口后再依赖当前状态。', 'warn'));
  }
  if (app.data.operation?.pending_firewall) {
    rows.push(ui.note('检测到未完成的防火墙回滚记录。请先完成或恢复处理；新的配置和更新会被阻止。', 'bad'));
  }
  if (app.data.operation?.reconfigure_active) {
    rows.push(ui.note('本机安全重新配置或网络恢复正在执行。请等待独立看护完成后重试。', 'warn'));
  }
  if (app.data.app?.node === false) {
    rows.push(ui.note('未找到项目内 Node.js。请重新运行根目录「一键网络.cmd」，启动器会将所需环境安装或修复到项目内，不修改系统 PATH。', 'warn'));
  }
  if ((app.data.update?.last?.success === false || app.data.update?.last?.skipped) && app.data.update?.last?.time) {
    rows.push(ruleUpdateNotice('上次规则更新未完成：' + (app.data.update.last.reason || app.data.update.last.error || '请查看规则更新页。'), 'warn'));
  }
  if (app.data.update?.mode === 'manual' && app.data.update?.stale) {
    rows.push(ruleUpdateNotice('手动更新模式：上次成功更新已超过 24 小时或尚无记录。', 'info'));
  }
  if (app.data.update?.mode === 'scheduled' && app.data.update?.task?.installed === false && app.data.configured) {
    rows.push(ui.note('定时更新已选择，但当前没有检测到计划任务。请在「规则更新」保存一次，或重新配置以注册任务。', 'warn'));
  }
  host.replaceChildren(...rows);
}

function ruleUpdateNotice(message, tone) {
  return ui.note(h('div', { class: 'note-with-action' },
    h('span', { text: message }),
    ui.button('立即手动更新', {
      variant: 'secondary', size: 'small', icon: 'download',
      disabled: !app.data.configured || ctx.busy,
      title: app.data.configured ? '更新已审核的规则来源，完成后自动刷新结果' : '请先完成网络配置',
      onClick: () => startJob({ action: 'update', confirm: 'UPDATE' }),
    })), tone);
}

function renderPage() {
  const page = PAGES.find((item) => item.id === app.page) || PAGES[0];
  updateNav();
  const content = document.getElementById('content');
  if (!content) return;
  if (!app.data && page.id !== 'help') {
    content.setAttribute('aria-busy', 'true');
    content.replaceChildren(h('div', { class: 'stack startup-loading' },
      ui.pageHeader({ eyebrow: 'LOCAL SETUP', title: page.title, description: '正在准备本机配置，请稍候。' }),
      ui.card({},
        h('div', { class: 'startup-loading-status', role: 'status', 'aria-live': 'polite' },
          h('span', { class: 'spinner large', 'aria-hidden': 'true' }),
          h('div', {}, h('strong', { text: '正在读取本地配置…' }),
            h('p', { class: 'muted', text: '正在检查 Clash、恢复记录和系统设置，首次读取可能需要几秒。' }))),
        h('div', { class: 'startup-skeleton', 'aria-hidden': 'true' },
          h('div', { class: 'skeleton-line' }), h('div', { class: 'skeleton-line short' }),
          h('div', { class: 'skeleton-line' })))));
    return;
  }
  content.removeAttribute('aria-busy');
  try {
    const node = page.render(ctx);
    content.replaceChildren(node);
  } catch (error) {
    console.error(error);
    content.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body' },
      ui.note('页面渲染失败：' + error.message, 'bad'))));
  }
  updateChrome();
  if (app.pendingLog) {
    const name = app.pendingLog;
    app.pendingLog = '';
    if (app.page === 'logs') openLogInline(name);
  }
}

function navigate(page, options = {}) {
  if (!PAGES.some((item) => item.id === page)) page = 'overview';
  app.page = page;
  if (options.log) app.pendingLog = options.log;
  const hash = '#/' + page;
  if (location.hash !== hash) history.replaceState(null, '', hash);
  renderPage();
  document.getElementById('content')?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function refresh(force = false) {
  forceRefreshPending ||= force;
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    do {
      const requestedForce = forceRefreshPending;
      forceRefreshPending = false;
      await refreshState(requestedForce);
    } while (forceRefreshPending && !app.fatal);
  })().finally(() => { refreshPromise = null; });
  return refreshPromise;
}

// Keep form nodes, focus, selections and open logs while replacing status cards.
function refreshPage() {
  const content = document.getElementById('content');
  const stack = content?.firstElementChild;
  if (!stack || !stack.classList.contains('stack') || stack.classList.contains('startup-loading')) {
    renderPage();
    return;
  }
  if (app.page === 'help') return;
  if (app.page === 'overview') { refreshOverview(ctx); return; }
  const preserveForm = (app.page === 'updates' && updatesHasDraft());
  const keep = [...stack.querySelectorAll('[data-preserve-key]')]
    .filter(node => node.dataset.preserveKey === 'open-log' || preserveForm);
  const active = document.activeElement;
  const scroll = window.scrollY;
  renderPage();
  for (const node of keep) {
    const replacement = content.querySelector('[data-preserve-key="' + node.dataset.preserveKey + '"]');
    if (!replacement) continue;
    for (const button of node.querySelectorAll('button[id]')) {
      const next = replacement.querySelector('#' + button.id);
      if (next) button.disabled = next.disabled;
    }
    for (const [index, control] of [...node.querySelectorAll('input,select,textarea')].entries()) {
      const next = replacement.querySelectorAll('input,select,textarea')[index];
      if (next) control.disabled = next.disabled;
    }
    replacement.replaceWith(node);
  }
  if (active?.isConnected) active.focus({ preventScroll: true });
  window.scrollTo(0, scroll);
}

async function refreshState(force = false) {
  app.stateLoading = true;
  renderBanners();
  try {
    const revision = stateRevision;
    const data = await api.getState(force);
    if (revision !== stateRevision) return;
    app.data = data;
    updateChrome();
    if (data.operation?.job && !app.jobId) resumeJob(data.operation.job.id);
    refreshPage();
  } catch (error) {
    if (error instanceof api.SessionError) {
      renderFatal(error.message);
    } else {
      ui.toast(error.message || '读取状态失败。', 'bad');
      const content = document.getElementById('content');
      if (content && !app.data && app.page !== 'help') {
        content.removeAttribute('aria-busy');
        content.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body' },
          ui.note(error.message || '读取本地状态失败。请检查 .local/gui/server.log。', 'bad'),
          ui.button('重试', { variant: 'ink', onClick: () => refresh(true) }))));
      }
    }
  } finally {
    app.stateLoading = false;
    renderBanners();
  }
}

async function loadNodes(force = false) {
  if (app.nodesLoading) return app.nodes;
  if (app.nodes && !force) return app.nodes;
  app.nodesLoading = true;
  try {
    app.nodes = await api.getNodes(force);
    return app.nodes;
  } catch (error) {
    ui.toast(error.message || '读取订阅失败。', 'bad');
    throw error;
  } finally {
    app.nodesLoading = false;
  }
}

async function startJob(payload) {
  if (ctx.busy) {
    ui.toast('另一个操作正在进行，请等待完成。', 'warn');
    return;
  }
  app.startingJob = true;
  updateChrome();
  try {
    const job = await api.startJob(payload);
    app.jobId = job.id;
    app.job = job;
    app.jobConnectionError = '';
    renderJobDock();
    updateChrome();
    pollJob();
  } catch (error) {
    ui.toast(error.message || '无法开始操作。', 'bad');
  } finally {
    app.startingJob = false;
    updateChrome();
  }
}

function resumeJob(id) {
  if (app.jobId === id) return;
  app.jobId = id;
  pollJob();
}

async function finishJob(job) {
  app.jobId = null;
  clearTimeout(app.jobTimer);
  updateChrome();
  if (job.state === 'success' && job.action === 'update' && job.result?.skipped) {
    ui.toast(job.result?.reason || '规则更新已跳过：订阅或脚本关联已变化。', 'warn', 8000);
  } else if (job.state === 'success') ui.toast(ACTION_LABELS[job.action] + '已完成。', 'ok', 6000);
  else if (job.state === 'cancelled') ui.toast(job.error || '操作已取消，请检查日志确认当前状态。', 'warn', 6000);
  else ui.toast(job.error || '操作未完成，请查看日志。', 'bad', 9000);
  // Do not await an existing refresh which may itself have resumed this job.
  refresh(true);
}

async function pollJob() {
  if (!app.jobId) return;
  clearTimeout(app.jobTimer);
  const id = app.jobId;
  try {
    const job = await api.getJob(id);
    if (id !== app.jobId) return;
    app.job = job;
    app.jobConnectionError = '';
    renderJobDock();
    updateChrome();
    if (job.state === 'running') {
      app.jobTimer = setTimeout(pollJob, 900);
      return;
    }
    await finishJob(job);
  } catch (error) {
    if (id !== app.jobId) return;
    if (error instanceof api.SessionError) {
      clearTimeout(app.jobTimer);
      renderFatal(error.message);
      return;
    }
    if (error.status === 404) {
      app.jobId = null;
      app.job = null;
      renderJobDock();
      updateChrome();
      ui.toast('任务记录不存在，无法确认操作结果。请查看日志和当前设置。', 'warn', 9000);
      refresh(true);
      return;
    }
    app.jobConnectionError = '连接中断，暂时无法确认任务状态；正在重试。请勿重复操作。';
    renderJobDock();
    app.jobTimer = setTimeout(pollJob, 3000);
  }
}

function jobStep(job) {
  if (!job || !Array.isArray(job.events)) return null;
  for (let index = job.events.length - 1; index >= 0; index -= 1) {
    if (job.events[index].type === 'step') return job.events[index];
  }
  return null;
}

function renderJobDock() {
  const dock = document.getElementById('job-dock');
  if (!dock) return;
  const job = app.job;
  if (!job && app.jobConnectionError) {
    dock.hidden = false;
    dock.replaceChildren(ui.note(app.jobConnectionError, 'warn'));
    return;
  }
  if (!job) {
    dock.hidden = true;
    dock.replaceChildren();
    return;
  }
  dock.hidden = false;
  const step = jobStep(job);
  const running = job.state === 'running';
  const total = step?.total || 0;
  const index = step?.index || 0;
  const progress = total ? Math.min(100, Math.round((index / total) * 100)) : 0;
  const stateTone = running ? 'info' : job.state === 'success' ? 'ok' : job.state === 'cancelled' ? 'warn' : 'bad';
  const stateLabel = running ? '进行中' : job.state === 'success' ? '已完成' : job.state === 'cancelled' ? '已取消' : '未完成';
  const timeline = (job.events || []).filter((event) => ['elevation', 'step', 'done', 'error', 'cancelled'].includes(event.type)).slice(-8);
  const details = [];
  for (const item of timeline) {
    if (!running && job.error && item.message === job.error) continue;
    if (item.type === 'step') details.push(h('div', { class: 'job-event' },
      h('span', { class: 'job-event-index', text: String(item.index || '') }),
      h('div', {}, h('strong', { text: item.title || '' }), item.note ? h('small', { text: item.note }) : null)));
    else if (item.type === 'elevation' && running && !step) details.push(h('div', { class: 'job-event muted', text: item.message || '等待管理员授权…' }));
    else if (item.type === 'done') details.push(h('div', { class: 'job-event ok', text: item.message || '已完成。' }));
    else if (item.type === 'error') details.push(h('div', { class: 'job-event bad', text: item.message || '操作未完成。' }));
    else if (item.type === 'cancelled') details.push(h('div', { class: 'job-event warn', text: item.message || '已取消。' }));
  }
  const logDetails = job.log ? h('details', { class: 'job-log' },
    h('summary', { text: '控制台输出' }),
    h('pre', { class: 'log-block small' }, job.log)) : null;
  const actions = [];
  if (running && job.stale) {
    actions.push(h('span', { class: 'job-hint bad-text', text: '任务已结束但界面没有收到退出信号，进程可能卡住。' }));
    actions.push(ui.button('结束卡住任务', { variant: 'danger', size: 'small', icon: 'close', onClick: () => forceCancelJob(job) }));
  } else if (running) {
    actions.push(h('span', { class: 'job-hint', text: '请勿关闭此窗口；管理员授权窗口可能已经弹出。' }));
  } else {
    actions.push(ui.button('查看日志', { variant: 'secondary', size: 'small', icon: 'list', onClick: () => showLog('gui/jobs/' + job.id + '/console.log') }));
    actions.push(ui.button('关闭', { variant: 'ghost', size: 'small', onClick: () => { app.job = null; renderJobDock(); } }));
  }
  dock.replaceChildren(h('div', { class: 'job-card' },
    h('div', { class: 'job-head' },
      h('div', {},
        h('span', { class: 'job-kicker', text: ACTION_LABELS[job.action] || job.action }),
        h('strong', { text: step ? step.title : running ? '准备中…' : stateLabel })),
      h('div', { class: 'job-head-right' },
        ui.badge(stateLabel, stateTone),
        ui.iconButton('close', running ? '操作进行中，不能收起' : '收起', () => { app.job = null; renderJobDock(); }, { size: 16, disabled: running }))),
    h('div', { class: 'progress' }, h('div', { class: 'progress-bar ' + (running ? '' : stateTone), style: { width: progress + '%' } })),
    h('div', { class: 'job-meta' },
      h('span', { text: total ? '第 ' + Math.min(index, total) + ' / ' + total + ' 步' : '等待进度' }),
      h('span', { text: '已用时 ' + (job.elapsed || 0) + ' 秒' }),
      job.exit_code !== null && job.exit_code !== undefined ? h('span', { text: '退出码 ' + job.exit_code }) : null),
    details.length ? h('div', { class: 'job-events' }, details) : null,
    job.error && !running ? h('div', { class: 'job-error' }, ui.note(job.error, 'bad')) : null,
    job.record_warning ? ui.note(job.record_warning, 'warn') : null,
    app.jobConnectionError ? ui.note(app.jobConnectionError, 'warn') : null,
    logDetails,
    h('div', { class: 'job-actions' }, actions)));
}

async function forceCancelJob(job) {
  const ok = await ui.confirmDialog({
    title: '结束卡住的任务？',
    description: (ACTION_LABELS[job.action] || '本次操作') + '可能尚未完成，结束进程不会回滚已写入的设置。请先查看日志，确认是否需要恢复或重试。',
    confirmText: '结束任务',
    danger: true,
  });
  if (!ok) return;
  try {
    const value = await api.cancelJob(job.id);
    app.job = value;
    app.jobId = null;
    clearTimeout(app.jobTimer);
    renderJobDock();
    updateChrome();
    ui.toast(value.state === 'success' ? '操作已经完成，无需结束或重新恢复。' : '任务状态已更新；请检查日志后再决定是否恢复或重试。', value.state === 'success' ? 'ok' : 'warn', 8000);
    await refresh(true);
  } catch (error) {
    ui.toast(error.message || '结束任务失败；请从任务管理器结束卡住的 powershell.exe。', 'bad', 9000);
  }
}

function showLog(name) {
  if (app.page !== 'logs') {
    app.pendingLog = name;
    navigate('logs', { log: name });
  } else {
    app.pendingLog = '';
    openLogInline(name);
  }
}

async function openLogInline(name) {
  const host = document.getElementById('log-inline');
  if (!host) {
    app.pendingLog = name;
    return;
  }
  host.replaceChildren(h('div', { class: 'loading-inline' }, h('span', { class: 'spinner' }), '正在读取日志…'));
  try {
    const value = await api.getLog(name);
    host.replaceChildren(
      h('div', { class: 'log-head' }, h('strong', { text: value.name }), ui.button('复制', { variant: 'ghost', size: 'small', onClick: () => ui.copyText(value.text) })),
      h('pre', { class: 'log-block' }, value.text || '空日志。'));
  } catch (error) {
    host.replaceChildren(ui.note(error.message || '读取日志失败。', 'bad'));
  }
}

async function shutdown() {
  if (ctx.busy) {
    ui.toast('操作仍在进行，完成后再退出界面。', 'warn');
    return;
  }
  const ok = await ui.confirmDialog({
    title: '退出界面？',
    description: '只会关闭界面和隔离的界面浏览器资料进程；不会修改、恢复或停止网络配置，也不会关闭外部浏览器。',
    confirmText: '退出界面',
    danger: true,
  });
  if (!ok) return;
  try {
    await api.post('/api/shutdown');
    document.body.classList.add('shutting-down');
    ui.toast('界面服务正在退出，可以关闭此窗口。', 'ok');
    setTimeout(() => window.close(), 1200);
  } catch (error) {
    ui.toast(error.message || '退出失败。', 'bad');
  }
}

function renderFatal(message) {
  app.fatal = message || '会话已失效';
  clearTimeout(app.jobTimer);
  document.body.replaceChildren(h('div', { class: 'fatal' },
    logoMark(36),
    h('h1', { text: '无法连接本地界面服务' }),
    h('p', { text: message || '界面会话已失效。' }),
    h('p', { class: 'muted', text: '请关闭此窗口，重新运行项目根目录的「一键网络.cmd」。' }),
    ui.button('重试', { variant: 'ink', onClick: () => location.reload() })));
}

async function init() {
  try {
    await api.bootstrap();
  } catch (error) {
    renderFatal(error.message);
    return;
  }
  bootShell();
  const initial = (location.hash || '#/overview').replace('#/', '');
  app.page = PAGES.some((item) => item.id === initial) ? initial : 'overview';
  renderPage();
  await refresh(true);
  window.scrollTo(0, 0);
  setInterval(() => { api.heartbeat().catch(() => {}); }, 8000);
  setInterval(() => { if (!app.fatal && !app.jobId && document.visibilityState === 'visible') refresh(false); }, 9000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      api.heartbeat().catch(() => {});
      refresh(false);
    }
  });
  window.addEventListener('hashchange', () => {
    const id = (location.hash || '#/overview').replace('#/', '');
    if (id !== app.page && PAGES.some((item) => item.id === id)) {
      app.page = id;
      renderPage();
    }
  });
}

init();
