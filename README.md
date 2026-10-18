# Windows Claude 网络环境配置与恢复

本项目用于 Windows 11 + Clash Verge Rev / Mihomo：国内域名/IP 直连，Claude/Anthropic 优先固定代理，其余流量进入用户指定的唯一海外节点；节点故障不回落直连。它包含 AI 智能体 skill、可审核的配置生成器、详细说明及两个一键入口。

**不安装 Claude、不处理账号、不承诺不封号、不伪造浏览器指纹。** 仅 Windows 适配器在支持范围内。默认要求出口为 SG；节点名、旗帜、ASN 或第三方评分不能代替实际出口验证，也不能证明账号符合服务地区政策。

## 在另一台 Windows 电脑复用

把私有仓库授权给你的 AI 智能体，然后发送：

> 阅读 https://github.com/bohaibo/clash-claude-network 中的 SKILL.md 和 README.md，根据其中流程配置这台 Windows 电脑。使用我已有订阅中的节点「填写完整节点名」，出口要求新加坡。保留全部原节点、订阅、局域网规则和浏览器数据。先发现实际配置并备份，生成候选再应用，完成验证，留下完整记录和两个一键入口。遇到节点歧义或缺少必要依赖时向我确认，不要猜测。不要安装或登录 Claude。

第一次执行仍可能需要你提供订阅归属、允许管理员授权、确认严格防火墙影响及保存浏览器网页。项目地址和节点名不能替代缺失的订阅凭据、管理员权限或客户端安装。**私有仓库需要相应 GitHub 访问权限**，不是公开下载链接。

前置依赖：已安装并正常运行 Clash Verge Rev、Mihomo 服务、Node.js、Python 3.10+ 与 PyYAML。可在审阅后通过 `python -m pip install -r requirements.txt` 安装 Python 依赖。脚本不会自动下载未知软件，也不会替你添加一个不存在的节点。

## 两个一键入口

- **一键配置.cmd**：新电脑询问精确节点名，生成、校验、备份并应用配置；之后执行网络/断网测试。高级用法：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run.ps1 -Action apply -Node "节点完整名称" -Region SG`。同名节点可加 `-Subscription "订阅名称或UID"`。
- **一键恢复.cmd**：校验备份，恢复本项目修改前记录的客户端、系统、浏览器设置，并移除本项目更新任务与防火墙规则。

两者需管理员权限。浏览器会关闭并尝试恢复原标签页，请先保存网页内容。重启 Windows 后 IPv6/语言策略才能完整生效。不要移动或删除 `.local`；它被 Git 排除，包含本机恢复所必需的原始记录。

本机沿用 `.local/legacy-binding.json` 指向的最早恢复基线，不把本次较新的状态当作“最初状态”。新电脑没有这个私密桥接文件，自动建立自己的首次基线。不要把本机 `.local` 复制到另一台电脑使用。

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
| IPv6 | Clash/DNS IPv6 关闭；系统 DisabledComponents=255，禁用现有网卡 IPv6 绑定 | 需要重启；新网卡出现后重新检查。恢复时按原网卡记录还原，不启用原先已关闭的绑定 |
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

- 国内网站返回正常响应，连接记录确认 DIRECT；外国 IP 查询返回目标国家。
- Claude/Anthropic 连接记录确认固定节点；HTTP 403、登录页面或 API 错误不视为应用可用。
- 向本地及外部 53 端口分别发 TCP/UDP A、64、65 查询；A 必须有结果，64/65 必须为空。
- 用 `python scripts/test_dns_filter.py` 做已知正样本实验：临时本机上游提供真实 64/65 回应，隔离内核必须拦截且不转发相应问题；避免把自然无记录误判为过滤成功。实验不会改动实际 TUN/运行配置。
- IPv6 公网连接失败；重启后重新核查地址和默认路由。
- 模拟选定节点失败：海外请求失败，国内网站仍可访问；始终在 finally 中恢复。
- 停止核心：国内/国外公网都不能连接；重新启动核心后网络恢复。
- 在实际浏览器用 browserleaks.com/webrtc 和 browserleaks.com/javascript 查看 IP、WebRTC、语言、时区。原生 STUN、文件内容或注册表值不能替代这一步。
- Docker/WSL、Tailscale、其他 VPN及新虚拟网卡不自动算通过，必须运行真实工作负载。

脚本记录“已验证/失败/未验证”，不把未知状态写成成功。断网测试会短暂中断正在使用代理的连接。新的 Windows 适配器做了本机候选生成、Mihomo 校验和离线回归；没有在另一台全新 Windows 上完成完整往返，因此初次部署仍须 AI 按清单验收。

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


本次实测与未验证项见 [VALIDATION.md](VALIDATION.md)。本机最初备份现位于 `.local/legacy-package/Final/backup`，不会上传 GitHub。
