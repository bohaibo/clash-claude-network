import { icon } from '../icons.js';
import * as ui from '../ui.js';

let selected = '';

export function render(ctx) {
  const state = ctx.state;
  const snapshots = state.snapshots || [];
  if (!selected || !snapshots.some((row) => row.name === selected)) {
    selected = snapshots.find((row) => row.associated && row.sealed)?.name || snapshots.find((row) => row.sealed)?.name || '';
  }
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    eyebrow: 'RECOVERY',
    title: '快照与恢复',
    description: '首次配置自动保存原始恢复点；之后可以另存当前状态。恢复会保留当前订阅、节点更新和后来手动选择的设置。',
    actions: [
      ui.button('保存当前快照', { variant: 'secondary', icon: 'plus', disabled: ctx.busy, onClick: () => saveSnapshot(ctx) }),
    ],
  }));
  root.append(settingsRecoveryCard(ctx));
  root.append(baselineCard(ctx));
  root.append(listCard(ctx, snapshots));
  root.append(ui.card({ title: '恢复边界', subtitle: '恢复不是 Windows 重置，也不会回放旧节点配置。' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只还原本项目记录过的系统、客户端关联和规则来源更新。'),
      ui.el('li', {}, '保留当前订阅、订阅地址更新、节点更新、删除和当前选择；不会用旧 profiles 索引覆盖。'),
      ui.el('li', {}, '默认 Chrome / Edge 与沙箱资料目录不会被关闭或删除；旧版快照中的浏览器记录会被忽略。'),
      ui.el('li', {}, '恢复后本项目注册的规则更新任务会被移除；如仍需定时更新，请在「规则更新」重新保存。'),
      ui.el('li', {}, '恢复不运行网络验收，需要自行确认联网。'))));
  root.append(ui.card({ title: '高风险恢复', subtitle: '当配置链路已经损坏，需要先回到最早状态再重新配置。' },
    ui.el('div', { class: 'action-row' },
      ui.el('div', {}, ui.el('strong', { text: '恢复最早状态并重新配置' }), ui.el('p', { text: '使用固定首次恢复点，再沿用当前精确节点重新配置。会短暂中断网络，并启动独立本机看护。' })),
      ui.button('前往网络环境', { variant: 'secondary', icon: 'network', onClick: () => ctx.navigate('network') }))));
  return root;
}

function settingsRecoveryCard(ctx) {
  const pending = ctx.state.operation?.settings_pending || [];
  return ui.card({ title: '未完成的设置保存', subtitle: '这里只恢复更新方式和计划任务，不更改网络快照或首次基线。' },
    pending.length ? pending.map(row => ui.el('div', { class: 'action-row' },
      ui.el('div', {}, ui.el('strong', { text: row.id }),
        ui.el('p', { text: row.error || '检测到中断或恢复失败的设置保存，可恢复修改前的状态。' })),
      ui.button('恢复原设置', { variant: 'secondary', disabled: ctx.busy, onClick: async () => {
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
    subtitle: '首次配置前自动保存，之后不会被当前状态覆盖。',
    actions: state.baseline ? ui.badge(verified ? '与配置关联' : '已固定', verified ? 'ok' : 'neutral') : ui.badge('缺失', 'bad'),
  },
  ui.kv([
    ['恢复点', state.baseline || '—'],
    ['配置关联', state.plan?.snapshot || '—'],
    ['用途', '完整的系统与客户端原始记录；配置失败或需要重建环境时先回到这里。'],
  ]),
  !state.baseline ? ui.note('没有可用的首次恢复点。请完成一次配置，或确认 .local/snapshots 未被删除。不要用当前状态伪造基线。', 'bad') : null);
}

function listCard(ctx, snapshots) {
  const host = document.createElement('div');
  const card = ui.card({
    title: '全部快照',
    subtitle: '按时间倒序；「配置关联」标记表示配置和补充改动所指向的恢复点。',
    actions: ui.button('恢复所选', { variant: 'ink', icon: 'clock', disabled: !selected || ctx.busy, onClick: () => restoreSelected(ctx) }),
  }, host);
  const restoreButton = card.querySelector('.card-actions .btn');
  const renderList = () => {
    if (!snapshots.length) {
      host.replaceChildren(ui.empty('还没有快照', '完成一次配置会自动保存原始恢复点；也可以手动保存当前状态。'));
      if (restoreButton) restoreButton.disabled = true;
      return;
    }
    host.replaceChildren(...snapshots.map((row) => {
      const radio = ui.el('span', { class: 'snapshot-radio' }, row.name === selected ? icon('check', 13) : null);
      const title = ui.el('div', { class: 'snapshot-title' },
        ui.el('strong', { text: row.name }),
        row.associated ? ui.pill('配置关联', 'ok') : null,
        row.sealed ? ui.pill('可恢复', 'neutral') : ui.pill('系统记录不完整', 'bad'));
      const meta = ui.el('div', { class: 'snapshot-meta' },
        ui.el('span', { text: ui.formatTime(row.time) }),
        ui.el('span', { text: row.kind === 'manual' ? '手动快照' : '自动基线' }),
        ui.el('span', { text: row.node ? '节点 ' + row.node : '未记录节点' }));
      const main = ui.el('div', { class: 'snapshot-main' }, title, meta);
      return ui.el('button', {
        class: 'snapshot-row' + (row.name === selected ? ' selected' : ''),
        type: 'button',
        onclick: () => {
          selected = row.name;
          renderList();
          if (restoreButton) restoreButton.disabled = ctx.busy || !row.sealed;
        },
      }, radio, main);
    }));
    if (restoreButton) restoreButton.disabled = ctx.busy || !snapshots.find((row) => row.name === selected)?.sealed;
  };
  renderList();
  return card;
}

async function saveSnapshot(ctx) {
  const ok = await ctx.confirm({
    title: '保存当前快照？',
    description: '会把当前客户端目录、系统设置和防火墙记录另存为一个新的快照。不会修改网络、浏览器或现有快照，也不会覆盖首次基线。',
    items: [
      { title: '需要读取系统记录', text: '保存过程会请求管理员权限以导出防火墙和系统状态。' },
      { title: '不会关闭浏览器', text: '默认浏览器和沙箱浏览器都保持运行。' },
    ],
    confirmText: '保存快照',
  });
  if (!ok) return;
  ctx.startJob({ action: 'snapshot', confirm: 'SNAPSHOT' });
}

async function restoreSelected(ctx) {
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
    description: '会按记录还原防火墙、IPv6、定位、国家/区域格式/时区、自动时区及自动校时、区域语言、代理设置和客户端项目关联；保留当前订阅、节点更新和后来手动选择的其他设置。',
    items: [
      { title: '需要管理员权限', text: '恢复会关闭并重新启动 Clash Verge；默认浏览器不会被关闭。' },
      { title: '可能部分完成', text: '如果文件被占用，界面会报告未完成项目；可以重试恢复。' },
    ],
    note: '恢复不会运行网络验收。恢复后本项目注册的规则更新任务会被移除。',
    confirmText: '确认恢复',
    danger: true,
    requireCheck: '我已保存工作，并了解恢复会改动系统网络设置。',
  });
  if (!ok) return;
  ctx.startJob({ action: 'restore', snapshot: row.name, confirm: row.name });
}
