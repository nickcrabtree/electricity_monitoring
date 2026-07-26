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

This repository does not ship a formal test suite or lint configuration beyond `tests/`. To validate changes:

```bash
conda run -n electricity python -m pytest tests/ -v
```

- Prefer running the relevant script in `--once` mode as a fast smoke test.
- For device-facing scripts, `--discover` exercises discovery paths.

All Python on quartz uses conda environments — never bare `pip` or `python`.

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
python tuya_local_to_graphite.py --discover  # Discover devices on the local network
python tuya_local_to_graphite.py --once      # Single cycle
python tuya_local_to_graphite.py             # Continuous monitoring
```

Device-specific power/voltage/current scales are loaded from `devices.json` and automatically reloaded when the file changes.

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

```bash
python -m tinytuya wizard                    # First-time setup (creates tinytuya.json)
python tuya_cloud_to_graphite.py --discover  # Discover devices via the Tuya cloud
python tuya_cloud_to_graphite.py --once      # Single cycle
python tuya_cloud_to_graphite.py             # Continuous monitoring
```

`tuya_cloud_to_graphite.py` also honors per-device scaling from `devices.json`, similar to the local path.

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
