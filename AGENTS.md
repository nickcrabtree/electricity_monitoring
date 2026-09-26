# Agent notes for electricity_monitoring

## Environment setup

**Conda (preferred on dev machines)**

```bash
conda create -n electricity python=3.11 -y
conda activate electricity
pip install -r requirements.txt
```

**On Raspberry Pi without conda**

```bash
pip3 install --user -r requirements.txt
```

### Conda usage: activate vs `conda run`

- **One-shot commands (tests, single scripts)**: `conda run -n electricity ...` is fine, and is what `~/code/AGENT_POLICY.md` recommends for one-shot invocations — see "Running tests" below.
- **Long-running/daemon scripts** (the monitoring loops below): avoid one-line `conda run -n electricity ...`; it buffers stdout/stderr and makes long-running processes hard to debug. Always `conda activate electricity` first, then run the command directly in that shell.

## Running tests

Install development tools into the project's conda environment, then install
this clone's Git hook (hooks themselves are not tracked by Git):

```bash
conda run -n electricity python -m pip install -r requirements-dev.txt
conda run -n electricity python -m pre_commit install
```

The tracked `.pre-commit-config.yaml` checks staged files for syntax, merge
conflicts, file hygiene, private keys, Ruff lint, and Ruff formatting. Tool
versions are pinned; keep the Ruff version in `requirements-dev.txt` aligned
with the hook revision. Ruff rules and formatting live in `pyproject.toml`.

Before committing:

```bash
conda run -n electricity python -m pytest tests/ -v
# Stage only the intended files, then:
conda run -n electricity python -m pre_commit run
```

If hygiene hooks modify files, review and re-stage them, then repeat the gate.
For Python formatting, run `conda run -n electricity python -m ruff format`
with explicit touched-file paths. Never run formatters or hooks with
`--all-files`; fix every lint error in each touched file. Do not bypass hooks
or allow a missing configuration. Commit approval follows the shared policy.

All Python on development machines uses conda environments. Development tools
are separate from production dependencies and are not needed just to run the
collectors on a Pi. Tests use temporary state and mocks; a live `--once` run
writes metrics and runtime state and is not a read-only smoke test.

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

### Graphite/Carbon connectivity checks

Graphite/Carbon is typically at `192.168.86.123:2003`.

```bash
nc -zv 192.168.86.123 2003
echo "test.metric 1 $(date +%s)" | nc 192.168.86.123 2003
```

### SSH tips

**Backgrounding processes via SSH:** a simple `nohup cmd &` will hang because the parent SSH session waits for the child. Wrap the command in a bash subshell:

```bash
# This hangs:
ssh host 'nohup python script.py &'

# This works:
ssh host 'bash -c "nohup python script.py >> log.txt 2>&1 &"'
```

## Big-picture architecture overview

### Core configuration (`config.py`)

