# Presence operations

Presence monitoring runs on quartz under `presence-monitoring.service`, as root
for network scanning. The recorded working directory is
`/home/nickc/code/electricity_monitoring`; check the installed unit before deployment.
A lane clone is not the service checkout. Deploy reviewed changes through Git on
the owning host, then restart the service when authorized.

## Configuration and metrics

- `presence/people_config.yaml`: people, WiFi MACs, HA entities and polling intervals.
- `/etc/presence-monitoring.env`: service credentials, including `HA_TOKEN`,
  `HA_URL` and Tado credentials. The service does not read the repo's local secrets file.
- `presence/state.json`: cached presence and Tado state; preserve it across updates.
- `presence/mac_learning_state.json`: learned fingerprints and mapping suggestions.

Sources are WiFi, Tado and Home Assistant. Per-person metrics under
`home.presence.<person>` are `from_wifi`, `from_tado`, `from_homeassistant` and
`is_home`. Aggregates are `home.presence.count_home`, `home.presence.anyone_home`
and `home.presence.wifi.devices_present_count`. The metric count depends on the
configured people and sources; the old fixed count of 27 is not a health check.

See [pipelines](PIPELINES.md) for source integration and [MAC learning](MAC_LEARNING.md)
for reviewing suggestions. The October investigation below records source-cache
behaviour, phone changes and entity names; re-check dated values before use.

## Read-only health checks

```bash
systemctl status presence-monitoring
systemctl cat presence-monitoring
journalctl -u presence-monitoring --since '10 minutes ago'
nc -zv 192.168.86.123 2003
```

Inspect source reports and errors in the journal, and metric freshness in Grafana.
Check that `count_home` equals the sum of per-person `is_home`, and that
`anyone_home` is 1 exactly when the count is positive. Missing data and an empty
house are different conditions. Do not send synthetic test data to production.

For a source outage, check its credentials, polling interval and cached last-good
reading. HA uses `http://homeassistant.lan:8123` on quartz. With credentials already
loaded into an authorized shell, check the API without printing the token:

```bash
curl --fail -H "Authorization: Bearer $HA_TOKEN" "${HA_URL:-http://homeassistant.lan:8123}/api/"
```

For startup failures, inspect the unit's Python interpreter and environment,
configuration syntax, file permissions and the journal traceback. Use the
`electricity` conda environment for development checks. Do not replace the
installed service interpreter just to match a development example.

## People and MAC changes

1. Run `python presence_to_graphite.py --discover` from the repository with the
   correct environment and network-scanning privileges. Discovery can persist
   learned state; it is not an offline test.
2. Compare discovery with the person's WiFi enabled and disabled, then review
   the evidence before assigning a MAC. A generic `iphone` hostname is not unique.
3. Edit `presence/people_config.yaml` with `vi`, preserving the existing schema;
   add confirmed addresses to the person's `wifi_macs` and remove obsolete ones.
4. Deploy the configuration through Git to the service checkout. Since 2026-11-02 the
   service reloads `people_config.yaml` within one WiFi scan (30 s) of the file changing
   and logs `Reloaded changed configuration`. An edit that cannot be read, fails to
   parse or has no people is ignored (the previous config stays in use) and emails
   Nick via quartz's postfix, at most once a week (marker
   `/var/tmp/presence-monitoring/last_alert`). Polling intervals and Tado/HA settings still need
   `sudo systemctl restart presence-monitoring`, when authorized.
5. Inspect the journal for the reload line, then validate actual arrivals/departures
   and grace periods.

## Credential maintenance

Home Assistant long-lived tokens are created in the user's HA profile. Edit
`/etc/presence-monitoring.env` with `sudo vi`, updating `HA_TOKEN` and `HA_URL`
while preserving all Tado and other entries. Restart the service and inspect
its journal. Never copy secrets into Git or diagnostic output.

Tado token rotation updates its own `TADO_*` lines in the same file. Preserve
those lines when updating HA credentials; see the incident notes below for the
HA-enabling helper and the service's credential source.

## Routine maintenance and potential alerts

Review journal errors and metric freshness weekly. Revisit MAC mappings after a
phone change, and check API access when a source stops reporting. On configuration
changes, validate count consistency, grace periods and behaviour during source
outages. Optional Grafana alerts can flag missing presence data or inconsistent
counts; these are suggestions, not a claim that alerts are deployed.

## Investigation record: October 2026

