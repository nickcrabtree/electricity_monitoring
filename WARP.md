# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

Home electricity monitoring system that integrates multiple data sources (Kasa smart plugs, Tuya devices, ESP32 pulse readers, DIN rail monitors) into a Graphite/Grafana infrastructure. All metrics are sent to Carbon server at `192.168.86.123:2003`.

## Development Environment

### Setup
```bash
# Create and activate conda environment
conda create -n electricity python=3.11 -y
conda activate electricity

# Install dependencies
pip install -r requirements.txt
```

**Always activate the conda environment before running any code:**
```bash
conda activate electricity
```

### Common Commands

#### Discovery and Testing
```bash
# Discover Kasa devices on network
python kasa_to_graphite.py --discover

# Test single poll (verify Graphite connection)
python kasa_to_graphite.py --once

# Start continuous monitoring
python kasa_to_graphite.py
```

### Tuya Device Setup (Local LAN)
```bash
# Run Tuya wizard to get device IDs and local keys
python -m tinytuya wizard

# After configuring devices in config.py:
python tuya_to_graphite.py --discover
python tuya_to_graphite.py
```

### Tuya Cloud Monitoring (no LAN access required)
```bash
# Ensure tinytuya.json exists (created by wizard with Access ID/Secret/Region)
python tuya_cloud_to_graphite.py --discover   # list devices and status keys
python tuya_cloud_to_graphite.py --once       # send one batch
python tuya_cloud_to_graphite.py              # continuous
```

### Whole-home Aggregation
```bash
# Compute and send aggregate power and cumulative kWh counters
python aggregate_energy.py --once   # one cycle
python aggregate_energy.py          # continuous (uses energy_state.json for persistence)
```

#### Testing Graphite Connection
```bash
# Test Carbon connectivity
nc -zv 192.168.86.123 2003

# Send test metric
echo "test.metric 1 $(date +%s)" | nc 192.168.86.123 2003
```

#### Running as Service
```bash
# systemd service management
sudo systemctl status kasa-monitoring
sudo systemctl start kasa-monitoring
sudo systemctl restart kasa-monitoring
sudo systemctl stop kasa-monitoring
sudo journalctl -u kasa-monitoring -f
```

## Architecture

### Core Components

1. **config.py** - Centralized configuration
   - Graphite server settings (IP: 192.168.86.123, Port: 2003)
   - Polling intervals (smart plugs: 30s, meter: 5s)
   - Device configurations (Kasa, Tuya)
   - Metric prefix: `home.electricity`

2. **graphite_helper.py** - Shared utilities
   - `send_metric()` - Send single metric to Carbon
   - `send_metrics()` - Batch send multiple metrics (preferred)
   - `format_device_name()` - Normalize device names for metric paths
   - Pattern based on `~/scripts/graphite_temperatures.py`

3. **kasa_to_graphite.py** - Kasa smart plug integration (✅ COMPLETE)
   - Async polling using python-kasa library
   - Auto-discovery with periodic re-discovery (every 10 min)
   - Extracts: power (watts), voltage (volts), current (amps), on/off state
   - Command-line modes: `--discover`, `--once`, or continuous

4. **tuya_cloud_to_graphite.py** - Tuya smart plug integration (✅ COMPLETE)
   - Uses tinytuya cloud library for remote device access
   - Requires device credentials from `tinytuya wizard`
   - Extracts: power (watts), voltage (volts), current (amps), on/off state
   - Command-line modes: `--discover`, `--once`, or continuous
   - Sends ~38 metrics per poll cycle

5. **aggregate_energy_enhanced.py** - Per-device and whole-house energy aggregation (✅ COMPLETE)
   - Combines Kasa + Tuya power readings into whole-house totals
   - Tracks individual device energy consumption (kWh)
   - Provides daily/weekly/monthly/yearly cumulative energy for each device
   - Sends ~41 metrics per poll cycle (5 whole-house + 36 per-device metrics)
   - Uses `energy_state_enhanced.json` for persistence
   - Replaces original `aggregate_energy.py`

6. **esp32_receiver.py** / **mqtt_to_graphite.py** - Smart meter pulse reader (TODO)
   - ESP32 sends whole-house consumption data
   - Option A: HTTP POST receiver (simpler)
   - Option B: MQTT subscriber (more robust)

