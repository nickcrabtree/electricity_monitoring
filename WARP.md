# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Common commands

### Environment setup

- **Conda (preferred on dev machines)**

  ```bash
  conda create -n electricity python=3.11 -y
  conda activate electricity
  pip install -r requirements.txt
  ```
  
  Note: do **not** use one-line `conda run -n ...` in this project; always activate the environment and then run commands.

- **On Raspberry Pi without conda**

  ```bash
  pip3 install --user -r requirements.txt
  ```

---

### Kasa integration (`kasa_to_graphite.py`)

- Discover devices:

  ```bash
  python kasa_to_graphite.py --discover
  ```

- Single cycle (one-pass validation):

  ```bash
  python kasa_to_graphite.py --once
  ```

- Continuous monitoring:

  ```bash
  python kasa_to_graphite.py
  ```

---

### Tuya integrations

#### Local LAN (`tuya_local_to_graphite.py`)

- Discover devices on the local network:

  ```bash
  python tuya_local_to_graphite.py --discover
  ```

- Single cycle:

  ```bash
  python tuya_local_to_graphite.py --once
  ```

- Continuous monitoring:

  ```bash
  python tuya_local_to_graphite.py
  ```

- Device-specific power/voltage/current scales are loaded from `devices.json` and automatically reloaded when the file changes.

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

- First-time setup (creates `tinytuya.json`):

  ```bash
  python -m tinytuya wizard
  ```

- Discover devices via the Tuya cloud:

  ```bash
  python tuya_cloud_to_graphite.py --discover
  ```

- Single cycle:

  ```bash
  python tuya_cloud_to_graphite.py --once
  ```

- Continuous monitoring:

  ```bash
  python tuya_cloud_to_graphite.py
  ```

`tuya_cloud_to_graphite.py` also honors per-device scaling from `devices.json`, similar to the local path.

---

### Aggregation (`aggregate_energy.py`)

- Single cycle (compute aggregate metrics once):

  ```bash
  python aggregate_energy.py --once
  ```

- Continuous aggregation loop:

  ```bash
  python aggregate_energy.py
  ```

- State file: `energy_state.json`.

---

### Presence monitoring (`presence_to_graphite.py`)

Configuration lives in `presence/people_config.yaml`. Key environment variables:

- Home Assistant:
  - `HA_TOKEN` for API access (used by `presence/homeassistant_api.py`).
- Tado:
  - `TADO_ACCESS_TOKEN` **or**
  - `TADO_USERNAME` and `TADO_PASSWORD`
  (used by `presence/tado_api.py`, with state persisted in `presence/state.json`).

Commands:

- Discover WiFi devices and view mapping suggestions / MAC-learning hints:

  ```bash
  python presence_to_graphite.py --discover
  ```

- Single presence update cycle:

  ```bash
  python presence_to_graphite.py --once
  ```

- Continuous monitoring loop:

  ```bash
  python presence_to_graphite.py
  ```

Operational runbooks and deeper presence details are in `docs/PRESENCE_OPERATIONS.md` and `docs/MAC_LEARNING.md`.

---

### Graphite/Carbon connectivity checks

Graphite/Carbon is typically at `192.168.86.123:2003`.

```bash
nc -zv 192.168.86.123 2003
echo "test.metric 1 $(date +%s)" | nc 192.168.86.123 2003
```

---

### Notes on tests and linting

This repository does **not** ship a formal test suite or lint configuration. To validate changes:

- Prefer running the relevant script in `--once` mode as a fast smoke test.
- For device-facing scripts, you can also use `--discover` to ensure discovery paths still work.

---

### SSH tips

**Backgrounding processes via SSH:** When starting a background process on a remote host via SSH, a simple `nohup cmd &` will hang because the parent SSH session waits for the child. Wrap the command in a bash subshell:

```bash
# This hangs:
ssh host 'nohup python script.py &'

# This works:
ssh host 'bash -c "nohup python script.py >> log.txt 2>&1 &"'
```

---

## Big-picture architecture overview

### Core configuration (`config.py`)