- Defines the **Graphite/Carbon target** (`CARBON_SERVER`, `CARBON_PORT`), **poll intervals** (e.g. `SMART_PLUG_POLL_INTERVAL`), and the **metric prefix** (`METRIC_PREFIX`, typically `home.electricity`).
- Settings for network scanning (`KASA_DISCOVERY_NETWORKS`), rediscovery cadence (`KASA_REDISCOVERY_INTERVAL`, `TUYA_REDISCOVERY_INTERVAL`), and Graphite whisper access via SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH`, etc.) used by the aggregation script.
- All higher-level scripts import `config.py` rather than hard-coding these values.

### Metric emission helpers (`graphite_helper.py`)

- `send_metric` and `send_metrics` encapsulate TCP writes to the Carbon server, handling timeouts, batching, and logging.
- `format_device_name` normalizes human-friendly device names to metric-safe IDs: lowercases, replaces spaces/dashes with underscores, strips special chars, collapses multiple underscores.
- All scripts build metric paths by combining `config.METRIC_PREFIX`, a **source** (e.g. `kasa`, `tuya`, `aggregate`), the formatted device name (if applicable), and a metric suffix — ensuring consistent naming across Kasa, Tuya, aggregation, and presence-related metrics.

### Device naming and identity (`device_names.py`)

- Persists a mapping from **stable IDs** to **friendly names** in `device_names.json`: Kasa uses MAC addresses, Tuya uses permanent device IDs.
- On first discovery, scripts call `get_device_name(id, fallback_alias)`; if unknown, they store the device's reported alias and reuse it on subsequent runs. This makes metric paths stable even when IP addresses change.
- See `docs/DEVICE_DISCOVERY.md` for details on automatic discovery.

Metric paths follow `home.electricity.kasa.<friendly_name>.<metric>` / `home.electricity.tuya.<friendly_name>.<metric>`, with `<friendly_name>` produced by `format_device_name`.

### Kasa pipeline (`kasa_to_graphite.py`)

- **Discovery**: Kasa's UDP broadcast discovery on the local subnet.
- **Polling and metrics**: `get_device_metrics` refreshes device state with retries/exponential backoff and emits `power_watts`, `voltage_volts`, `current_amps`, `is_on` under `home.electricity.kasa.<device>.*`. `poll_devices_once` gathers metrics concurrently via `asyncio`, then batch-sends via `send_metrics`.
- **Main loop**: `main_loop` maintains a view of active devices and triggers rediscovery after several failed polls or after `KASA_REDISCOVERY_INTERVAL` seconds.

### Tuya pipelines (local and cloud)

#### Local LAN (`tuya_local_to_graphite.py`)

- **Discovery**: `tinytuya.deviceScan()` on the local subnet.
- **Scaling and metrics**: `metric_scaling.py` provides product-ID based defaults and per-device overrides from `devices.json`. `get_device_metrics` reads DPS entries (e.g. `"18"`, `"19"`, `"20"`) and maps them to `power_watts`, `voltage_volts`, `current_amps`, `is_on` under `home.electricity.tuya.<device>.<metric>`.
- **Main loop**: repolls every `config.SMART_PLUG_POLL_INTERVAL`; if several consecutive polls return zero metrics, automatically rescans and rebuilds its device list; also periodically rescans based on `config.TUYA_REDISCOVERY_INTERVAL`.

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

- Uses the Tuya IoT Cloud via `tinytuya.Cloud()`; credentials and region configured via `tinytuya.json` created by `python -m tinytuya wizard`.
- Robust response normalization: handles multiple response shapes (string, dict, list), surfaces meaningful log messages on cloud API errors or unexpected structures.
- Metric derivation mirrors the local script: normalizes cloud-reported `cur_power`, `cur_voltage`, `cur_current`, and related fields with per-device scales from `devices.json`; emits under `home.electricity.tuya.<device>.<metric>`.
- The main polling loop periodically refreshes the device list and scales, and uses `send_metrics` for batch emission.

Use the local path where possible (lower latency, no cloud dependency), and fall back to the cloud path where LAN access is limited.

#### HA-bridge fallback (`tuya_ha_bridge_to_graphite.py`)

- Reads device states from Home Assistant's REST API (`presence/homeassistant_api.py`'s `HomeAssistantAPI`) for devices listed in `ha_bridge_devices.json` (name, `tuya_device_id`, an optional `switch_entity`, and a `sensors` map of metric suffix -> HA entity ID).
- `filter_devices_needing_fallback` skips any device with a recent entry in `tuya_local_state.json` (via `load_recent_local_successes`, TTL `10 * SMART_PLUG_POLL_INTERVAL` - same pattern as `tuya_cloud_to_graphite.py`), so it only actually emits metrics for devices local polling currently can't reach.
- `add_derived_power` fills in `power_watts` for devices that only expose `total_kwh` in HA, from the delta between polls (state in `ha_bridge_state.json`); skipped on the first poll, non-positive elapsed time, or a counter reset.
- A device configured with a direct `power_watts` sensor never receives an
  energy-derived substitute during a direct-sensor outage; absent, malformed,
  NaN, and infinite readings are omitted while valid zero remains zero.
- Emits under the same `home.electricity.tuya.<device>.<metric>` namespace as the local/cloud paths, so existing dashboards pick it up transparently.

### Aggregation (`aggregate_energy.py`)

- **Input data**: reads per-device power series directly from Graphite whisper files over SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH` in `config.py`).
- **State and integration**: maintains cumulative energy state in `energy_state.json` via dataclasses (`DeviceEnergyState`, `EnergyState`); integrates power over time to compute daily/weekly/monthly/yearly kWh totals.
- **Outputs**: whole-home aggregate metrics under `home.electricity.aggregate`, plus per-device cumulative energy metrics.

