# Windows Claude 网络环境

Windows 11 + Clash Verge Rev / Mihomo 的本地网络配置工具：国内域名/IP 直连，Claude/Anthropic 优先固定代理，其余流量进入指定海外节点；节点故障不回落直连。

项目包含：

- 本地图形界面（GUI），覆盖配置、补充、恢复、快照、重新配置、规则更新和日志；
- 独立资料目录的浏览器沙箱，用于 Claude 网页；
- 可审核的 Clash 配置生成器、恢复点和离线测试；
- 项目内运行环境（Python 3.13 + PyYAML + Node.js），不污染系统。

> 本项目不安装或登录 Claude，不处理账号，不保证账号可用性，不伪造浏览器指纹。仅支持 Windows。

## 安装

详见 [INSTALL.md](INSTALL.md)。

1. 手动安装 **Clash Verge Rev** 及其内核，启动服务并导入订阅。本项目不会安装 Clash。
2. 双击根目录 **一键网络.cmd**。
3. 首次运行自动把 Python 3.13、PyYAML、Node.js 下载到 `.runtime/`；不修改系统 PATH，不安装到系统目录。
4. 界面打开后选择节点、确认影响并配置。

浏览器沙箱使用本机已安装的 Edge 或 Chrome，不下载、不修改其默认资料。

## 图形界面

根目录只有 **一键网络.cmd** 一个用户入口；`一键网络.cmd --menu` 是命令行备用入口。

| 页面 | 功能 |
|---|---|
| 一键配置 | 读取订阅和节点，自动填出口地区，确认后配置 |
| 网络环境 | 当前节点、分流顺序、上次验收、补充配置、连通性检查、恢复并重配置 |
| 浏览器沙箱 | 打开 Claude、打开实测页、语言/引擎、设为默认浏览器、关闭/重置 |
| 快照与恢复 | 保存快照、选择恢复点、查看配置关联 |
| 规则更新 | 手动/定时、更新间隔、立即更新、任务状态 |
| 日志与验证 | 上次操作、验收摘要、本地日志、重配置记录 |
| 使用说明 | 操作边界和故障处理 |

界面服务只监听 `127.0.0.1` 的随机端口，使用一次性令牌、`SameSite=Strict` 会话 Cookie、CSRF 头、同源 `Origin` 和 CSP。界面以普通用户运行；配置/补充/恢复/快照/重配置在点击后请求管理员权限。

一键配置页在节点选择之前展示当前系统配置与完整变更说明；选择节点或修改出口地区后，动态显示“配置后会：当前值 → 目标值”。现值只读采集（最多缓存 30 秒），无法读取的项目标为未验证；代理只显示字段数量等摘要，不显示凭据。系统显示语言保持原样，区域格式与沙箱浏览器语言按地区同步。

## 配置内容

| 项目 | 行为 |
|---|---|
| 固定节点 | 创建只包含指定节点的 select 组，不含 DIRECT 或自动轮换 |
| Claude 优先 | 核心/补充域名、Claude 进程和官方入站 IP 先于国内规则 |
| 国内分流 | 已审核国内域名、国内 IP 与 GEOIP,CN 直连；IP 规则使用 no-resolve |
| 海外兜底 | MATCH 指向固定节点组，不回落 DIRECT |
| DNS | fake-IP；默认海外 DoH，国内名单使用国内 DoH；所有真实解析路径附加 Type 64/65 过滤 |
| TUN | mixed、auto-route、strict-route；接管 TCP/UDP 53 |
| IPv6 | Clash/DNS 关闭，系统全局关闭并关闭所有现有网卡 IPv6 绑定；需重启 |
| 代理清理 | 清理系统、WinHTTP、环境变量和 Git/npm/Docker 代理覆盖 |
| 严格防火墙 | 默认出站 Block，保留核心、TUN、LAN、DHCP 必要路径，阻断 SharedAccess 服务出站 |
| 系统自动定位 | 使用 Windows DisableLocation 策略关闭系统定位；恢复时还原原值（原无此项则删除），不强制开启原本关闭的定位 |
| 自动时区与自动时间 | 关闭自动时区服务及 Windows 自动校时（NoSync），保存服务启动方式、运行状态和原校时模式，恢复时逐项还原 |
| 区域与时区 | 按所选节点地区设置系统国家或地区、区域格式和时区，沙箱浏览器语言同步；不修改 Windows 显示语言 |

严格防火墙下：节点故障但核心仍在运行时，国内网站保持直连；**核心停止时所有公网（包括国内）都会被阻断**。这是保护行为。

## 出口地区自动识别

选择节点后，界面会从节点名和订阅名识别国家/地区并自动填入出口地区，例如 `demo-sg-01`、`🇸🇬 Singapore`、`US-01`、`demo-US`。识别只使用名称提示，不是实际探测；**实际出口由配置后的网络验收核对**。识别错误或无法识别时可以手动选择。

