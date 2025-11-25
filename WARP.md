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

### Aggregation (`aggregate_energy_enhanced.py`)

- Single cycle (compute aggregate metrics once):

  ```bash
  python aggregate_energy_enhanced.py --once
  ```

- Continuous aggregation loop:

  ```bash
  python aggregate_energy_enhanced.py
  ```

- State file: `energy_state_enhanced.json`.

`aggregate_energy_enhanced.py` supersedes the older `aggregate_energy.py` and should be preferred for new work.

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

Operational runbooks and deeper presence details are in `PRESENCE_OPERATIONS.md` and `MAC_LEARNING.md`.

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

## Big-picture architecture overview

### Core configuration (`config.py`)

- Defines the **Graphite/Carbon target** (`CARBON_SERVER`, `CARBON_PORT`), **poll intervals** (e.g. `SMART_PLUG_POLL_INTERVAL`), and the **metric prefix** (`METRIC_PREFIX`, typically `home.electricity`).
- Controls cross-subnet discovery and tunneling:
  - `KASA_DISCOVERY_NETWORKS` for Kasa scan subnets (e.g. local + OpenWrt subnet).
  - `SSH_TUNNEL_ENABLED`, `SSH_REMOTE_HOST`, `SSH_TUNNEL_SUBNET`, and related fields for SSH-based Kasa/Tuya reachability.
  - `UDP_TUNNEL_ENABLED` and associated ports/broadcast address for UDP broadcast tunneling.
