import { icon } from '../icons.js';
import * as ui from '../ui.js';

let saving = false;
let saveBusy = false;
let savingGistSync = false;
let revision = 0;
let currentCtx = null;
export function hasDraft() { return draft.dirty || saving; }

function needsTaskSync(ctx) {
  const update = ctx?.state?.update || {};
  return Boolean((draft.mode === 'scheduled' && ctx?.state?.configured && update.task?.installed === false) ||
    (draft.mode === 'manual' && update.task?.installed));
}

const PRESETS = [[1, '每天'], [3, '每 3 天'], [7, '每周'], [30, '每月'], [180, '每半年']];
const draft = { mode: '', time: '', days: 1, custom: false, dirty: false };

function operationsBlocked(ctx, settingsRecovery = false) { return ctx.busy || Boolean(ctx.state.operation?.pending_rule_update) || Boolean(ctx.state.operation?.pending_tun?.length) || (!settingsRecovery && Boolean(ctx.state.operation?.settings_pending?.length)); }

export function render(ctx) {
  currentCtx = ctx;
  const state = ctx.state;
  const update = state.update || {};
  if (!draft.mode || (!draft.dirty && !saving)) {
    draft.mode = update.mode || 'scheduled';
    draft.time = update.time || '13:00';
    draft.days = Number(update.days) || 1;
    draft.custom = !PRESETS.some(([value]) => value === draft.days);
  }
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '规则更新',
    actions: [ui.button('刷新状态', { variant: 'secondary', icon: 'refresh', onClick: ctx.refreshManually })],
  }));
  if (state.operation?.pending_rule_update) {
    root.append(ui.card({title: '上次规则更新待恢复'},
      ui.note('恢复完成前暂停其他配置操作。这里只恢复上次更新，不下载新规则；如检测到后续手动改动，会停止并保留记录。', 'warn'),
      ui.button('恢复上次更新', {variant: 'secondary', disabled: ctx.busy || Boolean(state.operation?.pending_tun?.length), onClick: () => recoverRules(ctx)})));
  }
  root.append(modeCard(ctx, update));
  root.append(gistSyncSettings(ctx, update));
  root.append(statusCard(ctx, update));
  root.append(lastResultCard(ctx, update));
  return root;
}

