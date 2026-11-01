# Home Electricity Monitoring System

Comprehensive electricity monitoring for home automation, integrating smart plugs, smart meter readers, and circuit monitors into Graphite/Grafana.

## Quick Start

### 1. Create and Activate Conda Environment

```bash
cd ~/code/electricity_monitoring

# Create new conda environment
conda create -n electricity python=3.11 -y

# Activate environment
conda activate electricity

# Install dependencies
pip install -r requirements.txt
```

**Note**: Always activate the environment before running scripts:
```bash
conda activate electricity
```

### 2. Discover Kasa Smart Plugs

```bash
# Make sure conda environment is activated
conda activate electricity

# Discover devices on network
python kasa_to_graphite.py --discover

# Single live poll (writes metrics and state)
python kasa_to_graphite.py --once

# Start continuous monitoring
python kasa_to_graphite.py
```

### Configure Tuya Devices

```bash
conda activate electricity

# Run Tuya wizard to get device IDs and local keys
python -m tinytuya wizard

# Discover and test
python tuya_local_to_graphite.py --discover
python tuya_local_to_graphite.py --once
```

#### Private local-device overrides

When LAN discovery can identify a device but cannot provide its local key, the
local collector can merge an external private device record from:

```text
~/.config/electricity-monitoring/tuya_local_devices.json
```

The file must be owned by the collector user and have mode `0600`. It contains
a top-level `devices` list whose records have `id`, `name`, `ip`, `key`, and
`version` fields:

```json
{
  "devices": [
    {
      "id": "tuya-device-id",
      "name": "Friendly device name",
      "ip": "192.168.1.155",
      "key": "<16-character-local-key>",
      "version": "3.4"
    }
  ]
}
```

The file is intentionally outside the repository and must never be committed
or included in logs. Set `TUYA_LOCAL_DEVICE_OVERRIDES_FILE` only when a
different private file location is required.

## Project Structure

```
electricity_monitoring/
├── kasa_to_graphite.py        # Kasa smart plug collector
├── tuya_local_to_graphite.py  # Tuya local LAN collector
├── tuya_cloud_to_graphite.py  # Tuya cloud collector
├── aggregate_energy.py        # Energy aggregation
├── presence_to_graphite.py    # Presence monitoring
├── config.py                  # Configuration
├── metric_scaling.py          # Centralized metric scaling
├── graphite_helper.py         # Graphite utilities
├── device_names.py            # Device name persistence
├── devices.json               # Device definitions
├── presence/                  # Presence subsystem
├── docs/                      # Documentation
└── watchdog_electricity.sh    # Process watchdog
```

## Features

- **Kasa Smart Plugs**: Real-time power, voltage, current monitoring
- **Tuya Smart Plugs**: Local LAN and cloud polling
- **Energy Aggregation**: Daily/weekly/monthly/yearly kWh totals
- **Presence Monitoring**: WiFi, Tado, and Home Assistant integration

## Graphite Metrics

All metrics follow the pattern: `home.electricity.<source>.<device>.<metric>`

### Kasa Metrics
- `home.electricity.kasa.<device_name>.power_watts`
- `home.electricity.kasa.<device_name>.voltage_volts`
- `home.electricity.kasa.<device_name>.current_amps`
- `home.electricity.kasa.<device_name>.is_on`

### Tuya Metrics
- `home.electricity.tuya.<device_name>.power_watts`
- Similar structure to Kasa

### Smart Meter Metrics
- `home.electricity.meter.power_kw` - Current power draw
- `home.electricity.meter.total_kwh` - Cumulative consumption
- `home.electricity.meter.pulse_count` - Raw pulse count

## Configuration

Edit `config.py` to set:
- Graphite server IP (default: 192.168.86.123:2003)
- Polling intervals
- Device-specific settings

## Troubleshooting

### No devices discovered
- Check devices are on same network
- Verify devices are powered on
- Try `kasa discover` CLI tool directly

### Cannot connect to Graphite
- Verify Graphite server is running: `nc -zv 192.168.86.123 2003`
- Check firewall rules

### Permissions error
- Check conda environment: `conda activate electricity && which python`

### Conda environment not found
- Create it: `conda create -n electricity python=3.11 -y`
- Activate it: `conda activate electricity`
- Install deps: `pip install -r requirements.txt`

## Deployment and development

Collectors write production metrics and state, including in `--once` mode. Use
mocked tests for offline validation. Do not start duplicate collectors on a host
that already runs them.

The deployment uses one Pi per subnet, plus presence monitoring on quartz. Read
[architecture](docs/ARCHITECTURE.md) and [operations](docs/OPERATIONS.md) before
installing services or changing an existing deployment. Read
[development](docs/DEVELOPMENT.md) for the conda environment, tests and staged-file
pre-commit checks.

## Documentation

The [documentation index](docs/README.md) links to all runbooks, design notes and
historical evidence. [AGENTS.md](AGENTS.md) contains the repository safeguards
and task-specific reading list.
