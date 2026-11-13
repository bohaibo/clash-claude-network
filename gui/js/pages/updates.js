import { icon } from '../icons.js';
import * as ui from '../ui.js';

let saving = false;
let revision = 0;
let currentCtx = null;
export function hasDraft() { return draft.dirty || saving; }

const draft = { mode: '', time: '', days: 1, dirty: false };

export function render(ctx) {
  currentCtx = ctx;
  const state = ctx.state;
  const update = state.update || {};
  if (!draft.mode || (!draft.dirty && !saving)) {
    draft.mode = update.mode || 'scheduled';
    draft.time = update.time || '13:00';
    draft.days = Number(update.days) || 1;
  }
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    eyebrow: 'RULES',
    title: '规则更新',
    description: '统一使用 Loyalsoldier 国内域名、国内 IP、私有域名和局域网规则。可手动更新，或让 Windows 在登录期间按固定时间更新；不更新内核或节点。',
    actions: [ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: () => ctx.refresh(true) })],
  }));
  root.append(modeCard(ctx, update));
  root.append(statusCard(ctx, update));
  root.append(lastResultCard(ctx, update));
  root.append(ui.card({ title: '边界与审核', subtitle: '自动更新不等于无条件接受远程变化。' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只更新项目配置中已审核的国内域名、国内 IP、私有域名和 LAN 地址来源。'),
      ui.el('li', {}, '来源发生较大变化、私有/LAN 内容变化或 Claude 策略变化时会停止，保留旧配置等待人工审核。'),
      ui.el('li', {}, '首次切换来源需点击「立即更新」；定时任务不会自动扩大到新来源。海外优先规则与对应 DNS 策略保持同步。'),
      ui.el('li', {}, '先校验完整候选配置，再发布并核对内核加载数量；失败会尝试恢复，恢复未完成时会明确提示。'),
      ui.el('li', {}, '不更新 Mihomo 内核、不添加节点、不改变固定节点组。'),
      ui.el('li', {}, '定时任务只在当前 Windows 用户登录期间运行；电脑关机或未登录时不会执行。'),
      ui.el('li', {}, '恢复快照会移除本项目的定时任务；需要时请在这里重新保存定时方式。'))));
  return root;
}

function modeCard(ctx, update) {
  const configured = ctx.state.configured;
  const option = (value, title, description, iconName) => ui.el('button', {
    class: 'mode-card' + (draft.mode === value ? ' selected' : ''),
    type: 'button',
    onclick: () => { draft.mode = value; draft.dirty = true; revision++; rerenderMode(ctx); },
  },
  ui.el('span', { class: 'mode-icon' }, icon(iconName, 20)),
  ui.el('div', {}, ui.el('strong', { text: title }), ui.el('p', { text: description })),
  ui.el('span', { class: 'mode-check' }, draft.mode === value ? icon('check', 15) : null));

  const timeInput = ui.input(draft.time, {
    type: 'time',
    disabled: draft.mode !== 'scheduled',
    oninput: (event) => { draft.time = event.target.value; draft.dirty = true; revision++; updateSaveState(); },
  });
  const presets = [[1, '每天'], [3, '每 3 天'], [7, '每周'], [30, '每月'], [180, '每半年']];
  const custom = !presets.some(([value]) => value === Number(draft.days));
  const frequency = ui.select(
    [...presets.map(([value, label]) => ({ value: String(value), label })), { value: 'custom', label: '自定义天数' }],
    custom ? 'custom' : String(draft.days),
    (value) => {
      if (value === 'custom') { draft.days = Number(draft.days) || 7; }
      else { draft.days = Number(value); }
      draft.dirty = true; revision++;
      rerenderMode(ctx);
      updateSaveState();
    },
    { disabled: draft.mode !== 'scheduled' },
  );
  const daysInput = ui.input(String(draft.days), {
    type: 'number', min: 1, max: 365, disabled: draft.mode !== 'scheduled',
    oninput: (event) => {
      draft.days = event.target.value;
      draft.dirty = true; revision++;
      updateSaveState();
    },
  });
  const save = ui.button('保存更新方式', { variant: 'ink', disabled: !draft.dirty || ctx.busy || saving, onClick: () => saveSettings(ctx) });
  save.id = 'save-update-settings';
  const card = ui.card({ title: '更新方式', subtitle: configured ? '保存后会立即调整 Windows 计划任务。' : '尚未配置网络；此设置会在首次配置时生效。' },
    ui.el('div', { class: 'mode-grid' },
      option('manual', '手动更新', '不注册后台任务。打开软件后在「规则更新」点击立即更新；不会在后台自动联网。', 'clock'),
      option('scheduled', '定时更新', '注册当前用户的 Windows 计划任务：每隔 1–365 天，在指定时间运行。', 'refresh')),
    ui.el('div', { class: 'time-row' },
      ui.el('div', { class: 'field time-field' }, ui.el('span', { class: 'field-label', text: '更新时间' }), timeInput),
      ui.el('div', { class: 'field' }, ui.el('span', { class: 'field-label', text: '更新间隔' }), frequency),
      ui.el('div', { class: 'field days-field' }, ui.el('span', { class: 'field-label', text: '间隔天数' }), daysInput),
      save),
    draft.mode === 'scheduled' && configured && update.task?.installed === false
      ? ui.note('当前方式为定时，但尚未检测到计划任务。保存一次即可注册；配置流程也会按此方式注册。', 'warn')
      : null,
    draft.mode === 'scheduled' && (!Number.isInteger(Number(draft.days)) || Number(draft.days) < 1 || Number(draft.days) > 365)
      ? ui.note('更新间隔必须是 1–365 天。', 'bad')
      : null,
    draft.mode === 'manual' && update.task?.installed
      ? ui.note('当前方式为手动，但检测到旧的计划任务。保存后会移除该任务。', 'warn')
      : null);
  card.dataset.preserveKey = 'update-settings';
  return card;
}