- Defines the **Graphite/Carbon target** (`CARBON_SERVER`, `CARBON_PORT`), **poll intervals** (e.g. `SMART_PLUG_POLL_INTERVAL`), and the **metric prefix** (`METRIC_PREFIX`, typically `home.electricity`).
- Provides settings for:
  - Network scanning (`KASA_DISCOVERY_NETWORKS`).
  - Rediscovery cadence (`KASA_REDISCOVERY_INTERVAL`, `TUYA_REDISCOVERY_INTERVAL`).
  - Graphite whisper access via SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH`, etc.) used by the aggregation script.

All higher-level scripts import `config.py` rather than hard-coding these values.

---

### Metric emission helpers (`graphite_helper.py`)

- `send_metric` and `send_metrics` encapsulate TCP writes to the Carbon server, handling timeouts, batching, and logging.
- `format_device_name` normalizes human-friendly device names to metric-safe IDs:
  - Lowercases, replaces spaces/dashes with underscores, strips special chars, and collapses multiple underscores.
- All scripts build metric paths by combining `config.METRIC_PREFIX`, a **source** (e.g. `kasa`, `tuya`, `aggregate`), the formatted device name (if applicable), and a metric suffix.

This ensures consistent naming across Kasa, Tuya, aggregation, and presence-related metrics.

---

### Device naming and identity (`device_names.py`)

- `device_names.py` persists a mapping from **stable IDs** to **friendly names** in `device_names.json`:
  - Kasa: MAC addresses.
  - Tuya: permanent device IDs.
- On first discovery, scripts call `get_device_name(id, fallback_alias)`:
  - If unknown, they store the device's reported alias and reuse it on subsequent runs.
- This makes metric paths stable even when IP addresses change.
- See `docs/DEVICE_DISCOVERY.md` for details on automatic discovery.

Metric paths follow:

```text
home.electricity.kasa.<friendly_name>.<metric>
home.electricity.tuya.<friendly_name>.<metric>
```

with `<friendly_name>` produced by `format_device_name`.

---

### Kasa pipeline (`kasa_to_graphite.py`)

- **Discovery:**
  - Uses Kasa's UDP broadcast discovery on the local subnet.
- **Polling and metrics:**
  - For each discovered device, `get_device_metrics`:
    - Refreshes the device state with retries and exponential backoff.
    - Emits metrics like:
      - `home.electricity.kasa.<device>.power_watts`
      - `home.electricity.kasa.<device>.voltage_volts`
      - `home.electricity.kasa.<device>.current_amps`
      - `home.electricity.kasa.<device>.is_on`
  - `poll_devices_once` gathers metrics concurrently via `asyncio`, then uses `send_metrics` to batch-send to Graphite.
- **Main loop:**
  - `main_loop`:
    - Maintains a view of active devices.
    - Triggers rediscovery after several failed polls or after `KASA_REDISCOVERY_INTERVAL` seconds.

---

### Tuya pipelines (local and cloud)

#### Local LAN (`tuya_local_to_graphite.py`)

- **Discovery:**
  - Uses `tinytuya.deviceScan()` to find devices on the local subnet.
- **Scaling and metrics:**
  - Scaling is handled by `metric_scaling.py` which provides product-ID based defaults and per-device overrides from `devices.json`.
  - `get_device_metrics` reads DPS entries (e.g. `"18"`, `"19"`, `"20"`) and maps them to:
    - `power_watts`
    - `voltage_volts`
    - `current_amps`
    - `is_on`
  - Metric prefix:

    ```text
    home.electricity.tuya.<device>.<metric>
    ```

- **Main loop:**
  - Keeps a current set of reachable devices, repolling every `config.SMART_PLUG_POLL_INTERVAL`.
  - If several consecutive polls return zero metrics, it automatically rescans and rebuilds its device list.
  - Periodically rescans based on `config.TUYA_REDISCOVERY_INTERVAL`.

#### Tuya Cloud (`tuya_cloud_to_graphite.py`)

- Uses the Tuya IoT Cloud via `tinytuya.Cloud()`:
  - Credentials and region are configured via `tinytuya.json` created by `python -m tinytuya wizard`.
- Robust response normalization:
  - Handles multiple response shapes (string, dict, list).
  - Surfaces meaningful log messages when the cloud API returns errors or unexpected structures.
- Metric derivation mirrors the local script:
  - Normalizes cloud-reported `cur_power`, `cur_voltage`, `cur_current`, and related fields with per-device scales from `devices.json`.
  - Emits metrics under `home.electricity.tuya.<device>.<metric>`.
- The main polling loop periodically refreshes the device list and scales, and uses `send_metrics` for batch emission.

Use the local path where possible (lower latency, no cloud dependency), and fall back to the cloud path where LAN access is limited.

---

### Aggregation (`aggregate_energy.py`)

- **Input data:**
  - Reads per-device power series directly from Graphite whisper files over SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH` in `config.py`).
