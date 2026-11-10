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

## Cut over the moved plugs to their new names (Nick, 2026-11-06)

Two Tuya plugs changed job today: id `bfb6f68a52a77dcbb2niyu` (was "Garage power A", now the tumble dryer) and id
`bf19c62a3629c3fc15br3k` (was "Boot room freezer", now the dishwasher). Nick: "properly cut over to the new names",
retrospectively for the points recorded since each plug went on its appliance, in Home Assistant and Graphite alike,
and every HA entity, automation and other reference renamed. Cutover instants (first point after the offline gap
while Nick moved the plug): dishwasher 2026-11-06 12:18:40 GMT, dryer 12:08:15 GMT.

- [x] Names in `device_names.json` / `devices.json` / docs; blackpi2 pulled and poller restarted (0cd9346)
- [x] Gating checks: ubuntu64 reachable by ssh as nickc; whisper root `/var/lib/graphite/whisper/home/electricity/tuya`,
      owned by `_graphite` (pruning needs root, so Nick runs that step); carbon-cache running; `python3 whisper` present
- [x] Inventory: no repo code, grafana JSON or blackpi2 `ha_bridge_devices.json` names either plug; only docs
- [x] Governed Opus agent: `tools/graphite_move_series.py` plus tests (TDD): copy points >= T from `A.*` to `B.*`,
      back up `.wsp` files, rebuild `A.*` without points >= T (same schema), verify
- [x] Lead: read-only run of the copy, review, commit via Nick's prompt
- [x] Prune run 2026-11-06 ~18:35 GMT on Nick's explicit go (passwordless sudo on ubuntu64); verified: old names end at 12:13:40 / 12:01:00, new names start at the cutover
- [x] Handover to the HA session: rename all ten entity ids per plug, friendly names without trailing space, sentinel
      automation, ghost KP115 entities, recorder history, any HA-side Grafana/blackpi2 bridge references
- [x] Docs: record the cutover, the energy_kwh counter step and the pre-existing May `dishwasher` data (docs/OPERATIONS.md)
- [x] ad: `aggregate_energy.py` ignores a device with no point for `GRAPHITE_STALE_AFTER_SECONDS` (300 s), with tests
- [x] ae/af: `tools/repair_energy_after_rename.py` (plan/state/series/verify/undo) plus tests; live dry run 2026-11-06 20:44:
      excess 1.619724 kWh (boot_room_freezer 1648.9 W, 332 cycles 12:47:35-13:46:20), dishwasher 2.7166 kWh and tumble
      dryer 1.3305 kWh since cutover; docs/OPERATIONS.md paragraph
- [x] ai (Nick 2026-11-09 "go"): committed 05316e3, pulled on blackpi2 and restarted `graphite-aggregate-energy` at 21:51:53 GMT
      2026-11-09; counters and `aggregate.power_watts` are being written. The 2026-11-06 deadlines (before 00:00 and before
      about 12:00 GMT 2026-11-07) were missed: the 5 s data for the cutovers is gone and the day and week counters have reset
- [x] aj: the repair tool reworked for 60 s (the 5 s data aged out on 2026-11-07): 5 s snapshot of 2026-11-06 20:44
      (durable copy beside the backups, checksummed) up to 20:44, live 60 s archive to 24 h ago, live 5 s after; probe
      series `test.electricity_monitoring.energy_repair_probe.2026_11_09_2215` left in Graphite; dry run 2026-11-09
      22:11: aggregate month 139.2572 -> 137.6375, year 3549.2666 -> 3547.6469; dishwasher month/year 15.6374 kWh,
      tumble dryer 10.1659 kWh since cutover
- [x] Lead (Nick "go" 2026-11-09): applied 2026-11-09 22:27-22:41 UTC. Aggregator stopped 22:27:13, restarted 22:28:26 on
      the corrected `energy_state.json` (month 139.4047 -> 137.7850, year 3549.4141 -> 3547.7944; dishwasher 15.6374 kWh,
      tumble dryer 10.2054 kWh since cutover; three stale entries removed), 93,185 Graphite values rewritten, one `redo` for
      the 22:34 boundary minute on 2026-11-08, `verify --log` clean; logs beside the backups in
      /ncloud/Nick/electricity_monitoring/backups/2026-11-09/energy_repair/. Delete this section after 2026-12-06
- [x] Gym lights (Nick 2026-11-09): `tools/estimate_shed_loads.py` plus tests; result and caveats in docs/CALIBRATION.md.
      Nick (2026-11-10) will not log the switch times; a 14-day history scan found no separable session (see
      docs/CALIBRATION.md). The first estimate (about 70 W, 45-95 W) stands unless a nightly full-resolution finder is approved.
- [x] ak (Nick 2026-11-10 "AK, yes"): aggregator reads Graphite with `noCache=1`; b4f881c, deployed to blackpi2 07:17 UTC.
      al: Nick says leave the other dead state entries alone (they may come back).
