import * as ui from '../ui.js';
import { icon } from '../icons.js';

let exporting = false;
export function render(ctx) {
  const info = ctx.state?.software || {};
  const root = document.createElement('div');
  root.className = 'stack';
  const check = ui.button('检查软件更新', {variant: 'secondary', icon: 'refresh', disabled: info.checking,
    onClick: async () => {
      try { ctx.acceptState('software', await ctx.api.post('/api/software-check', {})); await ctx.refresh(true); }
      catch (error) { ui.toast(error.message || '更新检查未完成。', 'bad'); }
    }});
  check.id = 'check-software-update';
  const labels = {available: '发现新版本', current: '当前无需更新', no_release: '尚未发布正式版本', error: '暂时无法检查', unchecked: '尚未检查'};
  root.append(ui.card({title: '软件版本', actions: ui.badge(info.checking ? '正在检查' : labels[info.status] || '尚未检查', info.status === 'available' ? 'ok' : 'neutral')},
    ui.kv([['当前版本', info.current_version || ctx.state?.version || '—'], ['最新正式版', info.latest_version || '—'],
      ['最近检查', info.checked_at ? ui.formatTime(info.checked_at) : '—']]),
    info.reason ? ui.note(info.reason, 'warn') : null,
    info.notes ? ui.disclosure({title: '版本说明', key: 'software-notes'}, ui.el('p', {class: 'muted release-notes', text: info.notes})) : null,
    ui.el('div', {class: 'form-foot'},
      check,
      releaseLink(info)),
    ui.helpTip('每次打开软件自动检查一次。当前仅提示更新，请从正式发布页下载，退出界面后覆盖程序文件，保留 .local 和 .runtime。', '软件更新说明')));
  return root;
}

export function packageCard(ctx) {
  const info = ctx.state?.software || {};
  const bundle = info.package;
  const card = ui.disclosure({title: '导出便携安装包', key: 'help-package'},
    ui.el('p', {class: 'muted small', text: '仅包含程序、文档和校验清单，不包含本机资料。解压后运行「一键网络.cmd」，新电脑会下载所需运行环境。'}),
    ui.el('div', {class: 'form-foot'}, ui.button(exporting ? '正在打包…' : '生成 ZIP 安装包', {
      variant: 'secondary', icon: 'download', disabled: exporting || ctx.busy,
      onClick: async (event) => {
        if (exporting || ctx.busy) return;
        exporting = true;
        if (event?.currentTarget) { event.currentTarget.disabled = true; event.currentTarget.textContent = '正在打包…'; }
        try {
          ctx.acceptState('software', await ctx.api.post('/api/package', {}, {timeout: 60000}));
          ui.toast('安装包已生成，可点击下载安装包。', 'ok');
        } catch (error) { ui.toast(error.message || '打包未完成。', 'bad'); }
        finally { exporting = false; await ctx.refresh(true); card.replaceWith(packageCard(ctx)); }
      },
    }), bundle?.download_url && /^\/api\/packages\/[a-f0-9]{32}$/.test(bundle.download_url)
      ? ui.el('a', {class: 'btn btn-secondary', href: bundle.download_url, download: bundle.filename, text: '下载安装包'}) : null),
    bundle ? ui.kv([['文件', bundle.filename], ['大小', (bundle.size / 1024).toFixed(0) + ' KB'], ['SHA-256', bundle.sha256]]) : null);
  return card;
}

export function updateInstructions() {
  return ui.disclosure({title: '如何更新软件'},
    ui.el('ol', {class: 'check-list'},
      ui.el('li', {text: '从正式发布页下载安装包，并核对发布方提供的 SHA-256。'}),
      ui.el('li', {text: '等待当前操作结束，点击「退出界面」，再解压并覆盖同一软件目录内的程序文件。'}),
      ui.el('li', {text: '保留原目录的 .local 和 .runtime；不要将本机资料复制给其他人。'}),
      ui.el('li', {text: '重新双击「一键网络.cmd」。若提示配置不一致，请在「使用说明 → 当前配置与浏览器详情」核对。'})));
}

export function control(ctx) {
  return ui.el('div', {class: 'software-control'},
    ui.el('button', {class: 'icon-btn software-update-button', id: 'software-update-button', type: 'button',
      title: '软件更新', hidden: true, 'aria-label': '软件更新', popovertarget: 'software-update-panel'},
      icon('download', 18), ui.el('span', {id: 'software-update-label', hidden: true, text: '发现新版本'})),
    ui.el('div', {class: 'software-update-panel', id: 'software-update-panel', popover: 'auto',
      role: 'dialog', 'aria-label': '软件更新'}, render(ctx)));
}

export function refreshControl(ctx) {
  const button = document.getElementById('software-update-button');
  const panel = document.getElementById('software-update-panel');
  if (!button || !panel) return;
  const info = ctx.state?.software || {};
  const available = info.status === 'available' && !info.checking && ctx.softwareCheckStarted !== false;
  button.hidden = !available;
  if (!available && panel.matches?.(':popover-open')) panel.hidePopover();
  const label = available ? '发现软件新版本 ' + (info.latest_version || '') : info.status === 'error' ? '软件更新：暂时无法检查' : '软件更新';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.classList.toggle('has-update', available);
  const hint = document.getElementById('software-update-label');
  if (hint) hint.hidden = !available;
  const signature = JSON.stringify(info);
  if (panel.dataset.signature === signature) return;
  panel.dataset.signature = signature;
  const checkingFocused = panel.contains(document.activeElement) && document.activeElement.id === 'check-software-update';
  panel.replaceChildren(render(ctx));
  if (checkingFocused) panel.querySelector('#check-software-update')?.focus({preventScroll: true});
}

function releaseLink(info) {
  if (!['available', 'current'].includes(info.status) || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(info.repository || '')) return null;
  const base = 'https://github.com/' + info.repository + '/releases/tag/';
  const url = info.release_url || '';
  if (!url.startsWith(base) || !/^v?\d+\.\d+\.\d+$/.test(url.slice(base.length))) return null;
  return ui.el('a', {class: 'btn btn-secondary', href: url, target: '_blank', rel: 'noopener noreferrer', text: '查看正式发布页'});
}