### Presence subsystem (`presence_to_graphite.py` and `presence/*`)

- **Inputs/sources**:
  - WiFi scanning via `presence/wifi_scan.py` — tracks active MACs on the WiFi network, with an "offline grace period" to smooth brief dropouts.
  - Tado geofencing via `presence/tado_api.py` — uses `TADO_ACCESS_TOKEN` or `TADO_USERNAME`/`TADO_PASSWORD`, tokens persisted in `presence/state.json`.
  - Home Assistant via `presence/homeassistant_api.py` — REST API with `HA_TOKEN` to query `device_tracker` entities.
- **Configuration**: `presence/people_config.yaml` defines people, associated MACs, Tado users/IDs, Home Assistant entities, and metric prefixes.
- **MAC learning**: `presence/mac_learning.py` and `presence/mac_learning_state.json` correlate WiFi devices, Home Assistant presence, hostnames, IPv6 suffixes, etc., and suggest new MAC–person mappings with confidence scores. Suggestions surface in `presence_to_graphite.py --discover` output and in logs.
- **Metrics**: per-person, under a configurable prefix from `people_config.yaml` — `<prefix>.<person>.from_wifi`, `.from_tado`, `.from_homeassistant`, `.is_home`. Aggregate: `<prefix>.count_home`, `<prefix>.anyone_home`, `<prefix>.wifi.devices_present_count`.

Operational runbooks are in `docs/PRESENCE_OPERATIONS.md` and `docs/PRESENCE_STATUS.md`.

### System operation and watchdog (`watchdog_electricity.sh`)

Generic watchdog used on Pi deployments — ensures `kasa_to_graphite.py`, `tuya_local_to_graphite.py`, and `aggregate_energy.py` are running, restarting them if they crash. Scheduled via cron (see comments in the script).

### Deployment architecture

Recommended deployment uses **one Pi per subnet**:

- `blackpi2` on `192.168.86.0/24` (main LAN)
- `flint` on `192.168.1.0/24` (device LAN behind OpenWrt)

Each Pi polls only its local devices — no SSH tunnelling or cross-subnet discovery required. Key configuration: `LOCAL_ROLE = 'main_lan'` (default, disables legacy tunnel code paths), `KASA_DISCOVERY_NETWORKS = [None]` (scan local subnet only), `SSH_TUNNEL_ENABLED = False`, `UDP_TUNNEL_ENABLED = False`.

`flint` maintains a **reverse SSH tunnel** to `quartz` for remote admin access (see `~/code/AGENTS.md` for the tunnel topology and its failure mode).

See `docs/ARCHITECTURE.md` for full details.

## Device history notes

Terse, dated facts about device identity/history that aren't derivable from the code or `device_names.json` alone (cross-agent shared notes - see `~/code/AGENT_POLICY.md`).

