---
name: clash-claude-network
description: Configure/restore a Windows Clash Verge Rev/Mihomo network with one exact overseas node, Claude-priority routing, TUN, DNS Type 64/65 filtering, IPv6 off, strict firewall, reversible WebRTC policies for standard browsers, and a local GUI with rule updates. Does not install Clash or Claude or guarantee account eligibility. Fingerprint browsers are managed externally.
---

# Windows Claude network environment

Windows 11 only. Read [README.md](README.md), [INSTALL.md](INSTALL.md), and [references/windows.md](references/windows.md) before changing a machine.

禁止连接 Claude 进行测试：开发、回归和验收只允许对 Claude/Anthropic 使用离线样例和 mock，不访问真实 HTTP/API/WebSocket，不查询相关域名 DNS，不打开 Claude 网页、桌面程序或登录页，也不为测试启动浏览器。真实 Claude 路由、应用和账号行为一律标为未验证。

- The human supplies an **exact existing node name**. Never invent, replace or guess nodes. Default exit country is SG.
- The root `一键网络.cmd` opens the GUI; `--menu` is the console fallback.
- Python 3.13, PyYAML and Node.js live in project `.runtime`; never install them system-wide or modify PATH. Run `scripts/bootstrap.ps1` if `.runtime` is missing.
- Missing runtimes resolve latest compatible official releases (Python 3.13 Windows embedded, Node LTS x64, requirements-constrained PyYAML). Verify archive SHA256 against official release metadata; fetch current get-pip over official HTTPS when pip is missing. Reuse working runtimes. Actual source/digest records belong in `.local/runtime-downloads.json`; `assets/runtime-provenance.json` is historical reference only.
- Do not configure or launch a browsing profile, change browser language, or register a default browser. The GUI uses `.local/gui/browser-profile` only for the loopback management window. Preserve legacy browser data and policy backups for recovery.
- The GUI is loopback-only and non-admin; admin actions are delegated to `scripts/run.ps1` with UAC. Keep it bound to `127.0.0.1`.

Workflow:

1. `python scripts/client.py discover` shows subscriptions/node names without credentials.
3. Windows Settings → Time & language → Language & region → Regional format follows the selected region's culture (SG → zh-SG, US → en-US). Keep Locale and LocaleName consistent, verify in a fresh process, and restore the pinned original culture. First apply creates the original snapshot automatically. Later applies reuse the pinned first recovery point; manual snapshots are additive. Never overwrite the baseline with the current state.
4. Apply: `scripts/run.ps1 -Action apply -Node "<exact>" -Acknowledged` (or the GUI). Use `-Subscription` for duplicate names and `-Region` when the hint is wrong.
5. Apply/supplement block all non-project outbound allows, close IPv6 bindings, set strict firewall and system region/language. Node failure keeps domestic direct; core failure blocks all public traffic. Explain this before acting.
6. Rule updates: manual, or scheduled every 1–365 days at HH:MM (current user task). Read `.local/ui-settings.json`; do not replace a manual choice with a scheduled task.
7. Verify with `scripts/verify.py`: domestic HTTP, foreign exit region, DNS 1/64/65, fake-IP, IPv6 block, UDP STUN/route, Git/npm, node outage, core outage. Report passed/failed/not tested.
9. Restore: use the GUI snapshot page or `-Action restore`. Restore keeps current subscriptions/node updates and removes the project update task; it restores the managed WebRTC policies without editing browser profiles.
10. Report what changed, evidence, and untested items. Never commit `.local`, `.runtime`, credentials, real profiles or IP logs.

Never install or log in to Claude. HTTP 403 means transport responded, not that Claude is usable. Never claim zero leakage, no bans or universal coverage.

配置及补充配置会关闭自动定位、自动时区和 Windows 自动校时，按所选地区设置国家/地区、区域格式及预设时区。新增状态在改动前保存为恢复点内的 region-time.json；首次基线不覆盖。恢复还原服务启动/运行/延迟启动状态、校时模式和原地区设置，不回拨备份日期。关闭校时后可能产生误差；实际 Windows 行为及联网校时需实测。


配置一致性检查：海外 geolocation-!cn 规则及对应 DNS 策略先于国内白名单；配置前要求 Clash 数据目录已有 geosite.dat 和 geoip.dat，规则库缺失或内核校验失败则停止。