### Metric Naming Convention

All metrics follow: `home.electricity.<source>.<device>.<metric>`

**Examples:**

*Device Power Metrics:*
- `home.electricity.kasa.living_room_lamp.power_watts`
- `home.electricity.kasa.living_room_lamp.voltage_volts`
- `home.electricity.kasa.living_room_lamp.current_amps`
- `home.electricity.kasa.living_room_lamp.is_on`
- `home.electricity.tuya.<device_name>.power_watts`

*Per-Device Energy Metrics (NEW):*
- `home.electricity.kasa.living_room_lamp.energy_kwh_daily`
- `home.electricity.kasa.living_room_lamp.energy_kwh_weekly`
- `home.electricity.kasa.living_room_lamp.energy_kwh_monthly`
- `home.electricity.kasa.living_room_lamp.energy_kwh_yearly`
- `home.electricity.tuya.<device_name>.energy_kwh_daily`
- `home.electricity.tuya.<device_name>.energy_kwh_weekly`
- `home.electricity.tuya.<device_name>.energy_kwh_monthly`
- `home.electricity.tuya.<device_name>.energy_kwh_yearly`

*Whole-House Aggregate Metrics:*
- `home.electricity.aggregate.power_watts` (sum of all Kasa + Tuya devices)
- `home.electricity.aggregate.energy_kwh_daily` (resets at local midnight)
- `home.electricity.aggregate.energy_kwh_weekly` (resets 01:00 Monday)
- `home.electricity.aggregate.energy_kwh_monthly` (resets 01:00 on 1st)
- `home.electricity.aggregate.energy_kwh_yearly` (resets 01:00 on Jan 1)

*Future Metrics:*
- `home.electricity.meter.power_kw`
- `home.electricity.circuit.<circuit_name>.<metric>`

Device names are normalized: lowercase, spaces→underscores, special chars removed.

### Data Flow

1. **Discovery Phase**: Scripts discover devices on local network (via broadcast/scanning)
2. **Polling Phase**: Scripts poll devices at configured intervals (30s for plugs, 5s for meter)
3. **Extraction Phase**: Raw device data parsed and converted to standard units
4. **Transmission Phase**: Metrics batched and sent to Carbon via TCP socket (port 2003)
5. **Storage Phase**: Graphite stores time-series data
6. **Visualization Phase**: Grafana queries Graphite and displays dashboards

### Async Pattern

Kasa integration uses Python asyncio for concurrent device polling:
- `discover_devices()` - Async discovery
- `get_device_metrics()` - Async metric collection
- `poll_devices_once()` - Coordinate polling of all devices
- `main_loop()` - Continuous monitoring with periodic re-discovery

This pattern should be followed for other integrations (Tuya, ESP32, etc.).

## Implementation Status

- ✅ **Phase 1.1**: Kasa smart plug integration - COMPLETE
- ✅ **Phase 1.2**: Tuya smart plug integration - COMPLETE
- ✅ **Phase 1.3**: Per-device energy aggregation - COMPLETE
- 🚧 **Phase 2**: ESP32 pulse reader (smart meter) - TODO
- 🚧 **Phase 3**: DIN rail circuit monitors - TODO (requires electrician)
- 🚧 **Phase 4**: Glow/MQTT smart meter data - TODO
- 🚧 **Phase 5**: Grafana dashboard - TODO
- 🚧 **Phase 6**: Orchestration and automation - TODO
- ✅ **Phase 7**: Presence monitoring integration - COMPLETE

See `IMPLEMENTATION_PLAN.md` for detailed roadmap.

## Code Conventions

### Error Handling
- Use try/except blocks for device communication
- Log errors with context (device name, IP, error type)
- Continue polling other devices if one fails
- Never crash the monitoring loop on single device failure

### Logging
- Use Python logging module (configured in config.py)
- Log levels: DEBUG for metrics, INFO for events, ERROR for failures
- Include timestamps in all logs
- Use structured logging: `logger.info(f"Sent {count} metrics to Graphite")`

