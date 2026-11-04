# Current plan

Rolling plan for agents (see `~/scripts/cross_repo_notes/topics/workflow.md`). Nick's own open items stay in
`docs/REMINDERS.md`, which `tools/remind_nick.py` emails weekly, so they are not moved here.

## Towel rail cloud check (Nick, 2026-11-04)

Smart Life / HA lost the towel rail from 2026-11-02 16:23 UTC until Nick toggled it in the app on 2026-11-04,
while blackpi2's direct LAN poll kept working. Nick: "configure a daily warning email if this happens again - just
for the towel rail".

- [x] Tests (20) first, then `tools/towel_rail_cloud_check.py`; full suite 238 passed; pre-commit clean
- [x] Live dry run `ok`; replay of the outage (Graphite as at 2026-11-03 05:40, 12:00, 20:00 against HA frozen at
      2026-11-02 16:23) classifies `fault` each time; test email sent
- [x] Quartz crontab at :07/:22/:37/:52 (backup `~/tmp/crontab_backups/quartz_crontab_2026-11-04_towel.txt`)
- [x] `docs/OPERATIONS.md` Watchdogs paragraph

Result (2026-11-04): live. Delete this section after 2026-12-04; the durable record is in docs/OPERATIONS.md.