- **2026-09-04**: "Amaryllis heater" (`bf5fe742cb004620efteys`) and "Amaryllis Fridge" (`bf6d71e3b1942c0414ud0a`) are the plugs previously named "Dishwasher" and "Freezer" - Nick intentionally renamed/repurposed them in the Tuya app, not new hardware. The *old* "Amaryllis Fridge" plug (`bfa520724989aae1147ahf`) was flaky and is scheduled to be binned; it's already gone from the Tuya cloud and the local network, and its stale `device_names.json` entry is intentionally left in place. "Big water butt pump" (`bf5cbebcc1b18d8615zhvq`) and "Shower" (`bf442ad41a4f6ea3205qwb`) are genuinely new plugs.
- **2026-09-04**: A device broadcasting the Tuya local protocol (`bfd298a0b74895cf9bbsue`, MAC `fc:3c:d7:7f:34:b6`) but absent from both the Tuya cloud project and Home Assistant is a **"Kinetic" branded light switch controller** on WiFi with its own private-label app/account (confirmed by Nick) - not integratable via this repo's normal Tuya cloud/local paths without a separate Kinetic-app-specific integration.

## House power topology (recorded 2026-09-25, during an Equiwatt event)

What is behind which plug, and what each switch actually controls. Established by reading live power per plug, the HA entity list, and process lists on the hosts; inferences are marked as such. Machine-level facts (where hosts live, how to reach them) are in `~/code/AGENTS.md`.

