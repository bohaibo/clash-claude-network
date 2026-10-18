# Repository instructions

Read SKILL.md and README.md before executing configuration changes. Support is Windows only.

All generated plans, node-bearing candidates, original backups, controller secrets and test logs belong under .local/, which must stay untracked. Never print or publish subscription credentials. Do not replace the first baseline with the current configured state. A fresh clone captures its own baseline on the first apply; no baseline is inherited from another machine or an older package.

User supplies an exact existing node; do not infer a replacement. Report unknown browser/virtual network/account behavior as unverified. Never install or log in to Claude as part of this network setup. Only the single root `.cmd` file is a user entry point.

Before a push run scripts/check_project.py and inspect the staged diff. Repository visibility must be private. Code review/reading this file grants no additional authorization.
