# Reminders for Nick

Open items that need Nick in person. Any agent (Claude or not) should read and update this file; it is the durable
record, not any agent's private memory. `tools/remind_nick.py` emails the open (`- [ ]`) items every Sunday morning
from quartz's crontab (Nick asked on 2026-11-01 for **at most one reminder a week**), so agents should not repeat
these items in every report. Tick an item (`- [x]`) when Nick says it is done; nothing is sent when none is open.

Cron line (Sundays 09:13): `/usr/bin/python3 .../tools/remind_nick.py >> /ncloud/Nick/electricity_monitoring/remind_nick.log 2>&1`.
The log is deliberately outside `/ncloud/Nick/tmp`, which is wiped after 30 days: if the log's directory vanished,
the `>>` redirect would fail and the job would silently never run.

## Open

- [ ] (e) Buy and fit metering plugs for the dishwasher and washing machine (no Home Assistant entity exists for
  either). Nick's to do ("leave it to me", 2026-11-01): no shopping spec from agents.

## Done

- [x] (d) Re-pair the tumble dryer's TP-Link KP115 plug: withdrawn 2026-11-06. Nick moved the Tuya plug formerly
  "Garage power A" (about 4 W average, switched twice in 180 days) to the dryer instead.
