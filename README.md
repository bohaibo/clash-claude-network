# Windows Claude 网络环境配置与恢复

本项目用于 Windows 11 + Clash Verge Rev / Mihomo：国内域名/IP 直连，Claude/Anthropic 优先固定代理，其余流量进入用户指定的唯一海外节点；节点故障不回落直连。它包含 AI 智能体 skill、可审核的配置生成器、详细说明及一个带配置/恢复菜单的一键入口。

**不安装 Claude、不处理账号、不承诺不封号、不伪造浏览器指纹。** 仅 Windows 适配器在支持范围内。默认要求出口为 SG；节点名、旗帜、ASN 或第三方评分不能代替实际出口验证，也不能证明账号符合服务地区政策。

## 在另一台 Windows 电脑复用

把私有仓库授权给你的 AI 智能体，然后发送：

> 阅读 https://github.com/bohaibo/clash-claude-network 中的 SKILL.md 和 README.md，根据其中流程配置这台 Windows 电脑。使用我已有订阅中的节点「填写完整节点名」，出口要求新加坡。保留全部原节点、订阅、局域网规则和浏览器数据。先发现实际配置并备份，生成候选再应用，完成验证，留下完整记录和一个带菜单的一键入口。遇到节点歧义或缺少必要依赖时向我确认，不要猜测。不要安装或登录 Claude。

第一次执行仍可能需要你提供订阅归属、允许管理员授权、确认严格防火墙影响及保存浏览器网页。项目地址和节点名不能替代缺失的订阅凭据、管理员权限或客户端安装。**私有仓库需要相应 GitHub 访问权限**，不是公开下载链接。

前置依赖：已安装并正常运行 Clash Verge Rev、Mihomo 服务、Node.js、Python 3.10+ 与 PyYAML。可在审阅后通过 `python -m pip install -r requirements.txt` 安装 Python 依赖。脚本不会自动下载未知软件，也不会替你添加一个不存在的节点。

## 一键入口

根目录只有 **一键网络.cmd**，运行后按菜单选择：

- **1. 配置**：新电脑询问精确节点名，生成、校验、备份并应用配置；之后执行网络/断网测试。高级用法：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run.ps1 -Action apply -Node "节点完整名称" -Region SG`。同名节点可加 `-Subscription "订阅名称或UID"`。
- **2. 恢复**：校验备份，恢复到本机第一次配置前的客户端、系统、浏览器设置，并移除本项目更新任务与防火墙规则。
- **0. 退出**。

两个操作都需要管理员权限。浏览器会关闭并尝试恢复原标签页，请先保存网页内容。重启 Windows 后 IPv6/语言策略才能完整生效。不要移动或删除 `.local`；它被 Git 排除，包含本机恢复所必需的原始记录。

不需要处理浏览器时，给高级命令加 `-SkipBrowser`：浏览器策略和语言字段不改动，WebRTC、语言、指纹相关验证项保持“未测试”，不能声称已全面防护。本项目只处理普通 Chrome/Edge，不配置指纹浏览器。

恢复目标固定为**本机第一次执行 apply 之前**自动拍下的基线：`.local/baseline` 里包含客户端目录、系统设置原值、防火墙导出和哈希清单。这份基线只建立一次，之后只读不覆盖；恢复时也只使用它，不会去找更早的历史包、别处的快照或“上一次配置前”的状态。不要把 `.local` 复制到另一台电脑使用。

### 恢复后 IPv6 仍未启用怎么办

先分清两种失效，它们的表现一样（`netsh interface ipv6 show interfaces` 只剩 Loopback），原因完全不同：

1. **网卡绑定被禁用**。旧版本只按网卡名称回放快照，而 `Get-NetAdapterBinding -Name` 支持通配符（Windows 虚拟网卡名形如 `本地连接* 1`），匹配失败会静默 `continue`；apply 之后新建或重建的网卡也不在快照里，于是绑定一直保持禁用。现在改为按 `InterfaceGuid` 精确匹配，并额外记录 apply 实际禁用了哪些网卡。
2. **Tcpip6 驱动被设为手动/禁用启动**。此时注册表里 `UpperBind`、`Linkage\Bind`、`DisabledComponents` 可能全都正常，但驱动不加载，IPv6 协议栈整体不存在，任何 `Enable-NetAdapterBinding` 都不会有效果。恢复流程现在会检查 `HKLM\SYSTEM\CurrentControlSet\Services\Tcpip6` 的 `Start` 值，发现是 3/4 时改回 1。

已经被旧版本恢复过、但 IPv6 仍未启用的机器，可以单独修复，不必重跑整套恢复：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/system.ps1 -Action repair-bindings
```

