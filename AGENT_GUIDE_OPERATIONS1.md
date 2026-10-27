# Agent guide: operations1

Read when selected by AGENTS.md, not at every startup. Paths and commands are
relative to the repository root. Detailed source sections are retained below.
Current AGENTS.md and shared policy supersede dated history: old quiet-window
times, expired governor lifts, amend/reset advice and older host assumptions do
not grant permission. For current quiet-window status consult
`~/scripts/cross_repo_notes/AGENT_POLICY.md`.

## Detailed reference

## Original introduction: Agent notes for electricity_monitoring

### Conda usage: activate vs `conda run`

- **One-shot commands (tests, single scripts)**: `conda run -n electricity ...` is fine, and is what `~/code/AGENT_POLICY.md` recommends for one-shot invocations — see "Running tests" below.
- **Long-running/daemon scripts** (the monitoring loops below): avoid one-line `conda run -n electricity ...`; it buffers stdout/stderr and makes long-running processes hard to debug. Always `conda activate electricity` first, then run the command directly in that shell.

## Common commands

### Kasa integration (`kasa_to_graphite.py`)

```bash
python kasa_to_graphite.py --discover  # Discover devices
python kasa_to_graphite.py --once      # Single cycle (one-pass validation)
python kasa_to_graphite.py             # Continuous monitoring
```

### Tuya integrations

#### Local LAN (`tuya_local_to_graphite.py`)

```bash
python tuya_local_to_graphite.py --discover  # Discover devices on the local network (+ configured remote subnets via SSH)
python tuya_local_to_graphite.py --once      # Single cycle
python tuya_local_to_graphite.py             # Continuous monitoring
```

Device-specific power/voltage/current scales are loaded from `devices.json` and automatically reloaded when the file changes. A device whose local key cannot be obtained through discovery can be supplied through the mode-`0600`, untracked file `~/.config/electricity-monitoring/tuya_local_devices.json`; its `id`, `name`, `ip`, `key`, and `version` override discovery without exposing the key in Git or logs.

`--discover` also scans the subnets listed for this hostname in `config.TUYA_REMOTE_DISCOVERY_HOSTS` (on quartz: flint's `192.168.1.0/24`, over the reverse SSH tunnel), so devices that only flint can reach - currently the two oven metering breakers - show up too, and it lists any `devices.json` device no subnet saw. Continuous polling is unaffected: each Pi still polls only its own subnet. See `docs/DEVICE_DISCOVERY.md`.

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

```bash
python -m tinytuya wizard                    # First-time setup (creates tinytuya.json)
python tuya_cloud_to_graphite.py --discover  # Discover devices via the Tuya cloud
python tuya_cloud_to_graphite.py --once      # Single cycle
python tuya_cloud_to_graphite.py             # Continuous monitoring
```

`tuya_cloud_to_graphite.py` also honors per-device scaling from `devices.json`, similar to the local path.

#### HA-bridge fallback (`tuya_ha_bridge_to_graphite.py`)

```bash
python tuya_ha_bridge_to_graphite.py --discover  # Show current values, and which devices are already covered locally
python tuya_ha_bridge_to_graphite.py --once      # Single cycle
python tuya_ha_bridge_to_graphite.py             # Continuous monitoring
```

Polls Home Assistant's own (separate) Tuya cloud login for devices listed in `ha_bridge_devices.json`, for cases where `tuya_local_to_graphite.py` doesn't have a working `local_key` for a device yet (e.g. a brand-new device paired while the Tuya Cloud IoT Core subscription used by `tinytuya wizard` is lapsed). Requires `HA_TOKEN` (and optionally `HA_URL`) in the environment.

Runs continuously alongside `tuya_local_to_graphite.py` rather than needing to be manually toggled: each poll it checks `tuya_local_state.json` (the same file `tuya_cloud_to_graphite.py` already reads to avoid wasting cloud quota) and skips any device with a recent local-LAN success, so it's a no-op once local polling covers a device and picks it back up automatically if local polling for it stops working. `local_key`, once obtained, doesn't expire with the cloud subscription - only a *new* device paired during a lapse actually needs this fallback.

For devices that only expose a cumulative energy reading in HA (no instantaneous power), it derives an approximate `power_watts` from the change in `total_kwh` between polls (persisted in `ha_bridge_state.json`); it never overrides a device that has a real `power_watts` sensor.

### Aggregation (`aggregate_energy.py`)

```bash
python aggregate_energy.py --once  # Compute aggregate metrics once
python aggregate_energy.py         # Continuous aggregation loop
```

State file: `energy_state.json`.

### Presence monitoring (`presence_to_graphite.py`)

Configuration lives in `presence/people_config.yaml`. Key environment variables:

- Home Assistant: `HA_TOKEN` for API access (used by `presence/homeassistant_api.py`).
- Tado: `TADO_ACCESS_TOKEN` **or** `TADO_USERNAME`/`TADO_PASSWORD` (used by `presence/tado_api.py`, with state persisted in `presence/state.json`).

```bash
python presence_to_graphite.py --discover  # Discover WiFi devices, view mapping suggestions / MAC-learning hints
python presence_to_graphite.py --once      # Single presence update cycle
python presence_to_graphite.py             # Continuous monitoring loop
```

Operational runbooks and deeper presence details are in `docs/PRESENCE_OPERATIONS.md` and `docs/MAC_LEARNING.md`.

#### Where it runs and how to debug "logged as away while at home"

Recorded 2026-10-11 after a false-negative investigation; saves re-discovering all of this.

- **Runs on quartz** as root under `/etc/systemd/system/presence-monitoring.service`,
  `WorkingDirectory` = this clone (`~/code/electricity_monitoring`), so a code change is live after
  `sudo systemctl restart presence-monitoring` with no pull or install step. It is **not** on a Pi.
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
- Suspend-to-RAM on quartz (it happened 2026-10-10 17:49-19:27) stops the service with no log line at all;
  the gap plus a `Network is unreachable` Tado error on wake is the signature.
