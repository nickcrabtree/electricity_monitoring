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

### 2. Discover Kasa Smart Plugs (Phase 1.1 - START HERE)

```bash
# Make sure conda environment is activated
conda activate electricity

# Discover devices on network
python kasa_to_graphite.py --discover

# Test single poll (verify connection to Graphite)
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

## Running in Background

### Option 1: systemd service (recommended for production)

Create `/etc/systemd/system/kasa-monitoring.service`:
```ini
[Unit]
Description=Kasa Smart Plug Monitoring
After=network.target

[Service]
Type=simple
User=nickc
WorkingDirectory=/home/nickc/code/electricity_monitoring
ExecStart=/home/nickc/miniconda3/envs/electricity/bin/python /home/nickc/code/electricity_monitoring/kasa_to_graphite.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Note**: Adjust the path to Python if your conda is installed elsewhere. Find it with:
```bash
conda activate electricity
which python
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable kasa-monitoring
sudo systemctl start kasa-monitoring
sudo systemctl status kasa-monitoring
```

### Option 2: cron @reboot

```bash
crontab -e
# Add line (adjust conda path if needed):
@reboot sleep 30 && cd /home/nickc/code/electricity_monitoring && /home/nickc/miniconda3/envs/electricity/bin/python kasa_to_graphite.py >> /tmp/kasa_monitoring.log 2>&1 &
```

### Option 3: screen/tmux session

```bash
screen -S electricity
cd ~/code/electricity_monitoring
conda activate electricity
python kasa_to_graphite.py
# Detach with Ctrl+A, D
```

## Troubleshooting

### No devices discovered
- Check devices are on same network
- Verify devices are powered on
- Try `kasa discover` CLI tool directly

### Cannot connect to Graphite
- Verify Graphite server is running: `nc -zv 192.168.86.123 2003`
- Check firewall rules
- Test with: `echo "test.metric 1 $(date +%s)" | nc 192.168.86.123 2003`

### Permissions error
- Make scripts executable: `chmod +x *.py`
- Check conda environment: `conda activate electricity && which python`

### Conda environment not found
- Create it: `conda create -n electricity python=3.11 -y`
- Activate it: `conda activate electricity`
- Install deps: `pip install -r requirements.txt`

## Documentation

See `docs/` for detailed documentation:
- `docs/ARCHITECTURE.md` - Deployment architecture
- `docs/DEVICE_DISCOVERY.md` - Device discovery
- `docs/IMPLEMENTATION_PLAN.md` - Development roadmap

## Git / Pi rehydration notes

When (re)hydrating a Raspberry Pi that should run this project (for example `flint`), make sure the Git remotes are configured to use SSH so pushes and pulls work the same as on the main dev machine.

1. On the Pi, ensure the repo exists at `/home/nickc/code/electricity_monitoring` (or clone it there):
   ```bash
   cd /home/nickc/code
   git clone git@github.com:nickcrabtree/electricity_monitoring.git
   cd electricity_monitoring
   ```
2. Verify the `origin` remote uses SSH (not HTTPS):
   ```bash
   git remote -v
   # origin  git@github.com:nickcrabtree/electricity_monitoring.git (fetch)
   # origin  git@github.com:nickcrabtree/electricity_monitoring.git (push)
   ```
   If it shows an `https://github.com/...` URL, update it:
   ```bash
   git remote set-url origin git@github.com:nickcrabtree/electricity_monitoring.git
   ```
3. From another machine, you can manage the Pi clone over SSH (example for `flint`):
   ```bash
   ssh -p 2222 nickc@localhost
   cd /home/nickc/code/electricity_monitoring
   git remote -v
   ```
