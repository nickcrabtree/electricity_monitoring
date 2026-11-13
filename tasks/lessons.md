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
