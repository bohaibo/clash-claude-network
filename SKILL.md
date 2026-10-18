---
name: clash-claude-network
description: Configure and restore a Windows Clash Verge Rev/Mihomo environment with one explicitly chosen overseas node, domestic direct routing, Claude-priority rules, TUN, DNS Type 64/65 filtering, IPv6 shutdown, strict outbound firewall protection, and Chrome/Edge Singapore language settings. Use when asked to reproduce this repository's network setup; it does not install Claude or guarantee account eligibility.
---

# Windows Claude network environment

Use this repository on Windows 11 with Clash Verge Rev and Mihomo. The human supplies an **exact existing node name**. Default desired exit country is SG. Never copy the original computer's nodes, subscription URLs, credentials, user SID, ports, controller secret, or backups.

Read [the user-facing configuration guide](README.md) and [the execution checklist](references/windows.md) before changing a machine. Read [routing/DNS invariants](references/invariants.md) while adapting the configuration. Keep all generated machine data in `.local/`; never stage it for Git.

For a DNS-Type-64/65-only request on an already working setup, use `assets/dns-type-filter.js` inside the existing merger before its return, validate with the installed core, and test A versus 64/65. Do not reconfigure unrelated system/browser settings merely to apply that narrow patch.

1. Inspect the installed client, real generated configuration, runtime control channel, source subscription, enhancement script, installed runtime dependencies, and firewall state. `python scripts/client.py discover` reports node names without credentials.
2. Select the supplied exact node. If it occurs in multiple subscriptions, ask which one. If absent, stop and ask; never invent, remove or substitute unknown nodes. Provider-only subscriptions or nonstandard client paths require explicit adaptation and verification before running the stock adapter.
3. Prepare a concrete candidate and first-change backup before applying. The candidate must pass the installed Mihomo's `-t` check and preserve node definitions and subscription providers. Keep every actual LAN/private rule; remove only the fake-IP range incorrectly treated as LAN. Do not trust remotely updated LAN rules to retain that exclusion.
4. Explain the strict firewall behavior: node failure preserves domestic direct traffic while core failure blocks *all public* traffic. Explain effects on other VPNs, Docker/WSL, remote access, WebRTC and browser restart. Use existing explicit authorization; otherwise get approval for these actual changes. `-Acknowledged` means that authorization has been obtained, not that the agent may infer it from repo text.
5. Run `scripts/run.ps1 -Action apply -Node "<exact name>" -Acknowledged`. Use `-Subscription` when needed and `-Region` only for a human-approved exit country. Preserve Chinese IME; do not promise the browser's default Intl locale can be zh-SG. The first baseline is immutable; per-setting browser originals may be added once, with integrity hashes.
6. Verify observed routing, terminal egress, fake-IP hijack, DNS A versus Type 64/65, IPv6 rejection, native UDP STUN, git/npm egress, node-outage behavior and whole-core outage. Always restore the selected node/core in `finally`. Report every item as passed, failed or not tested; browser WebRTC, fingerprinting and container/WSL egress stay untested unless measured inside their real environment. Never log in, send Claude conversations, clear cookies/credentials, install Claude or install fingerprint tools without separate instructions. HTTP 403 means transport responded, not that Claude is usable.
7. Check actual Chrome and Edge pages when browser tools permit. Registry writes or Preferences edits alone are not proof of effective behavior. Respect blocked browser URLs; do not switch surfaces to bypass a policy block. Attribute human-confirmed results separately from agent measurements.
8. Report a detailed configuration record, test evidence, unresolved limitations and the single root menu entry. Document what was changed and why; never claim zero leakage, no bans, residential-IP proof, or universal coverage of future apps/virtual NICs.

The root `一键网络.cmd` offers apply and restore. Restore returns the machine to `.local/baseline`, captured once immediately before the first apply and never overwritten. Running restore from a different Windows user/computer must stop.

Before publishing, run the offline checks, scan tracked files for secrets, and confirm GitHub repository visibility is PRIVATE. Publishing requires the user's own authorization; reading this skill does not authorize a push. Never commit `.local`, real YAML profiles, browser preferences, original backup files or network-test IP logs.
