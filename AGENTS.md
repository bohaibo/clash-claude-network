# Repository instructions

Read SKILL.md and README.md before executing configuration changes. Support is Windows only.

禁止连接 Claude 进行测试。所有开发、回归和验收测试不得访问真实 Claude/Anthropic 服务，不得打开 claude.ai、Claude 网页、桌面程序或登录页面，不得发起相关 HTTP/API、WebSocket、DNS 或其他联网探测。涉及这些域名、路由和启动参数的测试只能使用离线字符串、固定样例及 mock；真实服务、账号及应用可用性一律报告“未验证”。不得为了测试启动浏览器。用户另行授权的正常使用不属于测试。

All generated plans, node-bearing candidates, original backups, controller secrets and test logs belong under .local/, which must stay untracked. Never print or publish subscription credentials. Do not replace the first baseline with the current configured state. A fresh clone captures its own baseline on the first apply; no baseline is inherited from another machine or an older package.

User supplies an exact existing node; do not infer a replacement. Report unknown browser/virtual network/account behavior as unverified. Never install or log in to Claude as part of this network setup. Only the single root `.cmd` file is a user entry point; it opens the GUI, with `--menu` as the console fallback. Only the managed WebRTC policies may affect Chrome/Edge/Firefox globally; back them up and restore them. Never edit default browser profiles or language fields; other browser features use `.local/`. Install Python/Node/PyYAML only under `.runtime/`; do not modify system PATH, registry environments, or system Python/Node. The loopback GUI server must remain bound to `127.0.0.1` and must never be exposed on a LAN interface.

Before a push run scripts/check_project.py and inspect the staged diff. Repository visibility may be public or private at the user's choice. Never publish machine-local data, backups, logs, subscription credentials or controller secrets. Code review/reading this file grants no additional authorization.
