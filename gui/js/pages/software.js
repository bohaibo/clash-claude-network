import * as ui from '../ui.js';

let exporting = false;
export function render(ctx) {
  const info = ctx.state.software || {};
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({eyebrow: 'SOFTWARE', title: '软件更新与打包',
    description: '查看程序版本、检查正式版更新，或导出可分发的 ZIP 安装包。分流规则仍在「规则更新」单独管理。'}));
  const labels = {available: '发现新版本', current: '当前版本不低于最新正式版', error: '暂时无法检查', unchecked: '尚未检查'};
  root.append(ui.card({title: '软件版本', actions: ui.badge(info.checking ? '正在检查' : labels[info.status] || '尚未检查', info.status === 'available' ? 'ok' : 'neutral')},
    ui.kv([['当前版本', info.current_version || ctx.state.version || '—'], ['最新正式版', info.latest_version || '—'],
      ['最近检查', info.checked_at ? ui.formatTime(info.checked_at) : '—']]),
    info.reason ? ui.note(info.reason, 'warn') : null,
    info.notes ? ui.el('p', {class: 'muted', text: info.notes}) : null,
    ui.el('div', {class: 'form-foot'},
      ui.button('检查软件更新', {variant: 'secondary', icon: 'refresh', disabled: info.checking,
        onClick: async () => {
          try { ctx.acceptState('software', await ctx.api.post('/api/software-check', {})); await ctx.refresh(true); }
          catch (error) { ui.toast(error.message || '更新检查未完成。', 'bad'); }
        }}),
      releaseLink(info)),
    ui.note('正常启动时最多每 24 小时自动检查一次，也可手动检查。只提示正式版本，不自动下载或覆盖程序。', 'info')));
  const bundle = info.package;
  root.append(ui.card({title: '导出便携安装包', subtitle: '解压后双击「一键网络.cmd」；新电脑首次启动会下载项目内运行环境。'},
    ui.el('p', {text: '只打包程序、界面、文档和校验清单。节点、订阅、密钥、快照、日志及本机运行环境均不会包含在安装包中。'}),
    ui.el('div', {class: 'form-foot'}, ui.button(exporting ? '正在打包…' : '生成 ZIP 安装包', {
      variant: 'secondary', icon: 'download', disabled: exporting || ctx.busy,
      onClick: async () => {
        if (exporting || ctx.busy) return;
        exporting = true;
        try {
          ctx.acceptState('software', await ctx.api.post('/api/package', {}, {timeout: 60000}));
          ui.toast('安装包已生成，可点击下载安装包。', 'ok');
        } catch (error) { ui.toast(error.message || '打包未完成。', 'bad'); }
        finally { exporting = false; await ctx.refresh(true); }
      },
    }), bundle?.download_url && /^\/api\/packages\/[a-f0-9]{32}$/.test(bundle.download_url)
      ? ui.el('a', {class: 'btn btn-secondary', href: bundle.download_url, download: bundle.filename, text: '下载安装包'}) : null),
    bundle ? ui.kv([['文件', bundle.filename], ['大小', (bundle.size / 1024).toFixed(0) + ' KB'], ['SHA-256', bundle.sha256]]) : null));
  root.append(ui.card({title: '如何更新现有软件'},
    ui.el('ol', {class: 'check-list'},
      ui.el('li', {text: '从正式发布页下载安装包，并核对发布方提供的 SHA-256。'}),
      ui.el('li', {text: '等待当前操作结束，点击「退出界面」，再解压并覆盖同一软件目录内的程序文件。'}),
      ui.el('li', {text: '保留原目录的 .local 和 .runtime；不要将本机资料复制给其他人。'}),
      ui.el('li', {text: '重新双击「一键网络.cmd」。若版本提示配置不一致，再按网络环境页的说明处理。'}))));
  return root;
}

function releaseLink(info) {
  if (!['available', 'current'].includes(info.status) || !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(info.repository || '')) return null;
  const base = 'https://github.com/' + info.repository + '/releases/tag/';
  const url = info.release_url || '';
  if (!url.startsWith(base) || !/^v?\d+\.\d+\.\d+$/.test(url.slice(base.length))) return null;
  return ui.el('a', {class: 'btn btn-secondary', href: url, target: '_blank', rel: 'noopener noreferrer', text: '查看正式发布页'});
}
