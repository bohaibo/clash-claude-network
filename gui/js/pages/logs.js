import { icon } from '../icons.js';
import * as ui from '../ui.js';

export function render(ctx) {
  const state = ctx.state;
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '日志与验证',
    actions: [
      ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: ctx.refreshManually }),
      ui.button('打开 .local', { variant: 'ghost', icon: 'folder', onClick: () => reveal(ctx) }),
    ],
  }));
  const tun = state.operation?.pending_tun || [];
  if (tun.length) root.append(ui.card({title: tun.some(row => row.kind) ? '故障演练待恢复' : 'TUN 演练待恢复'},
    ui.note('演练恢复尚未完成，其他操作已暂时暂停。请等待自动恢复，或查看下方目录记录。', 'bad'),
    ...tun.map(row => ui.el('section', {}, ui.kv([['记录', row.id], ['阶段', ({'node-outage': '节点故障', 'core-exit': '停核'})[row.kind] || 'TUN 关闭'], ['状态', row.status === 'failed' ? '已报告恢复失败' : row.status === 'interrupted' ? '看护已停止，恢复尚未确认' : '恢复尚未确认'], ['本地目录', '.local/tun-drills/' + row.id]]),
      ui.el('div', {class: 'page-actions'},
        ui.button('打开本次记录', {variant: 'secondary', onClick: () => reveal(ctx, row.id)}),
        row.status === 'failed' && row.retryable === false && !row.kind ? ui.el('span', {class: 'muted small', text: '该记录缺少原活动配置，无法安全重试。'}) : null,
        (row.retryable ?? row.status === 'failed') ? ui.button('重试本次恢复', {variant: 'ink', disabled: ctx.busy,
          onClick: () => retryDrill(ctx, row.id)}) : null)))));
  root.append(lastRunCard(ctx));
  root.append(verificationCard(ctx));
  const inline = document.createElement('div');
  inline.id = 'log-inline';
  inline.dataset.preserveKey = 'open-log';
  inline.className = 'log-inline';
  inline.tabIndex = -1;
  root.append(logListCard(ctx, inline));
  if (state.reconfigure_runs?.length) root.append(reconfigureCard(ctx));
  return root;
}

function lastRunCard(ctx) {
  const run = ctx.state.last_run || {};
  const tone = run.success ? 'ok' : run.error || run.status === 'partial' ? 'bad' : 'neutral';
  return ui.card({ title: '上次操作', actions: run.action ? ui.badge(run.success ? '成功' : '未完成', tone) : null },
    run.action ? ui.kv([
      ['动作', actionName(run.action)],
      ...(run.status ? [['状态', run.status]] : []),
    ]) : ui.empty('还没有操作记录', '从「一键配置」开始，或在此查看上次运行结果。'),
    run.error ? ui.note(run.error + '。详情见 .local/last-error.log 或对应操作日志。', 'bad') : null);
}

function verificationCard(ctx) {
  const verification = ctx.state.verification || {};
  if (!verification.available) {
    return ui.card({ title: '网络验收', help: '一键配置完成后自动验收，也可在「网络环境」手动运行完整网络验收。' }, ui.el('p', { class: 'muted', text: '还没有验收记录' }));
  }
  const coverage = verification.coverage;
  const complete = coverage === 'full-network';
  return ui.disclosure({ title: ui.verificationLabel(verification), key: 'logs-verification', open: !verification.success || !complete },
    !complete ? ui.note('此记录不代表完整网络验收。' + (coverage === 'connectivity' ? '仅检查连通性，未进行完整故障演练。' : '请核对已测试与缺失阶段。'), 'warn') : null,
    ui.el('div', { class: 'verification-head' },
      ui.el('span', { class: 'muted', text: '记录时间 ' + ui.formatTime(verification.time) + ' · ' + ui.verificationRelation(ctx.state) }),
      verification.egress?.country ? ui.pill(verification.egress.country + ' · ' + (verification.egress.city || ''), 'neutral') : null),
    ui.el('div', { class: 'check-grid' }, (verification.checks || []).map((check) => ui.el('div', { class: 'check-item' },
      ui.el('span', { class: 'check-mark ' + ui.statusTone(check.status) }, check.status === 'passed' ? icon('check', 14) : check.status === 'failed' ? icon('close', 14) : icon('info', 14)),
      ui.el('div', {}, ui.el('strong', { text: check.title }), ui.el('small', { text: ui.statusText(check.status) }))))),
    ui.kv((verification.stages || []).map(stage => [stage.title || stage.id, !stage.tested ? '未测试' : stage.success ? '通过' : stage.status === 'unverified' ? '未验证' : '未通过'])),
    ...(verification.not_tested || []).map(text => ui.note('未测试：' + text, 'info')),
    ui.el('p', { class: 'muted small', text: '详细探测记录保存在本地 .local 目录。' }));
}

