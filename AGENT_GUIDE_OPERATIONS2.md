# Agent guide: operations2

Read when selected by AGENTS.md, not at every startup. Paths and commands are
relative to the repository root. Detailed source sections are retained below.
Current AGENTS.md and shared policy supersede dated history: old quiet-window
times, expired governor lifts, amend/reset advice and older host assumptions do
not grant permission. For current quiet-window status consult
`~/scripts/cross_repo_notes/AGENT_POLICY.md`.

## Detailed reference

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
