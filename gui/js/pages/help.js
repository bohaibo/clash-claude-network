import * as ui from '../ui.js';
import { configurationDetails } from './network.js';
import { packageCard, updateInstructions } from './software.js';

export function render(ctx) {
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '使用说明',
  }));
  root.append(ui.disclosure({ title: '第一次使用', open: true },
    ui.el('ol', { class: 'steps' },
      ui.el('li', {}, ui.el('strong', { text: '确认前置条件' }), '使用 Windows 11 x64；手动安装 Clash Verge Rev 及 Mihomo 内核，启动 Clash Verge 后台服务并导入可用订阅。准备已安装的 Edge 或 Chrome，仅用于显示本地管理界面。'),
      ui.el('li', {}, ui.el('strong', { text: '启动项目' }), '运行根目录「一键网络.cmd」。缺少的 Python 3.13、PyYAML 和 Node.js 会自动安装到项目 .runtime 目录，不需要手动安装，不修改系统 PATH 或系统运行环境。首次下载需要联网。命令行备用入口为 一键网络.cmd --menu。'),
      ui.el('li', {}, ui.el('strong', { text: '选择节点' }), '在「一键配置」选择订阅中的完整节点名。节点必须已存在；不会猜测或替换。'),
      ui.el('li', {}, ui.el('strong', { text: '完成配置' }), '先阅读当前设置与配置后目标，确认影响后在 Windows 管理员授权窗口中确认。首次配置保存本机原始快照，之后沿用该基线；配置会生成并检查候选配置、应用系统设置并确认写入，期间会短暂中断网络。联网检查和故障演练位于“网络环境”的独立按钮，按需点击。'),

  )));
  root.append(ui.disclosure({ title: '规则更新方式' },
    ui.el('div', { class: 'two-col' },
      ui.el('div', {}, ui.el('h3', { text: '手动更新' }), ui.el('p', {}, '不注册后台任务。打开软件后，在「规则更新」点击「立即更新」。适合不希望后台联网、或经常手动检查的用户。')),
      ui.el('div', {}, ui.el('h3', { text: '定时更新' }), ui.el('p', {}, '注册当前用户的 Windows 计划任务，可设 1–365 天间隔和具体时间。电脑开机且用户已登录时刷新已审核规则来源。')),
    ),
    ui.note('两种方式都只更新已审核来源，不更新内核或节点；来源变化过大时会停止并保留旧配置。', 'info')));

  root.append(ui.disclosure({ title: '严格防火墙与恢复' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '默认出站阻断，只保留项目核心、TUN、LAN 和 DHCP 必要路径，并阻断 SharedAccess 服务出站。'),
      ui.el('li', {}, '节点故障但内核仍在运行时，国内网站保持直连；整个内核停止时，所有公网（包括国内）都会被阻断。'),
      ui.el('li', {}, '「快照与恢复」只撤销本项目记录过的设置，保留当前订阅、节点更新和后来手动选择的其他设置。'),
      ui.el('li', {}, '配置和补充配置会关闭自动定位、自动时区和自动校时，并按节点所选地区设置国家、区域格式（日期和数字）及时区；Windows 显示语言保持不变。恢复还原原设置，不把时钟拨回备份日期。'),
      ui.el('li', {}, '恢复会移除项目规则更新任务；如需继续定时更新，请重新保存更新方式。'),
      ui.el('li', {}, '恢复不运行网络验收；IPv6 和语言改动通常需要重启 Windows 才完全生效。'))));

  root.append(ui.disclosure({ title: '浏览器 WebRTC 与外部指纹浏览器' }, ui.el('p', {}, 'Chrome/Edge 使用路径限制策略与扩展两层保护；一键配置自动安排安装各自官方商店的 WebRTC Control，已有扩展直接复用。浏览器运行时自动完成安装，关闭时将在下次正常启动后完成。Firefox 直接锁定关闭 WebRTC。项目同时附带离线备用扩展，备用文件位置可在本页下方的详细状态中查看。恢复还原受管策略及自动安装条目。外部指纹浏览器自行管理。')));

  root.append(ui.disclosure({ title: '不会做的事' },
    ui.el('ul', { class: 'check-list muted' },
      ui.el('li', {}, '不安装或登录 Claude，不发送 Claude 对话，不清理账号 Cookie。'),
      ui.el('li', {}, '不复制或发布订阅节点、控制器密钥、浏览器登录数据或原始备份。'),
      ui.el('li', {}, '不声称零泄漏、不封号、住宅 IP 或覆盖未来所有应用。'),
      ui.el('li', {}, '不把当前已配置状态覆盖首次基线；恢复点由 .local 记录固定。'))));

  root.append(ui.disclosure({ title: '遇到问题时' },
    ui.el('div', { class: 'table' },
      ui.el('div', { class: 'table-row table-head' }, ui.el('span', { text: '情况' }), ui.el('span', { text: '下一步' })),
      row('取消管理员授权', '不会执行所选操作。重新点击操作，在 Windows 授权窗口确认。'),
      row('节点不存在或重名', '在「一键配置」重新选择；不会自动替换。检查 Clash 是否已更新订阅。'),
      row('网络验收失败', '在「日志与验证」查看失败项和 .local/verification.json；不要使用旧的成功记录。'),
      row('配置中断', '先在「快照与恢复」恢复关联快照，再重新配置；不要用当前状态覆盖基线。'),
      row('规则更新被跳过', '订阅或脚本关联已变化；重新配置后再更新。'),
      row('界面无法连接', '关闭窗口后重新运行根目录「一键网络.cmd」；命令行备用入口为 一键网络.cmd --menu。'))));
  root.append(ui.disclosure({ title: '更新校验' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只更新项目配置中已审核的国内域名、国内 IP、私有域名和 LAN 地址来源。'),
      ui.el('li', {}, '来源发生较大变化、私有/LAN 内容变化或 Claude 策略变化时会停止，保留旧配置等待人工审核。'),
      ui.el('li', {}, '首次切换来源需点击「立即更新」；定时任务不会自动扩大到新来源。海外优先规则与对应 DNS 策略保持同步。'),
      ui.el('li', {}, '先校验完整候选配置，再发布并核对内核加载数量；失败会尝试恢复，恢复未完成时会明确提示。'),
      ui.el('li', {}, '不更新 Mihomo 内核、不添加节点、不改变固定节点组。'),
      ui.el('li', {}, '定时任务只在当前 Windows 用户登录期间运行；电脑关机或未登录时不会执行。'),
      ui.el('li', {}, '恢复快照会移除本项目的定时任务；需要时请在这里重新保存定时方式。'))));
  root.append(ui.disclosure({ title: '恢复边界' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只还原本项目记录过的系统、客户端关联和规则来源更新。'),
      ui.el('li', {}, '保留当前订阅、订阅地址更新、节点更新、删除和当前选择；不会用旧 profiles 索引覆盖。'),
      ui.el('li', {}, '外部浏览器及旧版浏览器资料不会被关闭或删除；旧版快照中的浏览器记录会被忽略。'),
      ui.el('li', {}, '恢复后本项目注册的规则更新任务会被移除；如仍需定时更新，请在「规则更新」重新保存。'),
      ui.el('li', {}, '恢复不运行网络验收，需要自行确认联网。'))));
  root.append(ui.disclosure({ title: '当前配置与浏览器详情' }, ...configurationDetails(ctx)));
  root.append(updateInstructions());
  root.append(packageCard(ctx));

  return root;
}

function row(left, right) {
  return ui.el('div', { class: 'table-row' }, ui.el('span', { text: left }), ui.el('span', { text: right }));
}
