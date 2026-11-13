import * as ui from '../ui.js';

export function render(ctx) {
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    eyebrow: 'GUIDE',
    title: '使用说明',
    description: '适用于 Windows 11 的本地网络配置工具。读取已有 Clash 订阅，不安装或登录 Claude，也不保证账号可用性。',
  }));
  root.append(ui.card({ title: '第一次使用' },
    ui.el('ol', { class: 'steps' },
      ui.el('li', {}, ui.el('strong', { text: '确认前置条件' }), '使用 Windows 11 x64；手动安装 Clash Verge Rev 及 Mihomo 内核，启动 Clash Verge 后台服务并导入可用订阅。准备已安装的 Edge 或 Chrome，仅用于显示本地管理界面。'),
      ui.el('li', {}, ui.el('strong', { text: '启动项目' }), '运行根目录「一键网络.cmd」。缺少的 Python 3.13、PyYAML 和 Node.js 会自动安装到项目 .runtime 目录，不需要手动安装，不修改系统 PATH 或系统运行环境。首次下载需要联网。命令行备用入口为 一键网络.cmd --menu。'),
      ui.el('li', {}, ui.el('strong', { text: '选择节点' }), '在「一键配置」选择订阅中的完整节点名。节点必须已存在；不会猜测或替换。'),
      ui.el('li', {}, ui.el('strong', { text: '完成配置' }), '先阅读当前设置与配置后目标，确认影响后在 Windows 管理员授权窗口中确认。首次配置保存本机原始快照，之后沿用该基线；配置会生成并检查候选配置、应用系统设置并运行网络验收，期间会短暂中断网络。'),

  )));
  root.append(ui.card({ title: '规则更新方式' },
    ui.el('div', { class: 'two-col' },
      ui.el('div', {}, ui.el('h3', { text: '手动更新' }), ui.el('p', {}, '不注册后台任务。打开软件后，在「规则更新」点击「立即更新」。适合不希望后台联网、或经常手动检查的用户。')),
      ui.el('div', {}, ui.el('h3', { text: '定时更新' }), ui.el('p', {}, '注册当前用户的 Windows 计划任务，可设 1–365 天间隔和具体时间。电脑开机且用户已登录时刷新已审核规则来源。')),
    ),
    ui.note('两种方式都只更新已审核来源，不更新内核或节点；来源变化过大时会停止并保留旧配置。', 'info')));

  root.append(ui.card({ title: '严格防火墙与恢复' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '默认出站阻断，只保留项目核心、TUN、LAN 和 DHCP 必要路径，并阻断 SharedAccess 服务出站。'),
      ui.el('li', {}, '节点故障但内核仍在运行时，国内网站保持直连；整个内核停止时，所有公网（包括国内）都会被阻断。'),
      ui.el('li', {}, '「快照与恢复」只撤销本项目记录过的设置，保留当前订阅、节点更新和后来手动选择的其他设置。'),
      ui.el('li', {}, '配置和补充配置会关闭自动定位、自动时区和自动校时，并按节点所选地区设置国家、区域格式（日期和数字）及时区；Windows 显示语言保持不变。恢复还原原设置，不把时钟拨回备份日期。'),
      ui.el('li', {}, '恢复会移除项目规则更新任务；如需继续定时更新，请重新保存更新方式。'),
      ui.el('li', {}, '恢复不运行网络验收；IPv6 和语言改动通常需要重启 Windows 才完全生效。'))));

  root.append(ui.card({ title: '浏览器 WebRTC 与外部指纹浏览器' }, ui.el('p', {}, '配置会备份 Chrome、Edge、Firefox 的受管 WebRTC 策略：Firefox 直接禁用；Chrome/Edge 施加路径限制并请求安装各自商店的 WebRTC Control 扩展。扩展实际安装和网页阻断未自动验证，隐私窗口不自动覆盖。恢复会还原策略并撤销本项目新增的扩展强制安装条目，保留其他扩展条目。外部指纹浏览器自行管理；本软件不提供浏览器启动、资料、语言、指纹或默认应用设置。')));

  root.append(ui.card({ title: '不会做的事' },
    ui.el('ul', { class: 'check-list muted' },
      ui.el('li', {}, '不安装或登录 Claude，不发送 Claude 对话，不清理账号 Cookie。'),
      ui.el('li', {}, '不复制或发布订阅节点、控制器密钥、浏览器登录数据或原始备份。'),
      ui.el('li', {}, '不声称零泄漏、不封号、住宅 IP 或覆盖未来所有应用。'),
      ui.el('li', {}, '不把当前已配置状态覆盖首次基线；恢复点由 .local 记录固定。'))));

  root.append(ui.card({ title: '遇到问题时' },
    ui.el('div', { class: 'table' },
      ui.el('div', { class: 'table-row table-head' }, ui.el('span', { text: '情况' }), ui.el('span', { text: '下一步' })),
      row('取消管理员授权', '不会执行所选操作。重新点击操作，在 Windows 授权窗口确认。'),
      row('节点不存在或重名', '在「一键配置」重新选择；不会自动替换。检查 Clash 是否已更新订阅。'),
      row('网络验收失败', '在「日志与验证」查看失败项和 .local/verification.json；不要使用旧的成功记录。'),
      row('配置中断', '先在「快照与恢复」恢复关联快照，再重新配置；不要用当前状态覆盖基线。'),
      row('规则更新被跳过', '订阅或脚本关联已变化；重新配置后再更新。'),
      row('界面无法连接', '关闭窗口后重新运行根目录「一键网络.cmd」；命令行备用入口为 一键网络.cmd --menu。'))));
  return root;
}

function row(left, right) {
  return ui.el('div', { class: 'table-row' }, ui.el('span', { text: left }), ui.el('span', { text: right }));
}