一键配置/补充配置会关闭自动时区和自动校时，将系统国家或地区、日期/数字等区域格式及系统时区切换为所选节点地区，例如 US → America/New_York，SG → Asia/Singapore；Claude 桌面、Claude 网页和沙箱浏览器都读取同一系统时区。沙箱浏览器语言同步为对应语言（US → en-US，SG → zh-SG），语言在重新打开沙箱后生效。当地时间由当前系统时钟按目标时区显示，不修改绝对时间，不按时差额外拨动时钟。关闭自动校时后长期使用可能产生误差。国家多时区时使用该国预设时区，并非探测节点城市；系统时区/Claude 桌面通常需要重启后完全生效。

## 规则更新

在「规则更新」选择：

- **手动**：不注册后台任务；打开软件后点击「立即更新」。
- **定时**：注册当前用户的 Windows 计划任务。可设置：
  - 每天、每 3 天、每周、每月、每半年；
  - 或自定义 1–365 天的间隔；
  - 指定更新时间（HH:MM）。

定时任务只在当前用户登录期间运行。更新只刷新已审核的国内/私有来源，不更新内核或节点；来源变化过大、Claude 策略变化或脚本关联改变时会停止并保留旧配置。第三方 Claude 候选只下载到 `.local` 待审核，不自动合并。

配置流程读取界面选择；恢复流程移除任务；界面可立即更新并显示上次结果和下次运行时间。

## 浏览器沙箱

Claude 使用独立资料目录：

- 沙箱：`.local/browser/profile`
- 界面：`.local/gui/browser-profile`

本项目不再写入或编辑默认 Chrome/Edge 的策略、资料首选项或语言字段，也不关闭默认浏览器。启动参数包含：

```text
--user-data-dir=<沙箱资料>
--force-webrtc-ip-handling-policy=disable_non_proxied_udp
--disable-non-proxied-udp
--disable-quic
--lang=zh-SG
```

资料首选项只管理 DoH（关闭）、WebRTC 非代理 UDP、语言、欢迎页和浏览器登录/同步键。一键配置会按节点地区设置系统时区与沙箱语言，使 Claude 桌面和沙箱浏览器保持一致。

这是资料目录隔离，不是操作系统级沙箱，也不是指纹伪装。扩展、其他浏览器、Docker/WSL、虚拟机和账号行为仍需另行验证。

启动后会确认浏览器确实创建了可见窗口；只有后台进程残留不算打开成功。所选引擎在时限内没有窗口时会改用另一个已安装引擎，并在界面提示实际使用的引擎和退出代码。

### 浏览器实测页

「浏览器沙箱 → 打开实测页」会显示：

- 网页语言、语言列表、Intl locale、时区与预期是否一致；
- 公网 IP、出口国家、城市、网络组织，并与配置地区比较；
- WebRTC 泄漏检测：通过 STUN 获取公网反射地址，与公网出口 IP 比较；不同判为泄漏，相同则未发现绕过；同时标出 host、srflx、relay；
- Canvas 哈希、WebGL 厂商/渲染器、屏幕、CPU、内存、UA、WebDriver。

出口接口使用 `ipapi.co` / `ipinfo.io`；失败时显示“未检测”，不会假装通过。无 WebRTC 候选时显示“未发现公网候选”，表示未观察到公网地址，但可能是策略/网络阻断了 STUN，不能据此证明完全阻断。自检页不能证明 DNS 泄漏或账号可用性。

### 设为默认浏览器

「浏览器沙箱 → 设为默认浏览器」会在当前用户注册一个 `Claude 沙箱浏览器` 启动器，并打开 Windows 默认应用设置。用户在设置中确认后，点击 http/https 链接会用沙箱资料目录打开。只写 `HKCU`，不修改系统级关联；Windows 不允许静默修改默认浏览器，必须手动确认。

注册失败时会指出具体无法写入的注册项。若界面提示进程运行在“受限环境（低完整性）”中，说明项目目录被安全/沙箱工具打上了低完整性标签：目录内的 Python 无法写 `HKCU`，Chrome 也会在创建窗口前退出。在项目根目录执行下面的命令（不需要管理员）恢复中完整性标签，再重新打开界面：

```powershell
icacls . /setintegritylevel "(OI)(CI)Medium"
```

## 快照与恢复

- 第一次配置前自动保存原始恢复点到 `.local/snapshots/<时间戳>/`，之后沿用同一个首次恢复点，不覆盖。
- 「保存当前快照」按当前状态新增一份，不覆盖旧快照。
- 恢复按时间倒序列出全部快照，选择要恢复的一份；保留当前订阅、节点更新、删除和用户后来选择的脚本。
- 恢复不运行网络验收；IPv6 和语言通常需要重启 Windows 才完全生效。
- 恢复会移除本项目的定时更新任务；需要时在「规则更新」重新保存。
- 配置、补充、恢复、快照和定时更新共用 `.local/operation.lock`，同一时间只执行一项。

