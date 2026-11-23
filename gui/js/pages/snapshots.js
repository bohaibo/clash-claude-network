import { icon } from '../icons.js';
import * as ui from '../ui.js';

let selected = '';
let deleting = false;

function operationsBlocked(ctx, settingsRecovery = false) { return ctx.busy || Boolean(ctx.state.operation?.pending_rule_update) || Boolean(ctx.state.operation?.pending_tun?.length) || (!settingsRecovery && Boolean(ctx.state.operation?.settings_pending?.length)); }

export function render(ctx) {
  const state = ctx.state;
  const snapshots = state.snapshots || [];
  if (!selected || !snapshots.some((row) => row.name === selected)) {
    selected = snapshots.find((row) => row.associated && row.sealed)?.name || snapshots.find((row) => row.sealed)?.name || '';
  }
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '快照与恢复',
    actions: [
      ui.button('保存当前快照', { variant: 'secondary', icon: 'plus', disabled: operationsBlocked(ctx), onClick: () => saveSnapshot(ctx) }),
    ],
  }));
  const pending = settingsRecoveryCard(ctx);
  if (pending) root.append(pending);
  root.append(baselineCard(ctx));
  root.append(listCard(ctx, snapshots));
  return root;
}

function settingsRecoveryCard(ctx) {
  const pending = ctx.state.operation?.settings_pending || [];
  if (!pending.length) return null;
  return ui.card({ title: '未完成的设置保存', help: '这里只恢复更新方式和计划任务，不更改网络快照或首次基线。' },
    pending.length ? pending.map(row => ui.el('div', { class: 'action-row' },
      ui.el('div', {}, ui.el('strong', { text: row.id }),
        ui.el('p', { text: row.error || '检测到中断或恢复失败的设置保存，可恢复修改前的状态。' })),
      ui.button('恢复原设置', { variant: 'secondary', disabled: operationsBlocked(ctx, true), onClick: async () => {
        if (operationsBlocked(ctx, true)) return;
        const ok = await ctx.confirm({ title: '恢复原设置？',
          description: '还原此次保存前的更新方式和计划任务。不会恢复或删除网络快照，也不会重新配置网络。', confirmText: '恢复原设置' });
        if (ok) ctx.startJob({ action: 'settings-recover', transaction: row.id, confirm: row.id });
      } }))) : ui.note('没有待恢复的设置保存。', 'info'));
}

function baselineCard(ctx) {
  const state = ctx.state;
  const verified = state.baseline && state.plan?.snapshot === state.baseline;
  return ui.card({
    title: '固定首次恢复点',
    help: '首次配置前自动保存的原始系统状态，不会被覆盖。',
    actions: state.baseline ? ui.badge(verified ? '与配置关联' : '已固定', verified ? 'ok' : 'neutral') : ui.badge('缺失', 'bad'),
  },
  ui.kv([
    ['恢复点', state.baseline || '—'],
    ['配置关联', state.plan?.snapshot || '—'],
  ]),
  !state.baseline ? ui.note('未找到首次恢复点，请完成一次配置或检查 .local/snapshots 目录。', 'bad') : null);
}