### Unit Conversion
- Store all power in watts (convert from milliwatts if needed)
- Store voltage in volts (convert from millivolts)
- Store current in amps (convert from milliamps)
- Always check device API response format (may vary by model)

### Socket Communication with Carbon
- Use TCP sockets (not UDP) for reliability
- Format: `metric_name value timestamp\n`
- Batch metrics in single connection when possible
- Set socket timeout (5s recommended)
- Always close sockets after use

## Development Workflow

### Adding New Device Integration

1. Install required library (add to requirements.txt)
2. Create new script (e.g., `<source>_to_graphite.py`)
3. Follow async pattern from `kasa_to_graphite.py`
4. Implement discovery, polling, and metric extraction
5. Use `graphite_helper.send_metrics()` for transmission
6. Add device config to `config.py`
7. Test with `--discover` and `--once` flags
8. Add systemd service or cron job

### Testing Changes

```bash
# Test discovery
python <script>.py --discover

# Test single poll (verify metrics sent)
python <script>.py --once

# Run in foreground to see logs
python <script>.py

# Check Grafana for data arrival
# Navigate to: http://192.168.86.123/grafana
```

### Debugging

```bash
# Check if devices are reachable
kasa discover  # For Kasa devices
python -m tinytuya scan  # For Tuya devices

# Test Graphite connectivity
nc -zv 192.168.86.123 2003

# Send test metric
echo "test.electricity.debug 99 $(date +%s)" | nc 192.168.86.123 2003

# Check systemd logs
sudo journalctl -u kasa-monitoring -n 100 -f

# Verify conda environment
conda activate electricity
which python
python --version
```

## Key Technical Details

### Python-Kasa Library
- Supports async operations (`await device.update()`)
- Energy monitoring methods: `device.has_emeter`, `device.get_emeter_realtime()`
- Response format varies by model (check for `_mw` vs base unit)
- Periodic re-discovery handles network changes

### TinyTuya Library
- Requires local keys obtained from Tuya Cloud API
- `tinytuya wizard` automates credential extraction
- Stores credentials in `tinytuya.json` 
- Requires device version (usually 3.3 or 3.4)

### Graphite/Carbon Protocol
- Plain text protocol over TCP
- Format: `path.to.metric value timestamp\n`
- Timestamp is Unix epoch (seconds)
- No response from server (fire-and-forget)
- Multiple metrics: newline-separated in single connection

### Systemd Service Pattern
- Place service file in `/etc/systemd/system/`
- Use absolute paths for Python binary and script
- Set `WorkingDirectory` to repo directory
- Use `Restart=always` with `RestartSec=10`
- Run as non-root user (nickc)

## Graphite and Grafana Access

### Server Endpoints

The monitoring infrastructure runs on `192.168.86.123`:

- **Grafana** (visualization): http://192.168.86.123:3000 (also http://192.168.86.123/grafana)
- **Graphite Web UI** (render API, metrics browser): http://192.168.86.123 (port 80)
- **Carbon** (plaintext metrics ingestion): 192.168.86.123:2003 (TCP)

### Testing Connectivity

```bash
# Test Carbon port
nc -zv 192.168.86.123 2003

# Send test metric
echo "test.electricity.ping 1 $(date +%s)" | nc 192.168.86.123 2003

# Verify metric was received (wait ~10 seconds)
curl -s 'http://192.168.86.123/render?target=test.electricity.ping&from=-5min&format=json'
```

### Querying Metrics via HTTP API

**Find available metrics:**
```bash
# List all electricity metrics
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.*'

# Find Kasa device power metrics
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.kasa.*.power_watts&format=treejson'
```

**Retrieve metric data:**
```bash
# Get Kasa power data for last 24 hours (JSON)
curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*.power_watts&from=-24hours&format=json'

# Get aggregate power for last hour
curl -s 'http://192.168.86.123/render?target=home.electricity.aggregate.power_watts&from=-1hour&format=json'

# Get Tuya devices
curl -s 'http://192.168.86.123/render?target=home.electricity.tuya.*.*&from=-1hour&format=json'
```