需要管理员权限，执行后重启 Windows。用 `netsh interface ipv6 show interfaces` 能重新列出物理网卡即表示恢复成功。若个别网卡仍报失败，说明它的 TCP/IPv6 组件是被移除而非禁用，此时才需要 `netcfg -d` 加重启（会重置全部网络配置）。

## 每一步做什么、有什么用

| 步骤 | 修改及用途 | 影响与边界 |
|---|---|---|
| 环境发现 | 读取实际订阅、增强脚本、生成配置、运行内核路径与控制通道 | 不凭常见路径覆盖陌生客户端；缺少内核或歧义节点就停止 |
| 首次备份 | 保存完整客户端目录、系统各项原值、原防火墙导出和出站允许规则、代理设置、浏览器语言/策略；建立哈希清单 | 含敏感数据，仅保存在 `.local`，不能提交 Git；首次备份不覆盖 |
| 固定节点 | 新建只包含指定节点的 select 组，不含 DIRECT 或 REJECT，不自动轮换 | 节点名固定不等于出口 IP 永久固定；国家不符合要求时校验失败 |
| 局域网优先 | 保留真实私有地址及原已审核 LAN 规则 | 从 LAN 规则排除 `198.18.0.0/15` fake-IP 地址池，避免误直连 |
| Claude 优先 | 核心域名、认证/CDN/监控补充域名、Windows Claude.exe/claude.exe，以及官方入站 IPv4 段先于国内规则 | 不把所有 node.exe、浏览器和终端进程统统强制代理；共享监控域名会影响其他软件使用这些服务的连接 |
| 国内分流 | pluwen 域名名单先转换为 domain；Loyalsoldier cncidr 用 ipcidr，private 用 domain，lancidr 用 ipcidr | 名单不是对全部中国网站的地理证明；不把 private.txt 误当 IP 列表 |
| 海外兜底 | 最后一条 MATCH 指向固定节点组 | 未知海外流量不会因遗漏单条规则回落直连 |
| DNS | fake-ip；默认 Cloudflare/Google DoH 经固定节点，国内名单单独使用国内 DoH；保留本地域名解析 | 沿用“默认海外”的策略，不改成所有域名先询问国内 DNS |
| DNS Type 64/65 | 在每条上游解析路径附加 `disable-qtype-64=true` 和 `disable-qtype-65=true`，覆盖默认/直连/节点域名/系统私有域名策略 | SVCB/HTTPS 查询得到空响应；这不是拦截 64/65 端口。可能失去 ECH 与部分服务发现优化，不关闭 TLS 证书校验，也不等于关闭全部 QUIC |
| TUN | mixed、auto-route、auto-detect-interface、strict-route，接管 TCP/UDP 53 | 接管普通 Windows 程序、curl/git/npm 等；其他 VPN、WSL、容器转发和应用自带加密 DNS需另行实测 |
| IPv6 | Clash/DNS IPv6 关闭；系统 DisabledComponents=255，禁用现有网卡 IPv6 绑定，并把实际被禁用的网卡清单写入 `.local/baseline/disabled-by-apply.json` | 需要重启；新网卡出现后重新检查。恢复时按网卡 GUID 精确还原，并补启 apply 之后新增的网卡；若原始基线本身是在 IPv6 已禁用状态下采集的，恢复为可用而不是回放该禁用状态 |
| 代理清理 | 关闭系统/WinHTTP 代理，清理 HTTP(S)/ALL/NO_PROXY 及 git/npm/Docker 客户端代理覆盖 | 保存原值。已打开的进程可能保留旧环境变量，需重新启动 |
| 严格防火墙 | 默认出站 Block，禁用原本地出站 Allow，保留核心、TUN、LAN、DHCP 必要路径 | 核心程序本身允许出站，必须保持正确配置；动态共享服务、管理员软件及将来新增允许规则仍需审核 |
| 核心故障保护 | 节点坏但核心运行时国内仍直连；整个核心停止时所有公网阻断 | 后一种情况下国内公网也会断。这是有意的严格行为，并非误报 |
| 启动与维护 | 核心服务设为自动启动；国内来源每日 13:00 在当前用户登录期间更新 | Windows 不保证所有开机程序的绝对先后顺序；靠出站阻断保护核心就绪前的流量 |
| 浏览器 | Chrome/Edge WebRTC 限制、关闭浏览器独立 DoH、直接交给 TUN、首选语言 zh-SG/en-SG/zh-CN | 策略可能受个人账号/管理状态影响，需验证实际网页。普通浏览器 Intl 默认 locale 可能仍为 zh-CN，不能伪称已全部变为 zh-SG |
| 区域语言 | Singapore Standard Time、zh-SG、新加坡语言偏好，尽量保留中文 IME | 不删除字体、不安装指纹扩展、不清理登录/Cookie；语言不能改变真实 IP |
| 更新审核 | 规则格式、规模变化、私有地址变更、fake-IP 排除与核心策略做检查；失败保留旧配置 | 第三方 Claude 候选只下载到 `.local` 待审核，不自动合并关键词、ASN 和未知共享域名 |

