---
name: clash-claude-network
description: Configure/restore a Windows Clash Verge Rev/Mihomo network with one exact overseas node, Claude-priority routing, TUN, DNS Type 64/65 filtering, IPv6 off, strict firewall, an isolated browser profile, and a local GUI with manual or interval-based rule updates. Does not install Clash or Claude, modify the default browser, or guarantee account eligibility.
---

# Windows Claude network environment

Windows 11 only. Read [README.md](README.md), [INSTALL.md](INSTALL.md), and [references/windows.md](references/windows.md) before changing a machine.

- The human supplies an **exact existing node name**. Never invent, replace or guess nodes. Default exit country is SG.
- The root `一键网络.cmd` opens the GUI; `--menu` is the console fallback.
- Python 3.13, PyYAML and Node.js live in project `.runtime`; never install them system-wide or modify PATH. Run `scripts/bootstrap.ps1` if `.runtime` is missing.
- Never write default Chrome/Edge policy, preferences or language fields, and never close the default browser. Claude uses `.local/browser/profile`; the GUI uses `.local/gui/browser-profile`.
- The GUI is loopback-only and non-admin; admin actions are delegated to `scripts/run.ps1` with UAC. Keep it bound to `127.0.0.1`.

Workflow:

1. `python scripts/client.py discover` shows subscriptions/node names without credentials.
2. Select the exact node. Region is auto-hinted from node/subscription names; actual exit is checked after configuration. Apply also switches the Windows timezone and sandbox browser language to the selected region so Claude desktop and the sandbox browser agree. Restart Claude desktop/Windows as needed; reopen the sandbox for language changes.
3. First apply creates the original snapshot automatically. Later applies reuse the pinned first recovery point; manual snapshots are additive. Never overwrite the baseline with the current state.
4. Apply: `scripts/run.ps1 -Action apply -Node "<exact>" -Acknowledged` (or the GUI). Use `-Subscription` for duplicate names and `-Region` when the hint is wrong.
5. Apply/supplement block all non-project outbound allows, close IPv6 bindings, set strict firewall and system region/language. Node failure keeps domestic direct; core failure blocks all public traffic. Explain this before acting.
6. Rule updates: manual, or scheduled every 1–365 days at HH:MM (current user task). Read `.local/ui-settings.json`; do not replace a manual choice with a scheduled task.
7. Verify with `scripts/verify.py`: domestic HTTP, foreign exit region, DNS 1/64/65, fake-IP, IPv6 block, UDP STUN/route, Git/npm, node outage, core outage. Report passed/failed/not tested.
8. Browser checks use the sandbox check page only. It compares STUN srflx public addresses with the public exit: a different IP is a WebRTC leak; host-only means local/mDNS; no candidates is inconclusive, not proof. Never claim DNS/fingerprint/account proof from it.
9. Restore: use the GUI snapshot page or `-Action restore`. Restore keeps current subscriptions/node updates and removes the project update task; it does not modify default browsers.
10. Report what changed, evidence, and untested items. Never commit `.local`, `.runtime`, credentials, real profiles or IP logs.

Never install or log in to Claude. HTTP 403 means transport responded, not that Claude is usable. Never claim zero leakage, no bans or universal coverage.

配置及补充配置会关闭自动定位、自动时区和 Windows 自动校时，按所选地区设置国家/地区、区域格式及预设时区。新增状态在改动前保存为恢复点内的 region-time.json；首次基线不覆盖。恢复还原服务启动/运行/延迟启动状态、校时模式和原地区设置，不回拨备份日期。关闭校时后可能产生误差；实际 Windows 行为及联网校时需实测。