- Provides settings for:
  - Rediscovery cadence (`KASA_REDISCOVERY_INTERVAL`, `TUYA_REDISCOVERY_INTERVAL`).
  - Graphite whisper access via SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH`, etc.) used by the aggregation script.
  - ESP32 receiver host/port (future smart meter integration).

All higher-level scripts import `config.py` rather than hard-coding these values.

---

### Metric emission helpers (`graphite_helper.py`)

- `send_metric` and `send_metrics` encapsulate TCP writes to the Carbon server, handling timeouts, batching, and logging.
- `format_device_name` normalizes human-friendly device names to metric-safe IDs:
  - Lowercases, replaces spaces/dashes with underscores, strips special chars, and collapses multiple underscores.
- All scripts build metric paths by combining `config.METRIC_PREFIX`, a **source** (e.g. `kasa`, `tuya`, `aggregate`), the formatted device name (if applicable), and a metric suffix.

This ensures consistent naming across Kasa, Tuya, aggregation, and presence-related metrics.

---

### Device naming and identity (`device_names.py` + `DEVICE_DISCOVERY.md`)

- `device_names.py` persists a mapping from **stable IDs** to **friendly names** in `device_names.json`:
  - Kasa: MAC addresses.
  - Tuya: permanent device IDs.
- On first discovery, scripts call `get_device_name(id, fallback_alias)`:
  - If unknown, they store the device\'s reported alias and reuse it on subsequent runs.
- This makes metric paths stable even when IP addresses change (DHCP, cross-subnet routing).
- `DEVICE_DISCOVERY.md` documents how fully automatic discovery + naming replaced older static `KASA_DEVICES` / `TUYA_DEVICES` config blocks.

Metric paths follow:

```text
home.electricity.kasa.<friendly_name>.<metric>
home.electricity.tuya.<friendly_name>.<metric>
```

with `<friendly_name>` produced by `format_device_name`.

---

### Kasa pipeline (`kasa_to_graphite.py` + tunneling helpers)

- **Discovery:**
  - Uses Kasa\'s UDP broadcast discovery on the local subnet and, optionally, additional subnets defined in `config.KASA_DISCOVERY_NETWORKS`.
  - Supports SSH-based cross-subnet discovery via `ssh_tunnel_manager.SSHTunnelManager` when `SSH_TUNNEL_ENABLED` is true.
  - Can optionally route UDP broadcast through `udp_tunnel.UDPTunnel` if `UDP_TUNNEL_ENABLED` is set.
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
    - Optionally starts/stops UDP tunneling around the discovery phase.

Kasa discovery and polling are intentionally resilient to intermittent device/network issues and cross-subnet setups.

---

### Tuya pipelines (local and cloud)

#### Local LAN (`tuya_local_to_graphite.py`)

- **Discovery:**
  - Uses `tinytuya.deviceScan()` to find devices on the local subnet.
  - Can also be guided by SSH-based remote scanning (see `README_SSH_SETUP.md` and `CROSS_SUBNET_SETUP.md`) for secondary subnets.
- **Scaling and metrics:**
  - `devices.json` holds per-device DPS scaling information; loaded by `load_device_scales` and automatically reloaded when the file changes.
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

### Aggregation (`aggregate_energy_enhanced.py`)

- **Input data:**
  - Reads per-device power series directly from Graphite whisper files over SSH (`GRAPHITE_SSH_HOST`, `GRAPHITE_WHISPER_PATH` in `config.py`).
- **State and integration:**
  - Maintains cumulative energy state in `energy_state_enhanced.json` via dataclasses (`DeviceEnergyState`, `EnergyState`).
  - Integrates power over time to compute:
    - Daily, weekly, monthly, and yearly kWh totals.
  - Handles boundary resets:
    - Day: midnight.
    - Week: Monday 01:00.
    - Month: 1st of month at 01:00.
    - Year: Jan 1 at 01:00.
- **Outputs:**
  - Whole-home aggregate metrics (under `home.electricity.aggregate`), e.g.:

    ```text
    home.electricity.aggregate.power_watts
    home.electricity.aggregate.energy_kwh_daily
    home.electricity.aggregate.energy_kwh_weekly
    home.electricity.aggregate.energy_kwh_monthly
    home.electricity.aggregate.energy_kwh_yearly
    ```

  - Per-device cumulative energy metrics using keys like `<source>.<device>` (e.g. `tuya.n_desk`) to emit:

    ```text
    home.electricity.<source>.<device>.energy_kwh_daily
    home.electricity.<source>.<device>.energy_kwh_weekly
    home.electricity.<source>.<device>.energy_kwh_monthly
    home.electricity.<source>.<device>.energy_kwh_yearly
    ```

`aggregate_energy_enhanced.py` is the authoritative place for whole-home and per-device cumulative energy metrics and should be preferred over `aggregate_energy.py`.

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

Operational runbooks (systemd service, log patterns, detailed health checks) are documented in `PRESENCE_OPERATIONS.md` and `PRESENCE_STATUS.md`.

---

### System operation and watchdog (`watchdog_electricity.sh`)

- `watchdog_electricity.sh` (shell script in repo root) is a generic watchdog used on Pi deployments:
  - Ensures `kasa_to_graphite.py`, `tuya_local_to_graphite.py`, and `aggregate_energy_enhanced.py` are running.
  - Restarts them if they crash and logs to `/home/pi/electricity_watchdog.log`.
- Typical deployments schedule this script via cron (see existing comments in the script and host-level crontab).
- Example systemd/cron setups for Kasa, Tuya, aggregation, and presence are described in:
  - `README.md`
  - `PRESENCE_OPERATIONS.md`

`WARP.md` should stay focused on repo-level behavior; defer host-specific service wiring to those docs.

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

See `docs/CURRENT_ARCHITECTURE_OVERVIEW.md` for full details.

---

### Legacy: Cross-subnet networking via SSH tunnels

> **Status: LEGACY / OPTIONAL** — only used when `LOCAL_ROLE = 'single_host_cross_subnet'`.

The codebase still supports the older pattern where a single host on 192.168.86.x reaches devices on 192.168.1.x via SSH tunnels through OpenWrt:

- `ssh_tunnel_manager.py`:
  - Discover devices on the remote subnet by reading DHCP leases via SSH.
  - Create per-device local TCP forwards (`localhost:<port> -> remote_ip:9999`).
- `udp_tunnel.py`:
  - Forward UDP broadcast discovery traffic to a remote broadcast address through SSH + `socat`/`nc`.

Controlled by flags in `config.py`:

- `SSH_TUNNEL_ENABLED`
- `UDP_TUNNEL_ENABLED`

For detailed setup and troubleshooting, refer to:

- `README_SSH_SETUP.md`
- `CROSS_SUBNET_SETUP.md`
- `SSH_TUNNEL_IMPLEMENTATION_SUMMARY.md`
- `SSH_TUNNEL_AUTO_DISCOVERY.md`
- `NETWORK_SETUP_STATUS.md`

---

## Repository constraints and state handling

### Conda usage

- When using conda, always:

  ```bash
  conda activate electricity
  # then run python commands here
  ```

- Avoid one-line `conda run -n electricity ...` patterns; they buffer stdout/stderr and make long-running processes hard to debug.

### Editing non–git-controlled files

Before modifying any **non–git-tracked** state/config files, create a timestamped backup with suffix `yyyy-dd-mm_hhmm.bak`. This includes, for example:

- `device_names.json`
- `devices.json`
- `tinytuya.json`
- `energy_state_enhanced.json`
- Files under `presence/` such as:
  - `presence/people_config.yaml`
  - `presence/state.json`
  - `presence/mac_learning_state.json`

Create backups alongside the original file so changes are easily reversible.

---

## Deeper docs

When you need more operational detail or design background, consult:

- `README.md` – project overview, quick start examples, and background.
- `docs/CURRENT_ARCHITECTURE_OVERVIEW.md` – dual-Pi deployment architecture (blackpi2 + flint).
- `IMPLEMENTATION_PLAN.md` – phased implementation roadmap (Kasa, Tuya, ESP32, Glow, Grafana, orchestration).
- `DEVICE_DISCOVERY.md` – automatic device discovery and naming (Kasa/Tuya) and `device_names.json`.
- `CROSS_SUBNET_SETUP.md` – legacy: multi-subnet Kasa setup and manual discovery fallback.
- `README_SSH_SETUP.md` – legacy: SSH configuration for OpenWrt and cross-subnet detection.
- `PRESENCE_OPERATIONS.md` and `PRESENCE_STATUS.md` – presence monitoring operations and health checks.
- `MAC_LEARNING.md` – detailed behavior of the MAC learning system.

This WARP guide is intentionally concise and repo-focused. Use these documents for host-specific deployment, cron/systemd configuration, and deeper reasoning about network and presence behavior.