function rerenderMode(ctx) {
  // Re-render only the mode card's selected state and time field without losing page scroll.
  const state = ctx.state;
  const root = document.querySelector('.stack');
  if (!root) return;
  const old = root.querySelector('[data-preserve-key="update-settings"]');
  if (!old) return;
  const fresh = modeCard(ctx, state.update || {});
  old.replaceWith(fresh);
}

function statusCard(ctx, update) {
  const task = update.task || {};
  const configured = ctx.state.configured;
  const taskBadge = task.installed === true ? ui.badge('已注册', 'ok') : task.installed === false ? ui.badge('未注册', 'neutral') : ui.badge('无法读取', 'warn');
  return ui.card({
    title: '计划任务',
    subtitle: '任务只运行 update.py，使用当前用户权限，不提升权限。',
    actions: taskBadge,
  },
  ui.kv([
    ['任务状态', task.state || (task.installed ? '已安装' : '未安装')],
    ['更新时间', task.time || (task.installed ? '未知' : '—')],
    ['更新间隔', task.days ? (Number(task.days) === 1 ? '每天' : '每 ' + task.days + ' 天') : '—'],
    ['下次运行', task.next_run ? ui.formatTime(task.next_run) : '—'],
    ['当前设置', (update.mode === 'scheduled' ? (Number(update.days || 1) === 1 ? '每天 ' : '每 ' + (update.days || 1) + ' 天 ') + (update.time || '13:00') : '手动')],
    ['配置状态', configured ? '已生效，可立即更新' : '尚未配置，更新会停止'],
  ]),
  task.error ? ui.note(task.error, 'warn') : null,
  ui.el('div', { class: 'form-foot' },
    ui.el('p', { class: 'muted small', text: '定时任务只在登录期间运行；不会在锁屏前强制唤醒电脑。' }),
    ui.button('立即更新', { variant: 'secondary', icon: 'download', disabled: !configured || ctx.busy, onClick: () => updateNow(ctx) })));
}

function lastResultCard(ctx, update) {
  const last = update.last || {};
  if (!last.time) {
    return ui.card({ title: '最近一次更新' }, ui.empty('还没有更新记录', '配置完成或点击立即更新后，这里会显示结果。'));
  }
  const tone = last.skipped ? 'warn' : last.success ? 'ok' : 'bad';
  const outcome = last.skipped ? '已跳过' : !last.success ? '未完成' : last.unchanged ? '已是最新' : last.migrated ? '已切换来源' : '成功';
  const labels = { domains: '国内域名', ips: '国内 IP', privateDomains: '私有域名', lanCidrs: '局域网地址' };
  const counts = Object.entries(last.changes || {}).map(([key, row]) => [labels[key] || key,
    `${row.after ?? '—'} 条（新增 ${row.added ?? 0}，移除 ${row.removed ?? 0}）`]);
  return ui.card({ title: '最近一次更新', actions: ui.badge(outcome, tone) },
    ui.kv([
      ['时间', ui.formatTime(last.time)],
      ['结果', last.skipped ? '未执行（订阅或脚本关联已变化）' : !last.success ? '更新未完成' : last.unchanged ? '规则内容未变化，无需重载' : '已发布并核对新配置'],
      ['来源', last.source || '—'],
      ['原因', last.reason || last.error || '—'],
      ...counts,
    ]),
    !last.success && !last.skipped ? ui.note(last.recovery_required ? '恢复尚未完成。请先处理本地恢复记录，当前配置状态需要核对。' : '请检查网络、来源变化或本地错误记录后再重试。', 'warn') : null,
    last.skipped ? ui.note('脚本关联已改变，更新主动跳过。请重新配置后再更新。', 'warn') : null);
}

function updateSaveState() {
  const button = document.getElementById('save-update-settings');
  if (button) button.disabled = !draft.dirty || saving || currentCtx.busy;
}

async function saveSettings(ctx) {
  if (saving || ctx.busy) return;
  if (!draft.time || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(draft.time)) {
    ui.toast('请输入 00:00–23:59 之间的时间。', 'warn');
    return;
  }
  const days = Number(draft.days);
  if (!Number.isInteger(days) || days < 1 || days > 365) {
    ui.toast('更新间隔必须是 1–365 天。', 'warn');
    return;
  }
  saving = true;
  updateSaveState();
  const sentRevision = revision;
  try {
    const result = await ctx.api.post('/api/update-settings', { mode: draft.mode, time: draft.time, days });
    ctx.acceptState('update', result);
    if (revision === sentRevision) draft.dirty = false;
    ui.toast('规则更新方式已保存。', 'ok');
    await ctx.refresh(true);
  } catch (error) {
    ui.toast(error.message || '保存更新方式失败。', 'bad');
  } finally {
    saving = false;
    updateSaveState();
  }
}

async function updateNow(ctx) {
  const ok = await ctx.confirm({
    title: '立即更新分流规则？',
    description: '使用 Loyalsoldier 更新国内/私有规则；旧配置会在本次操作中首次切换来源。后续更新仍受变化阈值保护，不更新内核或节点。',
    items: [
      { title: '可能短暂重连', text: '有变化时通过本地控制通道重载；内容未变化时不重载。' },
      { title: '保留更新前备份', text: '发布失败会尝试恢复；不会访问 Claude 服务进行验证。' },
    ],
    confirmText: '开始更新',
  });
  if (!ok) return;
  ctx.startJob({ action: 'update', confirm: 'UPDATE' });
}