## 规则顺序

节点连接所需的窄范围例外 / 真实 LAN、私有域名与地址 → 拒绝公网 IPv6 → Claude 进程、官方入站 IP、核心及补充域名 → 国内域名 → 国内 IP 与 GEOIP,CN（no-resolve）→ MATCH 固定海外组。

代理组只能包含指定的实际节点，不能经由另一个可能选 DIRECT 的组。严格模式不是“开启系统代理”；浏览器的 ProxyMode=direct 是交给本机 TUN，不意味着绕过 TUN 直连公网。

## DNS 64/65 与“所有网卡”的准确含义

过滤设置位于统一的 Mihomo 解析器，作用于进入 TUN/该解析器的流量，不是给每块网卡写不存在的 Windows qtype 防火墙选项。切换以太网/Wi-Fi 时，只要仍被 TUN 接管，相同过滤仍有效。关闭或未连接的网卡无法做联网实测。

**不能按 DNS qtype 检查任意端到端加密 DoH/DoT/DoQ 流量。** 未经解密看不到其中的 DNS 类型。本项目不安装根证书、不做 TLS 中间人解密，也不宣称能过滤未来所有应用自带的加密解析器。浏览器独立 DoH 被关闭，其他加密 DNS应用需要单独审核。

## 备份与恢复的具体含义

新电脑首次备份在 `.local/baseline`，包含机器和用户绑定以及校验清单；后续应用只复用，不覆盖。恢复只还原本项目记录过的项目，不是 Windows 出厂重置，也无法还原从未记录的历史人为操作。恢复浏览器只恢复对应语言字段/策略，不拿旧浏览器整个目录覆盖当前数据。

客户端源订阅原文件不修改；仅关联一个增强脚本并生成运行配置。新建的脚本会记录在计划中，恢复时只删除本项目明确创建的文件。未知节点、订阅和后续新增文件不会被批量删除。

