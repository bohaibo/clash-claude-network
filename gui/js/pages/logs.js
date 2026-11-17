import { icon } from '../icons.js';
import * as ui from '../ui.js';

export function render(ctx) {
  const state = ctx.state;
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '日志与验证',
    actions: [
      ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) }),
      ui.button('打开 .local', { variant: 'ghost', icon: 'folder', onClick: () => reveal(ctx) }),
    ],
  }));
  const tun = state.operation?.pending_tun || [];
  if (tun.length) root.append(ui.card({title: 'TUN 演练待恢复'},
    ui.note('独立看护正在等待或尝试恢复。待恢复标记消失前暂停项目操作；若失败或长时间未结束，请检查下方目录中的 recovery.json、watchdog.log 和本次验收记录。不要删除 ready/done 标记绕过保护。', 'bad'),
    ...tun.map(row => ui.kv([['记录', row.id], ['状态', row.status === 'failed' ? '已报告恢复失败' : '恢复尚未确认'], ['本地目录', '.local/tun-drills/' + row.id]])),
    ui.button('打开 .local 查看记录', {variant: 'secondary', onClick: () => reveal(ctx)})));
  root.append(lastRunCard(ctx));
  root.append(verificationCard(ctx));
  root.append(logListCard(ctx));
  if (state.reconfigure_runs?.length) root.append(reconfigureCard(ctx));
  const inline = document.createElement('div');
  inline.id = 'log-inline';
  inline.dataset.preserveKey = 'open-log';
  inline.className = 'log-inline';
  root.append(inline);
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
    return ui.card({ title: '网络验收', help: '配置不自动运行验收；请在网络环境页面检查或演练。' }, ui.el('p', { class: 'muted', text: '还没有验收记录' }));
  }
  const coverage = verification.coverage;
  const complete = coverage === 'full-network';
  const scope = complete ? '完整网络验收' : coverage === 'connectivity' ? '连通性检查' : coverage === 'partial' ? '部分阶段检查' : '旧版检查（范围待核对）';
  const outcome = !verification.success ? '未通过' : complete || coverage === 'connectivity' ? '通过' : '已记录阶段通过，验收不完整';
  return ui.disclosure({ title: '历史' + scope + ' · ' + outcome, key: 'logs-verification', open: !verification.success || !complete },
    !complete ? ui.note('此记录不代表完整网络验收。' + (coverage === 'connectivity' ? '仅检查连通性，未进行完整故障演练。' : '请核对已测试与缺失阶段。'), 'warn') : null,
    ui.el('div', { class: 'verification-head' },
      ui.el('span', { class: 'muted', text: '记录时间 ' + ui.formatTime(verification.time) }),
      verification.egress?.country ? ui.pill(verification.egress.country + ' · ' + (verification.egress.city || ''), 'neutral') : null),
    ui.el('div', { class: 'check-grid' }, (verification.checks || []).map((check) => ui.el('div', { class: 'check-item' },
      ui.el('span', { class: 'check-mark ' + ui.statusTone(check.status) }, check.status === 'passed' ? icon('check', 14) : check.status === 'failed' ? icon('close', 14) : icon('info', 14)),
      ui.el('div', {}, ui.el('strong', { text: check.title }), ui.el('small', { text: ui.statusText(check.status) }))))),
    ui.kv((verification.stages || []).map(stage => [stage.title || stage.id, !stage.tested ? '未测试' : stage.success ? '通过' : stage.status === 'unverified' ? '未验证' : '未通过'])),
    ...(verification.not_tested || []).map(text => ui.note('未测试：' + text, 'info')),
    ui.el('p', { class: 'muted small', text: '原始 IP 与探测明细只保存在 .local；界面只显示国家/城市摘要。' }));
}

function logListCard(ctx) {
  const files = ctx.state.log_files || [];
  const body = files.length
    ? ui.el('div', { class: 'log-list' }, files.map((file) => ui.el('button', {
        class: 'log-row', type: 'button', onclick: () => ctx.showLog(file.name),
      },
      ui.el('span', { class: 'log-icon' }, icon('list', 16)),
      ui.el('span', { class: 'log-name', text: file.name }),
      ui.el('span', { class: 'muted', text: ui.formatBytes(file.bytes) }),
      ui.el('span', { class: 'muted', text: ui.relativeTime(file.time) }),
      icon('chevron', 14))))
    : ui.empty('还没有日志文件', '操作产生错误或更新结果时会生成日志。');
  return ui.card({ title: '本地日志', help: '点击文件名在此读取；不会把日志上传到网络。' }, body);
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
    supplement: '补充配置', reconfigure: '恢复并重新配置', update: '更新规则', verify: '连通性检查', audit: '完整复核与故障演练',
  }[action] || action;
}

async function reveal(ctx) {
  try {
    await ctx.api.post('/api/reveal');
    ui.toast('已打开本地数据目录。', 'ok');
  } catch (error) {
    ui.toast(error.message || '无法打开目录。', 'bad');
  }
}