- **"Shed" plug (`bfca739a0f4da457a45his`, Tuya, main LAN) is the master feed for the whole shed**, at the house end of the shed's single power cable. Switching it off cuts the shed freezer, calcite (which hosts the Grafana/Carbon `ubuntu64` VM and the Home Assistant VM), blackpi2 (the coolbox controller and this repo's main-LAN pollers) and the shed Nest node. Never switch it off to save power. Its ~150 W is dominated by the **shed freezer, which has no plug of its own** and so cannot be shed separately.
- The **Amaryllis fridge** is the second-largest shed load (~70 W when running) and is the only shed load that can be dropped for a couple of hours. It is under blackpi2's tight control loop (`temp_humid_graphite_panicboard.py`, root cron every 2 min), which the Equiwatt poller suspends via `input_boolean.amaryllis_fridge_disabled` for the duration of an event (see `~/code/AGENTS.md`, "Equiwatt power hold").
- **quartz is not on the plug HA calls "quartz server".** `switch.quartz_server_socket_1` is the **Office shelves** plug (`10105863c4dd57078c2e`) and reads ~11 W. quartz is almost certainly the **"N desk"** plug (`10105863c4dd57021c2e`): ~175 W with the GPU idle at 46 W and the 20-core host at load 0.2, rising with GPU work. That is an inference from the power figures, not a traced cable.
- **Both ovens are Tuya "dlq" metering circuit breakers on the OpenWrt subnet**, reachable only from flint: Main Oven `bf52baba2305f136datzwi` at 192.168.1.157, Top Oven `bf64fd8c51015ede6db23j` at 192.168.1.135 (protocol 3.4, DHCP addresses). They never appear in a broadcast scan from the main LAN, which is why `--discover` now scans via flint. Real figures: Main Oven preheats at ~3.7 kW / 15.5 A then cycles ~1.5 kW; Top Oven ~3.2 kW / 13.3 A. In Home Assistant their entities are the untranslated product name `ji_liang_duan_lu_qi` / `ji_liang_duan_lu_qi_2`; HA exposes no power sensor for them, its total-energy sensor flickers between 0.001 and 0.002 kWh, and the Top Oven has been `unavailable` in HA since 2026-09-16 while flint polls it fine. Do not use HA for the ovens. The **Towel Rail** (`bffbd6e8894a52ef8e6qba`) is also on the OpenWrt subnet.
- **Unknown Tuya device `bf0ea1e2ab51599d86ckjp` at 192.168.1.155** (protocol 3.4), seen by flint's scan; not in `devices.json` or the 21-device Tuya cloud list. Identity not yet established.
- **Kasa fridge and Kasa tumble dryer produce no data** (nothing in Graphite for the last 7 days as of 2026-09-25); the HA `switch.tumble_dryer` entity is a restored, unavailable ghost. The Kasa poller on blackpi2 is running, so this is device-side or network-side, not yet investigated.
- **Whole-house meter: an ESP32 in the garage reading the electricity meter's pulse LED.** Canonical firmware is **`~/scripts/electricity_monitor/`** in the `scripts` repo (MicroPython 1.23; `main.py`, `monitor.py`, `config.py`, `pulse_test.py`, `upload*.sh`, `LM393-migration-plan.md`); the copy in `~/code/home_assistant/imported_from_scripts/electricity_monitor/` is a partial 2026-05-15 import (no `main.py`, `pulse_test.py` or upload scripts) whose `monitor.py` was later tidied and **never flashed**. History: bare LDR on the ADC until 2025-11-09, then migrated to an **LM393 light-comparator module on GPIO34** (falling-edge interrupt per flash, module has its own pull-up, threshold set by the pot on the module), 4000 imp/kWh, Wi-Fi `12 Lloyd`; last known upload 2025-11-10 over WebREPL, so whether the 2025-11-25 NTP changes are on the board is unrecorded. Board is **`mpy-esp32.local`** (DHCP; 192.168.86.11 on 2026-09-26), WebREPL on :8266 started at boot (password is in the tracked upload script), no USB cable needed; on-board `/state.json` persists the pulse count. It publishes to **both** sinks: Graphite `home.electricity.meter.{power_watts,power_kw,total_kwh,pulse_count}` every 10 s (the `meter` series on the Grafana Usage and Whole House panels) and HA `sensor.electricity_consumption` / `sensor.electricity_power` every 5 min. Nothing on the Mac, quartz or blackpi2 schedules it; it is autonomous. **Status 2026-09-26: alive but blind.** It answers ping, still reports every minute to Graphite and every 5 min to HA, so Wi-Fi and firmware are up; but `power_watts` has read 0 since 17:12 on 2026-09-24 and `pulse_count` last advanced at 13:46 on 2026-09-25 (one stray pulse), then 15,479,500 flat; `total_kwh` stuck at 3869.875. A stuck-high LM393 DO (pot drifted, module mis-aimed at the meter's very faint LED, or a module fault) gives exactly this pattern. Nick's to-do: confirm via WebREPL (`stats()`) and the module's LED, re-aim/re-trim, or find a better source of whole-house data. Until then `sensor.electricity_power` (0 W) is **not** a usable house total, and `home.electricity.aggregate.power_watts` is the sum of monitored plugs only (~340 W with quartz idle, ~480 W with quartz training).
- Heating is Tado (all zones idle/off during the September event); hot water is `water_heater.hot_water` on auto with no live state exposed. Neither is a plug-controllable electrical load.
- Loads worth avoiding during an event are the ones on plugs reading 0 W most of the time: kettle (~2.8 kW), both ovens, shower plug, ensuite shower pump, tumble dryer.

## Repository constraints and state handling

State files not tracked in git (runtime state):

- `tinytuya.json` — Tuya API credentials
- `energy_state.json` — aggregation state
- `presence/state.json` — presence state
- `presence/mac_learning_state.json` — MAC learning state

## Code quality (desloppify)

See [docs/DESLOPPIFY.md](docs/DESLOPPIFY.md) for scores, what was improved, next steps, and the full workflow for running subjective review batches.

## Documentation

All detailed documentation is in `docs/`:

- `docs/ARCHITECTURE.md` – dual-Pi deployment architecture
- `docs/DEVICE_DISCOVERY.md` – automatic device discovery
- `docs/FLINT_SSH_SETUP.md` – remote SSH access to flint
- `docs/TUYA_CLOUD_QUOTA.md` – Tuya cloud quota management
- `docs/PRESENCE_OPERATIONS.md` – presence monitoring ops
- `docs/PRESENCE_STATUS.md` – presence status checks
- `docs/MAC_LEARNING.md` – MAC learning behavior
- `docs/IMPLEMENTATION_PLAN.md` – development roadmap
