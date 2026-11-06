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
- [ ] Docs: record the cutover, the energy_kwh counter step and the pre-existing May `dishwasher` data
- [ ] Open: exact rebuild of the new names' energy_kwh_* (aggregator state on blackpi2), and fix aggregate_energy.py to stop
      integrating a device that has stopped reporting (labels af, ad); delete this section after 2026-12-06