### Where it runs and how to debug "logged as away while at home"

Recorded 2026-10-11 after a false-negative investigation; saves re-discovering all of this.

- **Runs on quartz** as root under `/etc/systemd/system/presence-monitoring.service`,
  `WorkingDirectory` = the production checkout (`~/code/electricity_monitoring`), so deployed changes take effect after
  `sudo systemctl restart presence-monitoring` after updating that checkout. It is **not** on a Pi.
- **Secrets come from `/etc/presence-monitoring.env`** (root-only, `EnvironmentFile=-`), not from this
  repo's `secrets_local.env`. The repo file has the working `HA_TOKEN`/`HA_URL`, but the service only sees
  what is in `/etc`. If startup logs `Home Assistant enabled but HA_TOKEN not set`, the HA source is
  silently dead; `sudo systemd/presence_enable_ha_and_restart.sh` copies the two HA values across (keeping
  every other line) and restarts. The Tado token rotation rewrites only its own two `TADO_*` lines in that
  file, so the two coexist. Use `http://homeassistant.lan:8123`; `.local` does not resolve on quartz.
- **Logs**: `journalctl -u presence-monitoring` works for `nickc` without sudo (plain `journalctl` grep on
  `python[<pid>]` also works). `systemctl status` and `pgrep` may fail inside an agent sandbox; `journalctl`
  still shows the live pid. One `Presence sources: nick: is_home=.. (wifi=, tado=, ha=)` line every 5 s.
- **Reading those lines**: `is_home = max(wifi, tado, ha)`. Tado is polled every 300 s, HA every 60 s;
  since commit d438ed5 the last good poll is reused between polls for 2 x its interval. Before that fix a
  source counted for one 5-second cycle per poll, which looked like `tado=1` blips 3-5 s long every few
  minutes; if you see that pattern again, the caching has regressed. A gap with no Tado blip at all and no
  Tado errors means the geofence genuinely saw nobody (confirmed correct on 2026-10-10 11:30-13:30 BST).
- **Per-source state** is in `presence/state.json`: `last_seen_person_wifi` (per person) and
  `last_seen_wifi` (per MAC, epoch seconds). A person whose configured MAC has not been seen for days has
  a new or re-randomised handset: iPhones use a private per-network MAC, so a new phone always needs a new
  `wifi_macs` entry in `presence/people_config.yaml` (run `--discover`). The `iphone` hostname hint is
  deliberately dropped because every person shares it, so there is no hostname fallback.
- **HA entities** (checked 2026-10-10): `device_tracker.nick_s_iphone`, `device_tracker.susans_iphone`,
  `device_tracker.charlies_iphone_3` (the live one; `_2` and the unsuffixed one are stale),
  `device_tracker.archies_iphone`, `device_tracker.toms_iphone`; `person.nick_crabtree` reads `unknown`
  and is not used. Query with `GET $HA_URL/api/states` and the repo token to confirm current state.
- **WiFi MACs updated 2026-10-12**: nick is `8A:DD:F9:79:CD:3D` (new handset; `BE:80:47:8F:9F:78` last seen
  2026-07-02) and susan is `8A:FD:F9:12:08:0D` (Nick's old handset, reset, so a fresh private MAC; identified by
  its departure from the network at 07:42 on 2026-10-12 and `iphone.lan` reverse DNS). Susan's previous phone
  `9A:C1:BC:4B:7F:47` is intentionally unlisted because it may still sit idle on the network. The HA
  `device_tracker` entities for nick and susan were not re-checked after the handset swap and may be stale.
- **WiFi presence invalid 2026-10-09 to 2026-10-28 18:24 GMT** (found 2026-11-02 from a home_assistant
  handover): the MACs above were committed but the service, which then read its config only at startup, was not
  restarted until quartz rebooted on 2026-10-28. `home.presence.{nick,susan}.from_wifi` is 0 throughout that
  window although the scan saw 50-56 devices a day; exclude it from any analysis or training. From 2026-10-29,
  while Tado says home, WiFi also says home 96-100 % of each day for both, never while Tado says away; gaps
  while home have median 2 min and p90 7-9 min, so the 300 s grace and the iPhones' private MACs are fine.
  The config reload above prevents a repeat.
- Suspend-to-RAM on quartz (it happened 2026-10-10 17:49-19:27) stops the service with no log line at all;
  the gap plus a `Network is unreachable` Tado error on wake is the signature.
