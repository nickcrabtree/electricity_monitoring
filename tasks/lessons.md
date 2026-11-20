# Lessons

Patterns from Nick's corrections, each with the rule that prevents a repeat (see
`~/scripts/cross_repo_notes/topics/workflow.md`).

- **Errors nobody reads are no use** (2026-11-02, presence config reload). A serious error in an unattended service
  must email Nick, not only log: via quartz's postfix, throttled (weekly, or daily where he asks), with the fix in the
  email.
- **Reminders at most weekly** (2026-11-01). Nick's open items go in `docs/REMINDERS.md` for the weekly email; do not
  repeat them in every report.
- **Installers must not re-enable what was deliberately disabled** (2026-11-01, `systemd/install.sh`). Refresh unit
  files, but enable only units new to the host.
- **Look up host access before calling a host unreachable** (2026-11-13, OpenWrt router). I tried the router only from
  flint and told Nick it needed a key, though quartz reaches it with `ssh openwrt`. Before saying a host cannot be
  reached, check quartz's `~/.ssh/config` and `~/scripts/cross_repo_notes/topics/host_access.md`; "no routing to the
  IoT subnet" does not cover the router's own main-LAN side.
- **Check the running process uses the current config** (2026-11-02). A config committed while a long-running service
  only reads it at startup does nothing until a restart: make services reload, or restart and verify.
- **Nick's explicit go does unblock a refused push** (2026-11-15, dashboard commit 65096ec). The auto-mode classifier
  refused a push, and I told Nick that saying "push" would not help. It did: after his "Commit and push ... I approve
  this", the same push outside the sandbox was allowed. When a classifier refuses an action Nick wants, ask for his
  explicit approval in plain words and retry once it comes; do not tell him approval cannot help.
- **Add a new plug without asking, and test it through the tunnel** (Nick, 2026-11-20, washing machine plug). Use the
  name Nick gave it in the Tuya/Smart Life app: if it is a meaningful name he chose, take it as his answer and do not
  ask him to confirm; hold only for a stock name (such as "Smart Socket" or "Antela smrat plug 9"). Do the whole
  onboarding unprompted next time: `tinytuya wizard` into a scratch directory, merge, name, key check, commit, deploy
  after his push. Quartz reaches flint's 192.168.1.0/24 over the reverse tunnel (`config.TUYA_REMOTE_DISCOVERY_HOSTS`),
  so check a flint-subnet plug's key at once with `OutletDevice(...).status()` run on flint, key on stdin; I wrote that
  I could not, and Nick called it lazy.