**Check latest timestamps (requires jq or python3):**
```bash
# Using Python
curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*.power_watts&from=-24hours&format=json' \
| python3 -c '
import sys, json, datetime as dt
data = json.load(sys.stdin)
for series in data:
    points = [p[1] for p in series["datapoints"] if p[0] is not None]
    if points:
        latest = max(points)
        print(f"{series["target"]}: {dt.datetime.utcfromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%SZ")}")
    else:
        print(f"{series["target"]}: no data")
'
```

## Related Resources

- Existing monitoring infrastructure: `~/scripts/`
- Reference implementation: `~/scripts/graphite_temperatures.py`
- ESP32 code: `~/scripts/electricity_monitor/`
- Grafana: http://192.168.86.123:3000
- Graphite: http://192.168.86.123
- README.md - Quick start guide
- IMPLEMENTATION_PLAN.md - Detailed roadmap with phases
- PRESENCE_STATUS.md - Presence monitoring system status
- PRESENCE_OPERATIONS.md - Operational playbook for presence monitoring


## Deployment and Remote Access

### Production Deployment on Raspberry Pi

Monitoring scripts run on **blackpi2** (Raspberry Pi) which also hosts other Graphite monitoring scripts.

**SSH Access:**
```bash
ssh pi@blackpi2.local
```

**Repository Location on Pi:**
- `/home/pi/code/electricity_monitoring/` - This repository
- `/home/pi/scripts/` - Shared monitoring scripts repository

### Git-Based Sync Workflow

Code is synchronized between local development machine and Pi using Git:

**Local Development → GitHub → Pi:**
```bash
# On local machine (after making changes)
git add .
git commit -m "Description of changes"
git push

# On Pi (to pull changes)
ssh pi@blackpi2.local
cd /home/pi/code/electricity_monitoring
git pull
```

**Pi → GitHub → Local (rare, for testing on Pi):**
```bash
# On Pi (if you make changes directly)
cd /home/pi/code/electricity_monitoring
git add .
git commit -m "Changes made on Pi"
git push

# On local machine
git pull
```

**Initial Repository Setup on Pi:**
```bash
ssh pi@blackpi2.local
mkdir -p /home/pi/code
cd /home/pi/code
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_github" git clone git@github.com:nickcrabtree/electricity_monitoring.git
cd electricity_monitoring
git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_github"
```

### Python Dependencies on Pi

The Pi uses system Python 3.9 (no conda):

```bash
# Upgrade pip first
pip3 install --user --upgrade pip

# Install dependencies
cd /home/pi/code/electricity_monitoring
/home/pi/.local/bin/pip3 install --user -r requirements.txt
```

**Note**: The Pi uses piwheels for pre-compiled packages optimized for ARM.

### Running on Pi via Cron

Follow the same pattern as other monitoring scripts on blackpi2:

```bash
# Edit crontab
crontab -e

# Add lines (following pattern of other monitoring scripts):
@reboot cd /home/pi/code/electricity_monitoring && stdbuf -oL -eL python3 /home/pi/code/electricity_monitoring/kasa_to_graphite.py > /home/pi/electricity_kasa.log 2>&1
@reboot cd /home/pi/code/electricity_monitoring && stdbuf -oL -eL python3 /home/pi/code/electricity_monitoring/tuya_cloud_to_graphite.py > /home/pi/electricity_tuya_cloud.log 2>&1
@reboot cd /home/pi/code/electricity_monitoring && stdbuf -oL -eL python3 /home/pi/code/electricity_monitoring/aggregate_energy_enhanced.py > /home/pi/electricity_aggregate.log 2>&1
```

**Cron Pattern Explanation:**
- `@reboot` - Run at boot
- `stdbuf -oL -eL` - Line-buffer stdout and stderr (for immediate log visibility)
- `> /home/pi/<log>.log 2>&1` - Redirect all output to log file

**Check Running Status:**
```bash
# SSH into Pi
ssh pi@blackpi2.local

# Check if running
ps aux | grep kasa_to_graphite

# View logs
tail -f /home/pi/electricity.log

# Check current cron jobs
crontab -l
```

### Existing Monitoring Scripts on Pi

Other Graphite monitoring scripts already running on blackpi2:
- `graphite_temperatures.py` - Temperature monitoring
- `temphumid_watering_waterbutt_level_graphite.py` - Garden sensors
- `graphite_uptime.sh` - System uptime (runs every 5 minutes)