防火墙完整导出用于留档；主恢复路径按已记录的配置文件默认动作及原允许规则逐项恢复。本机此前完整 .wfw 导入曾失败，所以不把“能导出”当成“导入已验证”。

## 验证与成功标准

`scripts/verify.py` 自动检查以下项目，结果写入 `.local/verification.json`：

- 百度、淘宝返回 200/301/302，连接记录确认 DIRECT；`ipinfo.io` 的出口国家必须等于 `-Region`。
- `claude.ai` 有 HTTP 响应；403、登录页或 API 错误只说明传输可达，不视为应用可用。
- 向 `8.8.8.8:53` 发 TCP/UDP 的 A、64、65 查询：A 必须有结果，64/65 必须为空；并确认 `claude.ai` 得到 `198.18.0.0/16` 的 fake-IP，证明解析确实进入 TUN，而不是直连外部解析器。
- 原生 UDP STUN 返回映射地址；清空代理环境变量后 `git ls-remote`、`npm ping` 可用。
- IPv6 公网地址不可达。
- 模拟选定节点失败：海外请求失败、国内仍可访问，`finally` 中恢复节点并复测出口。
- 停止核心：国内外公网都不可连接，重启后恢复。`verify_core_exit.py` 记录 `domestic_also_blocked`，明确这是全阻断模式，不是“国内仍可用”。

以下项目脚本不代测，必须人工完成或标记为未验证：

- 实际连接元数据里的每一条代理链（脚本只核对固定组与出口国家）。
- 浏览器 WebRTC、语言、时区、指纹：在真实配置文件里用 browserleaks.com/webrtc、browserleaks.com/javascript、CreepJS 查看。
- Docker/WSL/Tailscale 及其他虚拟网卡：必须在各自运行环境内测出口，宿主机 curl 不能代替。
- 任意应用自带的加密 DNS（DoH/DoT/DoQ）：未解密看不到 qtype，不在过滤证明范围内。
- `python scripts/test_dns_filter.py` 是隔离正样本实验，用于排除“域名本来没有 64/65 记录”的假阳性，不修改运行配置。

脚本记录“通过/失败/未测试”，不把未知状态写成成功。断网测试会短暂中断正在使用代理的连接。新的 Windows 适配器做过本机候选生成、Mihomo 校验和离线回归，但没有在另一台全新 Windows 上完成完整往返，初次部署仍须按清单验收。

## 安全发布与引用来源

`.local`、真实节点/订阅、控制密钥、浏览器原文件、备份和实测 IP日志禁止提交，即使仓库为私有。提交前运行 `python scripts/check_project.py`，并检查 Git 待提交文件。私有不等于允许上传凭据。

来源：
- https://github.com/pluwen/china-domain-allowlist （SwitchyOmega 原始名单，需转换）
- https://github.com/Loyalsoldier/clash-rules （cncidr、private、lancidr）
- https://github.com/xiaolai/anthropic-claude-surge-rules-set （按来源说明审核，不直接使用格式不匹配的生成文件）
- https://github.com/VPSDance/ai-proxy-rules （候选补漏，不全量取并集）
- https://wiki.metacubex.one/config/dns/ （disable-qtype 附加参数）
- https://platform.claude.com/docs/en/api/ip-addresses （客户端入站与服务端出站网段区别）
- https://learn.microsoft.com/en-us/deployedge/microsoft-edge-browser-policies/webrtclocalhostiphandling
- https://github.com/chromium/chromium/blob/main/components/policy/resources/templates/policy_definitions/Miscellaneous/ForcedLanguages.yaml

下载来源按各自许可证使用。本项目不把第三方完整规则数据纳入 Git；生成时记录来源及 SHA-256，随配置保存在本机。


本次实测与未验证项见 [VALIDATION.md](VALIDATION.md)。本机基线位于 `.local/baseline`，不会上传 GitHub。