function gistSyncSettings(ctx, update) {
  let enabled = update.mobile_gist_auto_sync !== false && update.mobile_gist_auto_sync !== null;
  const toggle = ui.el('input', {
    id: 'mobile-gist-auto-sync', type: 'checkbox', checked: enabled, disabled: savingGistSync || operationsBlocked(ctx),
    onchange: async event => {
      const input = event.target;
      const next = input.checked;
      if (savingGistSync || operationsBlocked(ctx)) {
        input.checked = enabled;
        return;
      }
      savingGistSync = true;
      input.disabled = true;
      try {
        const result = await ctx.api.post('/api/mobile-gist-auto-sync', {enabled: next});
        ctx.acceptState('update', {...ctx.state.update, mobile_gist_auto_sync: result.enabled});
        enabled = result.enabled;
        input.checked = result.enabled;
        ui.toast(result.enabled ? '多设备 Secret Gist 自动同步已开启。' : '多设备 Secret Gist 自动同步已关闭。', 'ok');
      } catch (error) {
        input.checked = enabled;
        ui.toast(error.message || '无法保存自动同步设置。', 'bad');
      } finally {
        savingGistSync = false;
        const current = document.getElementById?.('mobile-gist-auto-sync') || input;
        current.checked = enabled;
        current.disabled = operationsBlocked(ctx);
      }
    },
  });
  return ui.card({title: '多设备 Secret Gist 同步'},
    ui.el('label', {class: 'check-row'}, toggle, ui.el('span', {text: '规则更新后自动同步到已授权的 Secret Gist（手机与另一台电脑共用）'})),
    ui.el('p', {class: 'muted small', text: '此处为总开关。各配置需在「一键配置 → 多设备同步与导出 → 进阶设置」单独开启自动同步；定时规则更新只刷新分流名单，不会自动从远端 Gist 拉取更换本机节点。'}),
    update.mobile_gist_auto_sync === null ? ui.note('自动同步设置无法读取，保存开关后可恢复。', 'warn') : null);
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
  const custom = draft.custom || !PRESETS.some(([value]) => value === Number(draft.days));
  const frequency = ui.select(
    [...PRESETS.map(([value, label]) => ({ value: String(value), label })), { value: 'custom', label: '自定义天数' }],
    custom ? 'custom' : String(draft.days),
    (value) => {
      draft.custom = value === 'custom';
      if (draft.custom) { draft.days = Number(draft.days) || 7; }
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
      draft.custom = true;
      frequency.value = 'custom';
      draft.dirty = true; revision++;
      updateSaveState();
    },
  });
  const save = ui.button('保存更新方式', { variant: 'ink', disabled: (!draft.dirty && !needsTaskSync(ctx)) || operationsBlocked(ctx) || saveBusy, onClick: () => saveSettings(ctx) });
  save.id = 'save-update-settings';
  const unsaved = ui.el('span', { id: 'update-unsaved-hint', class: 'unsaved-hint', hidden: !draft.dirty, text: '有未保存的修改' });
  const card = ui.card({ title: '更新方式', help: configured ? '保存后会立即调整 Windows 计划任务。' : '尚未配置网络；此设置会在首次配置时生效。' },
    ui.el('div', { class: 'mode-grid' },
      option('manual', '手动更新', '需要时手动点击更新，不注册后台任务', 'clock'),
      option('scheduled', '定时更新', '登录期间按设定间隔自动检查并更新', 'refresh')),
    ui.el('div', { class: 'muted small heading-with-help' }, '更新范围', ui.helpTip('只更新国内和局域网分流名单，不改内核和节点。定时任务在当前用户登录期间运行。', '规则更新说明')),
    ui.el('div', { class: 'time-row' },
      ui.el('label', { class: 'field time-field' }, ui.el('span', { class: 'field-label', text: '更新时间' }), timeInput),
      ui.field('更新间隔', frequency),
      ui.el('label', { class: 'field days-field' }, ui.el('span', { class: 'field-label', text: '间隔天数' }), daysInput),
      save,
      unsaved),
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
    help: '在当前用户登录期间自动运行；恢复快照会移除该任务。',
    actions: taskBadge,
  },
  ui.kv([
    ['任务状态', task.state || (task.installed ? '已安装' : '未安装')],
    ['更新时间', task.time || (task.installed ? '未知' : '—')],
    ['更新间隔', task.days ? (Number(task.days) === 1 ? '每天' : '每 ' + task.days + ' 天') : '—'],
    ['下次运行', task.next_run ? ui.formatTime(task.next_run) : '—'],
    ...(!configured ? [['配置状态', '尚未配置，更新暂不可用']] : []),
  ]),
  task.error ? ui.note(task.error, 'warn') : null,
  ui.el('div', { class: 'form-foot' },
    ui.button('立即更新', { variant: 'secondary', icon: 'download', disabled: !configured || operationsBlocked(ctx), onClick: () => updateNow(ctx) })));
}