网络环境页显示配置生成版本、进程匹配、IPv6 绑定、国家/区域格式/时区的当前值与目标值。不一致时分别使用重新配置或补充配置。完整配置在内核重启后再次关闭并核对 IPv6 绑定；恢复记录累积在原恢复点内，不替换首次基线。验收记录标为历史结果并显示时间，本地配置一致不等于实际网络通过。

配置/补充管理标准 Chrome、Edge、Firefox 的受管 WebRTC 策略：Firefox 锁定 media.peerconnection.enabled=false；Chrome/Edge 限制非代理 UDP 并校验、准备项目自带离线 WebRTC 扩展（首次手动加载，后续复用；不新增商店下载请求，已有历史条目保持原状）。改动前将策略及扩展条目分别存入 webrtc-policies.json、webrtc-extensions.json 并封存；旧基线只补存新增记录。使用空闲编号，保留其他扩展及后续变化，冲突停止。恢复撤销本项目新增条目，可能触发扩展卸载。不得把策略写入当作 Chrome/Edge 内核完全关闭或扩展已安装/已启用；隐私窗口不自动覆盖。外部指纹浏览器不管理，浏览器实际行为、虚拟网络、Claude 服务和账号未验证。保留旧版 .local/browser 资料。

国内域名统一使用 Loyalsoldier/clash-rules 的 direct.txt，国内 IP、私有域名、局域网地址分别使用 cncidr.txt、private.txt、lancidr.txt；不融合其他直连名单。保留精确域名与 +. 后缀的原始语义。首次切换来源请在规则更新页点击「立即更新」，定时任务会提示等待手动迁移。

更新先下载并严格校验四份来源，保存候选摘要；未发布前不覆盖已应用来源记录。国内域名/IP 的增删总量超过 max(100, 原数量的 10%)、私有/LAN 变化、新增过宽 IP 网段或未经审核的顶级域时停止。首次明确手动切换仅豁免国内域名的数量差异，其余保护仍生效。内容和生成版本均未变化时不重载。

每次发布前在 .local/rule-updates/ 保存更新前文件及校验摘要；发布后通过本机控制通道核对海外优先顺序和规则数量，失败尝试恢复。未完成事务在下次更新前先恢复；发现用户后续改动或备份校验失败则停止，不覆盖用户修改。来源记录和配置版本随成功发布一起更新，界面显示规则数量及增减；恢复失败明确标记待处理。规则更新不连接 Claude/Anthropic，不启动浏览器，不更新 geosite.dat/geoip.dat 或内核。

完整复核与故障演练：在「网络环境」点击对应操作，经 Windows 管理员授权后检查完整防火墙，再分别演练节点故障、关闭 TUN 但保持同一内核运行、内核退出，并在恢复后复测及再次复核防火墙。不重新配置规则、节点或系统地区，不启动浏览器，不访问 Claude/Anthropic。公网会短暂中断。

TUN 演练先确认正常状态可连接通用测试 IP，再启用独立本机恢复看护，只临时切换 TUN 开关；核对关闭状态、网卡状态及同一内核进程，验证新建直连 TCP、UDP/TCP DNS 被阻断，最后恢复 TUN。显式本机代理、浏览器及虚拟网络不在此演练范围内。恢复未完成时阻止后续项目操作并保留 .local/tun-drills/ 记录。

防火墙复核必须验证管理员权限、核心文件可读、实际运行路径、三个有效配置文件、PersistentStore/ActiveStore 中五条项目规则的全部筛选条件，以及其他有效放行规则。权限不足、核心不可读或证据不足不得标记完整复核通过。仅同一操作中包含节点故障、TUN 关闭、停核、恢复后复测、前后两次完整防火墙复核，才显示完整网络验收；旧三阶段报告显示部分阶段。

完整配置的看护准备阶段仍为 180 秒，首次防火墙修改后的写入与扩展验收窗口为 360 秒；超时仍请求回滚，不允许无限延期。独立完整复核不修改防火墙规则，TUN 切换另有 60 秒恢复看护。

软件版本由 assets/app-release.json 统一管理。打包严格遵守 assets/package-files.json，不包含 .local、.runtime、节点、密钥、日志或基线；包输出放 .local/packages/。软件更新只检查指定 GitHub Releases 并提示，不自动执行下载文件或覆盖程序；网络失败或私有来源不得显示已是最新。打包授权不等于提交、推送或发布授权。