定位原值单独保存在恢复点的 `location.json`，重复配置不覆盖。旧恢复点在首次使用新版配置/补充时补存定位改动前状态，不改写原始系统基线；没有定位记录的旧恢复点不更改定位并显示提示。定位策略可能需重启或重新打开应用后生效；IP 推断位置、浏览器和虚拟机实际行为未验证。

国家/自动时间新增恢复信息保存在恢复点的 `region-time.json`，并在改动前封存。包括原国家 GeoID、区域格式、时区、两个时间服务的启动方式/运行状态/延迟启动项以及自动校时模式；原本不存在的值在恢复时删除。旧恢复点仅补存以前未管理的项目，原有时区/区域格式仍采用首次系统记录，不覆盖基线。恢复不会把时钟拨回备份日期；自动校时原本开启则还原该配置，能否实际联网校时须实测。缺少新增记录的旧恢复点会提示并跳过新增项目；记录损坏或恢复校验失败会报告未完成，不报告成功。

旧版快照可能包含浏览器策略/语言记录；新版本不会应用或恢复这些条目，也不会写入或关闭默认浏览器。旧版已写入的默认浏览器策略不会自动撤销。

## 恢复与故障处理

| 情况 | 处理 |
|---|---|
| 缺少 Python / Node | 重新运行 `一键网络.cmd`，自动安装到 `.runtime` |
| 取消管理员授权 | 不执行操作；重新点击并在 UAC 确认 |
| 节点不存在或重名 | 在「一键配置」重新选择；不会自动替换 |
| 预检失败 | 查看界面错误和 `.local/last-error.log`；通常无需恢复 |
| 写入后失败 | 在「快照与恢复」恢复关联快照 |
| 防火墙回滚失败 | 公网可能仍被阻断；在「快照与恢复」重试，见 `.local/rollback-error.log` |
| 网络验收失败 | 查看失败项；不使用上一次成功记录 |
| 规则更新失败/跳过 | 保留旧配置；查看「规则更新」或 `.local/update-result.json` |
| 沙箱浏览器打不开 | 确认 Edge/Chrome 已安装；查看 `.local/browser/engine.log`。提示“退出代码 0x80000003”说明浏览器进程在创建窗口前中止，通常来自低完整性环境或浏览器损坏 |
| 提示“受限环境（低完整性）” | 项目目录被安全/沙箱工具标记为低完整性；在项目根目录执行 `icacls . /setintegritylevel "(OI)(CI)Medium"` 后重新打开界面 |
| 双击后窗口关闭、未看到界面 | 启动器会确认本地服务和独立浏览器就绪；失败时保留错误窗口。查看 `.local/gui/startup.stderr.log` 和 `.local/gui/server.log` |
| 设为默认浏览器无变化 | 在 Windows 设置 → 默认应用中手动确认 |
| 沙箱语言/时区与节点地区不一致 | 关闭并重新打开沙箱浏览器；系统时区或 Claude 桌面可能需重启 |
| 界面一直显示“操作进行中”，但控制台已关闭 | 若任务已有错误/完成事件，点击任务面板的「结束卡住任务」；仍不行时在任务管理器结束运行 `run.ps1` 的 `powershell.exe`，再重新打开界面 |

## 验证

`scripts/verify.py` 检查：

- 国内网站 HTTP 200；
- 海外出口国家与所选地区一致；
- DNS A 有结果，64/65 为空；`claude.ai` 得到 fake-IP；
- 公网 IPv6 不可达；
- UDP STUN 出口与节点路由；
- Git/npm 出口；
- 节点故障时海外阻断、国内保留；
- 核心停止时国内外公网都阻断，重启后恢复。

不测试或需人工确认：

- Claude 网页/账号可用性；不安装、不登录 Claude；
- 沙箱浏览器实际 WebRTC/DNS/指纹和扩展行为；
- Docker/WSL/虚拟机内部出口；
- 任意应用自带加密 DNS。

## 本地运行环境与测试

- 运行环境：`.runtime/python313`、`.runtime/node`、`.local/env.json`；全部在项目目录。
- 离线回归：

```powershell
.\.runtime\python313\python.exe -m unittest discover -s scripts -p "test_*.py"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_firewall_rules.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_system_logic.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_location_logic.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_region_time_logic.ps1
.\.runtime\python313\python.exe scripts/check_project.py
```

- 开发界面：`.\.runtime\python313\python.exe scripts/gui_server.py --open --no-idle-exit`；正常使用请用 `一键网络.cmd`。
- `.local` 和 `.runtime` 均被 Git 忽略。

## 来源

- https://github.com/pluwen/china-domain-allowlist
- https://github.com/Loyalsoldier/clash-rules
- https://github.com/xiaolai/anthropic-claude-surge-rules-set
- https://github.com/VPSDance/ai-proxy-rules
- https://wiki.metacubex.one/config/dns/
- https://platform.claude.com/docs/en/api/ip-addresses
- https://peter.sh/experiments/chromium-command-line-switches/

本机实测记录与测试日志仅保存在未跟踪的 `.local/` 中，不随仓库发布。