function lastResultCard(ctx, update) {
  const last = update.last || {};
  if (!last.time) {
    return ui.card({ title: '最近一次更新' }, ui.el('p', { class: 'muted', text: '还没有更新记录' }));
  }
  const tone = last.skipped ? 'warn' : last.success ? 'ok' : 'bad';
  const outcome = last.skipped ? '已跳过' : !last.success ? '未完成' : last.recovery_only ? '已恢复' : last.unchanged ? '已是最新' : last.migrated ? '已切换来源' : '成功';
  const labels = { domains: '国内域名', ips: '国内 IP', privateDomains: '私有域名', lanCidrs: '局域网地址' };
  const gist = last.mobile_gist_sync || {};
  const gistLabel = {updated: '已覆盖原 Gist，手机与其他电脑可继续使用原链接', unchanged: '已同步当前配置',
    unbound: '当前配置未绑定 Gist', disabled: '自动同步已关闭', inactive: '绑定的配置当前未启用',
    partial: '部分配置已同步', failed: '同步未完成'}[gist.status];
  const counts = Object.entries(last.changes || {}).map(([key, row]) => [labels[key] || key,
    `${row.after ?? '—'} 条（新增 ${row.added ?? 0}，移除 ${row.removed ?? 0}）`]);
  return ui.card({ title: '最近一次更新', actions: ui.badge(outcome, tone) },
    ui.kv([
      ['时间', ui.formatTime(last.time)],
      ['结果', last.skipped ? '未执行（订阅或脚本关联已变化）' : !last.success ? '更新未完成' : last.recovery_only ? '已处理上次更新的恢复记录，未下载新规则' : last.unchanged ? '规则内容未变化，无需重载' : '已发布并核对新配置'],
      ...(last.chain_profile_updated ? [['链式配置', '已同步分流规则']] : []),
      ...(last.chain_traffic_updated ? [['链式流量', '已同步原订阅缓存用量，Clash 客户端可能需重新打开显示']] : []),
      ...(gistLabel ? [['Secret Gist 同步', gist.reason ? gistLabel + '（' + gist.reason + '）' : gistLabel]] : []),
      ...(last.reason || last.error ? [['原因', last.reason || last.error]] : []),
      ...counts,
    ]),
    !last.success && !last.skipped ? ui.note(last.recovery_required ? '恢复尚未完成。请先处理本地恢复记录，当前配置状态需要核对。' : '请检查网络、来源变化或本地错误记录后再重试。', 'warn') : null,
    ['partial', 'failed'].includes(gist.status) ? ui.note('本机规则已更新或已是最新。下次规则检查将重试 Secret Gist 同步。', 'warn') : null,
    last.skipped ? ui.note('脚本关联已改变，更新主动跳过。请重新配置后再更新。', 'warn') : null);
}

function updateSaveState() {
  const button = document.getElementById('save-update-settings');
  if (button) button.disabled = (!draft.dirty && !needsTaskSync(currentCtx)) || saveBusy || operationsBlocked(currentCtx);
  const unsaved = document.getElementById('update-unsaved-hint');
  if (unsaved) unsaved.hidden = !draft.dirty;
}

async function saveSettings(ctx) {
  if (saveBusy || operationsBlocked(ctx)) return;
  const validTime = value => typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
  const validDays = value => Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 365;
  const scheduled = draft.mode === 'scheduled';
  if (scheduled && !validTime(draft.time)) {
    ui.toast('请输入 00:00–23:59 之间的时间。', 'warn');
    return;
  }
  if (scheduled && !validDays(draft.days)) {
    ui.toast('更新间隔必须是 1–365 天。', 'warn');
    return;
  }
  saving = true;
  saveBusy = true;
  updateSaveState();
  const sentRevision = revision;
  try {
    const saved = ctx.state.update || {};
    const time = validTime(draft.time) ? draft.time : validTime(saved.time) ? saved.time : '13:00';
    const days = Number(validDays(draft.days) ? draft.days : validDays(saved.days) ? saved.days : 1);
    const result = await ctx.api.post('/api/update-settings', { mode: draft.mode, time, days });
    ctx.acceptState('update', result);
    if (revision === sentRevision) draft.dirty = false;
    ui.toast('规则更新方式已保存。', 'ok');
  } catch (error) {
    ui.toast(error.message || '保存更新方式失败。', 'bad');
  } finally {
    saving = false;
    await ctx.refresh(true);
    saveBusy = false;
    updateSaveState();
  }
}

async function updateNow(ctx) {
  if (operationsBlocked(ctx)) return;
  const ok = await ctx.confirm({
    title: '立即更新分流规则？',
    description: '更新国内与局域网分流名单（不改动内核和节点）。有变化时会自动重载配置。',
    confirmText: '开始更新',
  });
  if (!ok) return;
  ctx.startJob({ action: 'update', confirm: 'UPDATE' });
}

async function recoverRules(ctx) {
  const ok = await ctx.confirm({title: '恢复上次规则更新？',
    description: '从备份还原上次规则更新前的文件并重载配置。',
    confirmText: '恢复上次更新'});
  if (ok) ctx.startJob({action: 'update-recover', confirm: 'RECOVER-RULES'});
}