function logListCard(ctx, inline) {
  const files = ctx.state.log_files || [];
  let listArea;
  if (files.length) {
    const list = ui.el('div', { class: 'log-list' });
    const renderRows = (query = '') => {
      const needle = query.trim().toLowerCase();
      const matched = needle ? files.filter(file => file.name.toLowerCase().includes(needle)) : files;
      if (!matched.length) {
        list.replaceChildren(ui.el('p', { class: 'muted pad', text: '没有匹配的日志文件。' }));
        return;
      }
      const active = (typeof document !== 'undefined' ? document.getElementById?.('log-inline')?.dataset?.activeLog : null) ?? inline.dataset?.activeLog;
      list.replaceChildren(...matched.map((file) => ui.el('button', {
        class: 'log-row', type: 'button', 'data-log-name': file.name,
        'aria-pressed': String(Boolean(active && active === file.name)),
        onclick: () => ctx.showLog(file.name),
      },
      ui.el('span', { class: 'log-icon' }, icon('list', 16)),
      ui.el('span', { class: 'log-name', text: file.name }),
      ui.el('span', { class: 'muted', text: ui.formatBytes(file.bytes) }),
      ui.el('span', { class: 'muted', text: ui.relativeTime(file.time) }),
      icon('chevron', 14))));
    };
    renderRows();
    if (files.length > 5) {
      const filter = ui.input('', {
        type: 'search', class: 'input log-filter', placeholder: '筛选日志文件名…',
        'aria-label': '筛选日志文件',
        oninput: (event) => renderRows(event.target.value),
      });
      listArea = ui.el('div', {}, filter, list);
    } else {
      listArea = list;
    }
  } else {
    listArea = ui.empty('还没有日志文件', '操作产生错误或更新结果时会生成日志。');
  }
  return ui.card({ title: '本地日志', help: '点击文件名即可查看，所有日志仅保存在本地。' }, ui.el('div', {class: 'log-layout'}, listArea, inline));
}

function reconfigureCard(ctx) {
  const runs = ctx.state.reconfigure_runs || [];
  if (!runs.length) {
    return ui.card({ title: '安全重新配置记录' }, ui.empty('还没有记录', '「恢复最早状态并重新配置」会在 .local/reconfigure-runs 保存阶段和恢复结果。'));
  }
  return ui.disclosure({ title: '重新配置记录', key: 'logs-reconfigure', open: runs.some(run => !run.success) },
    ui.el('div', { class: 'run-list' }, runs.map((run) => ui.el('div', { class: 'run-row' },
      ui.el('span', { class: 'status-dot ' + (run.success ? 'ok' : run.stage === 'failed' ? 'bad' : 'warn') }),
      ui.el('div', { class: 'run-main' },
        ui.el('strong', { text: run.id }),
        ui.el('small', { text: (run.stage || '未知阶段') + ' · ' + ui.formatTime(run.time) + (run.recovered ? ' · 已尝试自动恢复' : '') })),
      run.error ? ui.el('span', { class: 'muted small', text: run.error }) : null))));
}

function actionName(action) {
  return {
    apply: '配置网络', restore: '恢复快照', snapshot: '保存快照',
    supplement: '补充配置', reconfigure: '恢复并重新配置', update: '更新规则', verify: '连通性检查', audit: '完整网络验收',
  }[action] || action;
}

async function retryDrill(ctx, id) {
  const ok = await ctx.confirm({
    title: '重试本次演练恢复？',
    description: '将申请管理员权限并恢复本次演练的临时改动，期间网络可能短暂断开。',
    confirmText: '重试恢复',
  });
  if (ok) ctx.startJob({action: 'drill-recover', recovery: id, confirm: 'RECOVER-DRILL'});
}

async function reveal(ctx, recovery) {
  try {
    await ctx.api.post('/api/reveal', recovery ? {recovery} : {});
    ui.toast('已打开本地数据目录。', 'ok');
  } catch (error) {
    ui.toast(error.message || '无法打开目录。', 'bad');
  }
}