All use the same Graphite server (192.168.86.123:2003).

### Watchdog for Automatic Crash Recovery

A watchdog script automatically detects and restarts crashed monitoring processes.

**Watchdog Script:**
- File: `/home/pi/code/electricity_monitoring/watchdog_electricity.sh`
- Purpose: Ensures kasa_to_graphite.py, tuya_cloud_to_graphite.py, and aggregate_energy_enhanced.py stay running
- Schedule: Runs every minute via cron
- Log: `/home/pi/electricity_watchdog.log`

**Setup Watchdog on Pi:**
```bash
# The watchdog is already in the repo, just ensure it's executable
ssh pi@blackpi2.local 'chmod +x /home/pi/code/electricity_monitoring/watchdog_electricity.sh'

# Add to crontab (runs every minute)
ssh pi@blackpi2.local 'crontab -l | { cat; echo "* * * * * /home/pi/code/electricity_monitoring/watchdog_electricity.sh"; } | crontab -'

# Verify cron entry
ssh pi@blackpi2.local 'crontab -l | grep watchdog'
```

**Check Watchdog Status:**
```bash
# View watchdog log
ssh pi@blackpi2.local 'tail -100 /home/pi/electricity_watchdog.log'

# Check which processes are running
ssh pi@blackpi2.local 'ps aux | grep -E "(kasa|tuya|aggregate)" | grep python3 | grep -v grep'

# Check individual script logs
ssh pi@blackpi2.local 'tail -50 /home/pi/electricity_kasa.log'
ssh pi@blackpi2.local 'tail -50 /home/pi/electricity_tuya_cloud.log'
ssh pi@blackpi2.local 'tail -50 /home/pi/electricity_aggregate.log'
```

**Manual Restart:**
```bash
# Kill all monitoring scripts
ssh pi@blackpi2.local 'pkill -f kasa_to_graphite.py || true'
ssh pi@blackpi2.local 'pkill -f tuya_cloud_to_graphite.py || true'
ssh pi@blackpi2.local 'pkill -f aggregate_energy_enhanced.py || true'

# Run watchdog to restart them
ssh pi@blackpi2.local '/home/pi/code/electricity_monitoring/watchdog_electricity.sh'

# Wait a moment, then verify they're running
ssh pi@blackpi2.local 'ps aux | grep python3 | grep -E "(kasa|tuya|aggregate)" | grep -v grep'
```

**Troubleshooting:**

1. **Script won't start:**
   - Check individual log files for Python errors
   - Ensure dependencies are installed: `/home/pi/.local/bin/pip3 install --user -r requirements.txt`
   - Test manually: `ssh pi@blackpi2.local 'cd /home/pi/code/electricity_monitoring && python3 kasa_to_graphite.py --once'`

2. **Watchdog not running:**
   - Verify cron entry: `ssh pi@blackpi2.local 'crontab -l | grep watchdog'`
   - Check if cron service is running: `ssh pi@blackpi2.local 'systemctl status cron'`
   - Test watchdog manually: `ssh pi@blackpi2.local '/home/pi/code/electricity_monitoring/watchdog_electricity.sh'`

3. **No metrics in Grafana:**
   - Verify Carbon connectivity: `nc -zv 192.168.86.123 2003`
   - Check if scripts are logging "Sent N metrics": `ssh pi@blackpi2.local 'grep "Sent.*metrics" /home/pi/electricity_*.log | tail -20'`
   - Query Graphite directly with curl (see "Querying Metrics via HTTP API" above)
   - Check device connectivity: `ssh pi@blackpi2.local 'cd /home/pi/code/electricity_monitoring && python3 kasa_to_graphite.py --discover'`

4. **Kasa device connection issues:**
   - Some Kasa devices (e.g., at 192.168.86.48) may have intermittent connectivity
   - The improved error handling will retry failed devices and continue polling others
   - Check device status on local network
   - Consider power cycling the device if it's consistently failing

5. **Tuya cloud API errors:**
   - Verify tinytuya.json credentials are valid
   - Check Tuya cloud API rate limits
   - Review tuya_cloud.log for "'str' object has no attribute" errors (now handled defensively)