- **State and integration:**
  - Maintains cumulative energy state in `energy_state.json` via dataclasses (`DeviceEnergyState`, `EnergyState`).
  - Integrates power over time to compute daily, weekly, monthly, and yearly kWh totals.
- **Outputs:**
  - Whole-home aggregate metrics under `home.electricity.aggregate`.
  - Per-device cumulative energy metrics.

---

### Presence subsystem (`presence_to_graphite.py` and `presence/*`)

- **Inputs/sources:**
  - WiFi scanning via `presence/wifi_scan.py`:
    - Tracks active MACs on the WiFi network, with an \"offline grace period\" to smooth brief dropouts.
  - Tado geofencing via `presence/tado_api.py`:
    - Uses `TADO_ACCESS_TOKEN` or `TADO_USERNAME`/`TADO_PASSWORD`, with tokens persisted in `presence/state.json`.
  - Home Assistant via `presence/homeassistant_api.py`:
    - Uses REST API with `HA_TOKEN` to query `device_tracker` entities.
- **Configuration:**
  - `presence/people_config.yaml` defines:
    - People, associated MACs, Tado users/IDs, Home Assistant entities, and metric prefixes.
- **MAC learning:**
  - `presence/mac_learning.py` and `presence/mac_learning_state.json` implement an \"intelligent MAC learning\" system:
    - Correlates WiFi devices, Home Assistant presence, hostnames, IPv6 suffixes, etc.
    - Suggests new MAC–person mappings with confidence scores.
    - Suggestions surface in `presence_to_graphite.py --discover` output and in logs.
- **Metrics:**
  - For each person, `presence_to_graphite.py` emits metrics under a configurable prefix (from `people_config.yaml`), such as:

    ```text
    <prefix>.<person>.from_wifi
    <prefix>.<person>.from_tado
    <prefix>.<person>.from_homeassistant
    <prefix>.<person>.is_home
    ```

  - Aggregate metrics:

    ```text
    <prefix>.count_home
    <prefix>.anyone_home
    <prefix>.wifi.devices_present_count
    ```

Operational runbooks are in `docs/PRESENCE_OPERATIONS.md` and `docs/PRESENCE_STATUS.md`.

---

### System operation and watchdog (`watchdog_electricity.sh`)

- `watchdog_electricity.sh` is a generic watchdog used on Pi deployments:
  - Ensures `kasa_to_graphite.py`, `tuya_local_to_graphite.py`, and `aggregate_energy.py` are running.
  - Restarts them if they crash.
- Schedule via cron (see comments in the script).

---

### Deployment architecture

The current **recommended** deployment uses **one Pi per subnet**:

- `blackpi2` on `192.168.86.0/24` (main LAN)
- `flint` on `192.168.1.0/24` (device LAN behind OpenWrt)

Each Pi polls only its local devices. No SSH tunnelling or cross-subnet discovery is required.

Key configuration:

- `LOCAL_ROLE = 'main_lan'` (default) — disables legacy tunnel code paths.
- `KASA_DISCOVERY_NETWORKS = [None]` — scan local subnet only.
- `SSH_TUNNEL_ENABLED = False` and `UDP_TUNNEL_ENABLED = False`.

`flint` maintains a **reverse SSH tunnel** to `quartz` for remote admin access.

See `docs/ARCHITECTURE.md` for full details.

---

## Repository constraints and state handling

### Conda usage

- When using conda, always:

  ```bash
  conda activate electricity
  # then run python commands here
  ```

- Avoid one-line `conda run -n electricity ...` patterns; they buffer stdout/stderr and make long-running processes hard to debug.

### State files

The following files are not tracked in git and contain runtime state:

- `tinytuya.json` - Tuya API credentials
- `energy_state.json` - aggregation state
- `presence/state.json` - presence state
- `presence/mac_learning_state.json` - MAC learning state

---

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