function listCard(ctx, snapshots) {
  const host = ui.el('div', { class: 'snapshot-library' });
  const card = ui.card({
    title: '全部快照',
    help: '选中快照后点击「恢复所选」即可还原；首次恢复点和当前使用的快照不可删除。',
    actions: ui.button('恢复所选', { variant: 'ink', icon: 'clock', disabled: !selected || operationsBlocked(ctx), onClick: () => restoreSelected(ctx) }),
  }, host);
  const restoreButton = card.querySelector('.card-actions .btn');
  const renderList = () => {
    if (!snapshots.length) {
      host.replaceChildren(ui.empty('还没有快照', '完成一次配置会自动保存原始恢复点；也可以手动保存当前状态。'));
      if (restoreButton) restoreButton.disabled = true;
      return;
    }
    const chosen = snapshots.find(row => row.name === selected);
    const summary = ui.el('div', { class: 'snapshot-selection' },
      ui.el('div', {}, ui.el('span', { class: 'muted small', text: '已选恢复点' }),
        ui.el('strong', { text: chosen ? ui.formatTime(chosen.time) || chosen.name : '选择恢复点' }),
        ui.el('p', { text: chosen ? chosen.name : '点击下方卡片选择' })),
      ui.badge(chosen?.sealed ? '系统记录已封存' : '系统记录不完整', chosen?.sealed ? 'ok' : 'warn'));
    const grid = ui.el('div', { class: 'snapshot-grid', role: 'group', 'aria-label': '快照选择' }, ...snapshots.map((row) => {
      const active = row.name === selected;
      const pick = ui.el('button', {
        class: 'snapshot-pick', type: 'button', 'aria-pressed': String(active),
        'aria-label': '选择快照 ' + row.name,
        onclick: () => {
          selected = row.name;
          renderList();
          host.querySelectorAll('.snapshot-pick').forEach(button => {
            if (button.getAttribute('aria-pressed') === 'true') button.focus();
          });
        },
      },
      ui.el('div', { class: 'snapshot-card-top' },
        ui.el('span', { class: 'snapshot-symbol' }, icon('clock', 22)),
        ui.el('span', { class: 'snapshot-radio', 'aria-hidden': 'true' }, active ? icon('check', 13) : null)),
      ui.el('strong', { class: 'snapshot-date', text: ui.formatTime(row.time) || row.name }),
      ui.el('span', { class: 'snapshot-id', text: row.name }),
      ui.el('div', { class: 'snapshot-tags' },
        ui.pill(row.baseline ? '首次恢复点' : row.kind === 'manual' ? '手动快照' : '自动快照', 'neutral'),
        row.associated ? ui.pill('配置关联', 'ok') : null,
        !row.sealed ? ui.pill('记录不完整', 'bad') : null),
      ui.el('span', { class: 'snapshot-description', text: row.sealed ? '系统记录已封存 · 可选择恢复' : '缺少已封存系统记录 · 暂不可恢复' }),
      ui.el('span', { class: 'snapshot-description', text: row.files ? row.files + ' 项文件校验记录' : '暂无文件校验记录' }));
      const deleteButton = ui.button('删除', { variant: 'snapshot-delete', disabled: operationsBlocked(ctx) || deleting || !!row.delete_reason,
        title: row.delete_reason || '永久删除这份快照', onClick: () => deleteSnapshot(ctx, row, deleteButton) });
      return ui.el('article', { class: 'snapshot-tile' + (active ? ' selected' : '') }, pick,
        ui.el('div', { class: 'snapshot-footer' },
          ui.el('span', { class: 'snapshot-protection', text: row.delete_reason || '可删除此快照' }),
          deleteButton));
    }));
    host.replaceChildren(summary, grid);
    if (restoreButton) restoreButton.disabled = operationsBlocked(ctx) || deleting || !chosen?.sealed;
  };
  renderList();
  return card;
}

async function deleteSnapshot(ctx, row, button) {
  if (operationsBlocked(ctx) || deleting || row.delete_reason) return;
  const ok = await ctx.confirm({ title: '永久删除这份快照？', subtitle: row.name,
    description: '将删除这份快照的全部备份文件，删除后无法用它恢复。当前系统设置和其他快照保持原状。',
    items: [{title: '快照时间', text: ui.formatTime(row.time) || row.name}],
    danger: true, confirmText: '永久删除', requireCheck: '我确认不再需要这份恢复备份。' });
  if (!ok || deleting || operationsBlocked(ctx)) return;
  deleting = true;
  if (button) { button.disabled = true; button.textContent = '正在删除…'; }
  try {
    await ctx.api.post('/api/snapshot-delete', {snapshot: row.name, confirm: row.name}, {timeout: 60000});
    if (selected === row.name) selected = '';
    ui.toast('快照已删除。', 'ok');
  } catch (error) { ui.toast(error.message || '删除未完成，请刷新检查。', 'bad'); }
  finally { deleting = false; await ctx.refresh(true); }
}

async function saveSnapshot(ctx) {
  if (operationsBlocked(ctx) || deleting) return;
  const ok = await ctx.confirm({
    title: '保存当前快照？',
    description: '将当前客户端配置、系统设置和防火墙状态另存为一份新快照，不影响现有快照。',
    confirmText: '保存快照',
  });
  if (!ok) return;
  ctx.startJob({ action: 'snapshot', confirm: 'SNAPSHOT' });
}

async function restoreSelected(ctx) {
  if (operationsBlocked(ctx) || deleting) return;
  const state = ctx.state;
  const row = (state.snapshots || []).find((item) => item.name === selected);
  if (!row) return;
  if (!row.sealed) {
    ui.toast('该快照缺少完整的系统记录，不能作为可靠恢复点。', 'warn');
    return;
  }
  const ok = await ctx.confirm({
    title: '恢复到这份快照？',
    subtitle: row.name,
    description: '将还原防火墙、IPv6、定位、地区/时区、代理设置及客户端配置关联，保留你现有的订阅和节点。',
    note: '恢复会重启 Clash 并移除定时更新任务，期间网络会短暂断开。',
    confirmText: '确认恢复',
    danger: true,
    requireCheck: '我已保存工作，确认恢复此快照。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'restore', snapshot: row.name, confirm: row.name });
}
