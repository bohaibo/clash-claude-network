# Windows execution checklist

1. Detect Windows version, client version/path, selected source profile and enhancement hook, running core executable and uniquely accessible control pipe. Python 3.13, PyYAML and Node must be project-local under `.runtime`; do not use or modify system Python/Node. The stock adapter requires the Clash Verge service and Meta TUN adapter; missing/nonstandard paths require agent adaptation before applying.
2. Discover exact node with client.py discover. Pass --subscription only when necessary. Never print server credentials or subscription URLs. Ensure exit requirements and authorization for strict firewall/network interruption are known. Browsers are managed externally; never configure their profiles or check default-browser associations.
4. The entry snapshots system state, seals backup hashes, then publishes persistent/client runtime changes. Full source node definitions and subscription providers are preserved. The renderer composes the prior enhancement script before the final secure override.
5. Apply system settings with a bounded firewall rollback watchdog: allow up to 180 seconds for preparation, then arm a fresh 360-second window for the expanded drills immediately before the first firewall mutation. A complete pass must set its commit marker; a failure attempts to restore original firewall settings while leaving a visible incomplete result for the full restore entry.
6. Verify standard traffic, qtypes 1/64/65, IPv6, node failure and core failure. Confirm routing from runtime connection metadata as well as HTTP statuses. Stock verify.py checks core selected group/config and connectivity; the agent must additionally observe the actual per-connection route when no sample was captured. HTTP 403 remains unresolved service usability, not a pass.

Automatic verification does not send HTTP requests to Claude/Anthropic. Use other foreign targets for outage testing and mark Claude transport/account usability untested. Git/npm attempts are bounded to 20 seconds with at most two attempts, use file-backed logs under `.local/tool-probes/`, and terminate the created Windows process tree on timeout. No credential prompts may open. Failed tested tools still fail the deployment.

Require four project outbound allows and one all-scope SharedAccess service block in both PersistentStore and ActiveStore. Validate effective policy immediately after applying and after network tests. A dynamic ICS allow may be treated as covered only when its actual system svchost path, SharedAccess service, NotRequired authentication and false OverrideBlockRules have been checked. Unknown dynamic/group-policy allows must fail even during supplement preflight. Keep virtualization and SharedAccess services running; document that the service block can affect connection sharing and virtual workloads, whose actual traffic remains unverified.
9. Keep exactly one public entry at project root (`一键网络.cmd`, which opens the GUI; `--menu` is the console fallback). Agent helper scripts are internal dependencies. A fresh clone captures its own baseline on the first apply; there is no shared or inherited baseline.

Rollback: use the GUI restore page or the console fallback. Never import another computer's .local state. If no privileged service exists or the control channel is ambiguous, stop before firewall mutation rather than fabricating a path. Restoring firewall defaults can change Internet availability; retain a local console and explain that before a remote-only deployment.

All-adapter IPv6 handling includes hidden, virtual and disconnected adapters. Record enabled bindings before mutation, recheck all adapters afterward, and restore baseline states plus journaled later adapters. Match exact GUIDs; never substitute a same-name replacement. Use binding instances for mutation. Normal restoration preserves originally disabled states and does not repair driver startup. Binding failures must produce an incomplete result.

Before closing applications, verify the firewall prerequisites or selected restore snapshot. Browser features are removed. Never launch a browser for testing. Only the managed, backed-up WebRTC policies may affect standard browsers globally; do not edit browser profiles. Restore only proxy fields in Git/npm/Docker files, preserving later unrelated settings. A pending watchdog rollback is pinned to its recorded snapshot and serialized with firewall writes; resolve it before a new operation. The bounded firewall timeout requests rollback after the current write step. Scheduled updates must skip unresolved guards or changed script associations; honor the GUI interval (1–365 days) and time.

A configured manual snapshot retains its pre-existing project scripts and their current rule updates. Remove only later project additions, preserving current subscriptions and user script choices. Missing required scripts or the exact selected node must stop preflight; do not revive old node-bearing runtime YAML. Validate regenerated configured runtime against current source nodes and routing invariants. Record verification stages under one operation ID, retaining node-outage/core-exit/recovery evidence separately. Failed tested STUN/git/npm checks fail verification; unavailable tools remain explicitly untested.

配置及补充配置会关闭自动定位、自动时区和 Windows 自动校时，按所选地区设置国家/地区、区域格式及预设时区。新增状态在改动前保存为恢复点内的 region-time.json；首次基线不覆盖。恢复还原服务启动/运行/延迟启动状态、校时模式和原地区设置，不回拨备份日期。关闭校时后可能产生误差；实际 Windows 行为及联网校时需实测。


配置一致性检查：海外 geolocation-!cn 规则及对应 DNS 策略先于国内白名单；配置前要求 Clash 数据目录已有 geosite.dat 和 geoip.dat，规则库缺失或内核校验失败则停止。

网络环境页显示配置生成版本、进程匹配、IPv6 绑定、国家/区域格式/时区的当前值与目标值。不一致时分别使用重新配置或补充配置。完整配置在内核重启后再次关闭并核对 IPv6 绑定；恢复记录累积在原恢复点内，不替换首次基线。验收记录标为历史结果并显示时间，本地配置一致不等于实际网络通过。

配置/补充仅管理标准 Chrome、Edge、Firefox 的 WebRTC 策略并备份恢复。Firefox 直接锁定禁用；Chrome/Edge 限制非代理 UDP 并自动设置各自官方商店的 WebRTC Control 安装策略（已有扩展复用；项目离线包保留备用；离线版恢复点新增独立自动安装恢复记录，不覆盖原记录），不能标为内核完全禁用。扩展实际下载、开关、权限和阻断未验证，隐私窗口不自动覆盖。新增恢复记录在首次改动前封存，保留原基线和其他扩展条目；外部指纹浏览器不管理。浏览器实际行为、虚拟网络、Claude 服务和账号未验证。

完整复核与故障演练：在「网络环境」点击对应操作，经 Windows 管理员授权后检查完整防火墙，再分别演练节点故障、关闭 TUN 但保持同一内核运行、内核退出，并在恢复后复测及再次复核防火墙。不重新配置规则、节点或系统地区，不启动浏览器，不访问 Claude/Anthropic。公网会短暂中断。

TUN 演练先确认正常状态可连接通用测试 IP，再启用独立本机恢复看护，只临时切换 TUN 开关；核对关闭状态、网卡状态及同一内核进程，验证新建直连 TCP、UDP/TCP DNS 被阻断，最后恢复 TUN。显式本机代理、浏览器及虚拟网络不在此演练范围内。恢复未完成时阻止后续项目操作并保留 .local/tun-drills/ 记录。

防火墙复核必须验证管理员权限、核心文件可读、实际运行路径、三个有效配置文件、PersistentStore/ActiveStore 中五条项目规则的全部筛选条件，以及其他有效放行规则。权限不足、核心不可读或证据不足不得标记完整复核通过。仅同一操作中包含节点故障、TUN 关闭、停核、恢复后复测、前后两次完整防火墙复核，才显示完整网络验收；旧三阶段报告显示部分阶段。
