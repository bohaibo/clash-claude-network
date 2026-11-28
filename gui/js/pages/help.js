import * as ui from '../ui.js';
import { configurationDetails } from './network.js';
import { packageCard, updateInstructions, render as softwareVersion } from './software.js';

export function render(ctx) {
  const root = document.createElement('div');
  root.className = 'stack';
  root.append(ui.pageHeader({
    title: '使用说明',
  }));
  root.append(ui.disclosure({ title: '第一次使用', open: true },
    ui.el('ol', { class: 'steps' },
      ui.el('li', {}, ui.el('strong', { text: '准备软件' }), '仅支持 Windows 11。请先安装并启动 Clash Verge Rev（含后台服务）。使用订阅节点时需先导入订阅；如使用「从 Secret Gist 导入配置」或「单一静态 IP」，无需预先导入订阅。'),
      ui.el('li', {}, ui.el('strong', { text: '打开工具' }), '双击根目录「一键网络.cmd」。首次运行会自动把 Python、Node.js 等下载到项目 .runtime 目录，不影响系统环境（命令行模式可用 一键网络.cmd --menu）。'),
      ui.el('li', {}, ui.el('strong', { text: '选择方式与节点' }), '在「一键配置」选择配置方式（已有另一台电脑上传的 Secret Gist 时，直接选「从 Secret Gist 导入配置」粘贴链接最省事）。工具不会自动猜节点或替换节点。'),
      ui.el('li', {}, ui.el('strong', { text: '确认并验收' }), '查看影响说明并授权管理员权限。首次配置会自动备份本机原始状态；写入完成后自动做网络验收，期间会短暂断网。'),
  )));
  root.append(ui.disclosure({ title: '多设备同步（另一台电脑 / 手机）' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '共用同一个 Secret Gist：在源电脑「一键配置 → 多设备同步与导出」点一次「创建 / 更新 Secret Gist」，即可直接一键复制 Windows 导入链接、Android 订阅链接或 iPhone 订阅链接。'),
      ui.el('li', {}, '另一台 Windows 电脑选「从 Secret Gist 导入配置」，粘贴链接即可一键完成配置，无需导入原订阅或登录 GitHub CLI；工具会在本机完整重建 Windows 规则、TUN 与严格防火墙。'),
      ui.el('li', {}, '源电脑换节点并点「更新 Secret Gist」后，另一台电脑选中已保存配置点「从原 Secret Gist 重新拉取最新节点」即可更新，无需再复制粘贴链接。'))));
  root.append(ui.disclosure({ title: '规则更新方式' },
    ui.el('div', { class: 'two-col' },
      ui.el('div', {}, ui.el('h3', { text: '手动更新' }), ui.el('p', {}, '不设后台任务，需要时在「规则更新」点「立即更新」。适合按需手动控制的用户。')),
      ui.el('div', {}, ui.el('h3', { text: '定时更新' }), ui.el('p', {}, '使用 Windows 计划任务，在登录期间按设定的间隔（1–365 天）自动更新。')),
    ),
    ui.note('只更新已审核的分流名单，不更新内核或节点；名单异常变化时会自动停止并保留原配置。', 'info')));
  root.append(ui.disclosure({title:'链式代理与完整防护'},
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '单一节点、链式代理、静态 IP 与 Secret Gist 导入五种配置可同时保留，在 Clash Verge「订阅」页切换使用。'),
      ui.el('li', {}, '前置节点负责中转，后置节点决定最终出口与地区，不影响原订阅。'),
      ui.el('li', {}, '任一跳故障时海外流量自动断开；内核停止时阻断全部公网。'),
      ui.el('li', {}, '流量条显示两份原订阅中剩余较少者的用量，到期日取较早者；在 Clash 刷新订阅后执行「规则更新」即可同步。'))));

  root.append(ui.disclosure({ title: '严格防火墙与恢复' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '默认拦截出站流量，仅放行代理内核、TUN、局域网和 DHCP。'),
      ui.el('li', {}, '节点故障时国内直连保持可用；内核停止时国内外公网都会断开。'),
      ui.el('li', {}, '配置会关闭自动定位、自动时区和自动校时，并按出口地区调整国家、区域格式和时区。'),
      ui.el('li', {}, '在「快照与恢复」可随时还原系统设置，保留你的订阅和节点；IPv6 和区域格式通常需重启 Windows 生效。'))));

  root.append(ui.disclosure({ title: '浏览器 WebRTC' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, 'Chrome / Edge：限制非代理 UDP，并自动安排安装官方商店的 WebRTC Control（另附离线备用包）。'),
      ui.el('li', {}, 'Firefox：直接通过策略关闭 WebRTC，无需扩展。'),
      ui.el('li', {}, '恢复快照会自动还原这些策略设置。'))));

  root.append(ui.disclosure({ title: '功能范围' },
    ui.el('ul', { class: 'check-list muted' },
      ui.el('li', {}, '专注本地网络与系统环境配置，不涉及 Claude 客户端安装或账号登录。'),
      ui.el('li', {}, '所有配置、备份和日志仅保存在本地 .local 目录，不上传到网络。'),
      ui.el('li', {}, '首次保存的原始恢复点会被固定保护，便于随时回退。'))));

  root.append(ui.disclosure({ title: '遇到问题时' },
    ui.el('div', { class: 'table' },
      ui.el('div', { class: 'table-row table-head' }, ui.el('span', { text: '情况' }), ui.el('span', { text: '处理方式' })),
      row('取消了管理员授权', '操作不会执行。重新点击按钮并在系统弹窗点「是」即可。'),
      row('找不到节点或重名', '先在 Clash 刷新订阅，再到「一键配置」重新选择。'),
      row('网络验收失败', '到「日志与验证」查看失败项，不要依赖旧的通过记录。'),
      row('配置中途出错', '先到「快照与恢复」恢复关联快照，再重新配置。'),
      row('规则更新被跳过', '订阅或脚本关联已变化，重新配置一次即可恢复更新。'),
      row('新电脑无法读取 Secret Gist', '检查能否访问 gist.githubusercontent.com（工具会先直连，失败后尝试本机 Clash 端口）；请粘贴 Gist 页面地址或 android-clash-meta.yaml 的 Raw 地址（不要贴 iOS CONF 链接）。'),
      row('源电脑换节点后怎么同步', '源电脑点「更新 Secret Gist」后，另一台电脑在「从 Secret Gist 导入配置」选中已保存配置，点「从原 Secret Gist 重新拉取最新节点」再重新配置即可。'),
      row('界面连不上', '关闭窗口后重新双击「一键网络.cmd」；命令行备用：一键网络.cmd --menu。'))));
  root.append(ui.disclosure({ title: '更新校验' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只刷新国内域名、国内 IP、私有域名和局域网名单，不改动内核和固定节点。'),
      ui.el('li', {}, '名单增删异常或策略变化时自动停止，保留原配置。'),
      ui.el('li', {}, '发布前先校验完整配置，失败自动回滚；定时任务仅在当前用户登录时运行。'))));
  root.append(ui.disclosure({ title: '恢复边界' },
    ui.el('ul', { class: 'check-list' },
      ui.el('li', {}, '只还原由本工具修改的系统设置、防火墙和客户端脚本关联。'),
      ui.el('li', {}, '保留你的订阅列表、节点更新和手动修改；不关闭外部浏览器，不删除用户资料。'),
      ui.el('li', {}, '恢复会移除定时更新任务，且不自动运行网络验收。'))));
  root.append(ui.disclosure({ title: '当前配置与浏览器详情' }, ui.el('div', {id: 'help-configuration', class: 'stack'}, ...configurationDetails(ctx))));
  root.append(ui.disclosure({ title: '软件版本与更新检查' }, ui.el('div', {id: 'help-software'}, softwareVersion(ctx, {id: 'help-check-software-update'}))));
  root.append(updateInstructions());
  root.append(packageCard(ctx));

  return root;
}

export function refreshStatus(ctx) {
  const configuration = document.getElementById('help-configuration');
  const state = ctx.state || {};
  const configurationSignature = JSON.stringify([state.configured, state.plan, state.service, state.baseline,
    state.last_run, state.verification, state.preview?.current?.health, state.preview?.current?.webrtc]);
  if (configuration && configuration.dataset.signature !== configurationSignature) {
    configuration.dataset.signature = configurationSignature;
    configuration.replaceChildren(...configurationDetails(ctx));
  }
  const software = document.getElementById('help-software');
  if (software) {
    const signature = JSON.stringify([ctx.state?.software, ctx.softwareChecking]);
    if (software.dataset.signature === signature) return;
    software.dataset.signature = signature;
    const focused = software.contains(document.activeElement) && document.activeElement.id === 'help-check-software-update';
    software.replaceChildren(softwareVersion(ctx, {id: 'help-check-software-update'}));
    if (focused) software.querySelector('#help-check-software-update')?.focus({preventScroll: true});
  }
}

function row(left, right) {
  return ui.el('div', { class: 'table-row' }, ui.el('span', { text: left }), ui.el('span', { text: right }));
}
