# Presence Monitoring System Status

## Overview

Home presence monitoring system that correlates WiFi device detection and Home Assistant presence data to track occupancy patterns for energy usage analysis.

**Current Status**: ✅ **DEPLOYED AND OPERATIONAL**

## System Architecture

### Data Sources
- **WiFi Scanning**: ARP-based device discovery on local network (192.168.86.0/24)
- **Home Assistant Integration**: Person and device tracker entities via REST API
- **Tado Integration**: Disabled due to API authentication issues (410 Gone errors)

### People Tracked
- Nick, Susan, Charlie, Archie, Mo, Tom (6 people total)

### Metric Namespace
All metrics published to `home.presence.*`:
- Per-person: `home.presence.{person}.is_home` (0/1)
- Per-person sources: `home.presence.{person}.from_wifi`, `home.presence.{person}.from_homeassistant`
- Aggregate: `home.presence.count_home`, `home.presence.anyone_home`
- Device counts: `home.presence.wifi.devices_present_count`

### Deployment
- **Host**: quartz (Ubuntu, local development machine)
- **Service**: `presence-monitoring.service` (systemd)
- **User**: root (required for network scanning capabilities)
- **Update Frequency**: 27 metrics every 5 seconds
- **Configuration**: `/home/nickc/code/electricity_monitoring/presence/people_config.yaml`
- **Secrets**: `/etc/presence-monitoring.env` (Home Assistant token)

## ✅ Completed Components

### Core Implementation
- [x] `presence_to_graphite.py` - Main orchestrator with CLI modes
- [x] `presence/wifi_scan.py` - Network device scanner using scapy ARP
- [x] `presence/homeassistant_api.py` - Home Assistant REST API client
- [x] `presence/people_config.yaml` - People and device mapping configuration
- [x] `graphite_helper.py` integration - Reused from electricity monitoring
- [x] Runtime state management in `presence/state.json`

### Infrastructure
- [x] Systemd service deployed and running
- [x] Environment file with Home Assistant API token
- [x] Root user permissions for network scanning
- [x] Continuous operation with automatic restart
- [x] Logging to systemd journal

### Testing and Validation
- [x] WiFi device discovery working
- [x] Home Assistant API connectivity confirmed
- [x] Metrics successfully sent to Graphite (192.168.86.123:2003)
- [x] Service health monitoring operational

## 🚧 Remaining Tasks

### 1. Grafana Dashboard Creation
**Status**: Pending
- Create Grafana variables for person selection
- Build presence visualization panels:
  - Individual presence timeline (stepped lines)
  - Total occupancy count
  - Presence overlays on electricity consumption panels
- Set up optional arrival/departure annotations

### 2. System Validation and Testing
**Status**: Pending  
- Test WiFi toggle scenarios for each person
- Validate presence state transitions and grace periods
- Confirm metric accuracy (count equals sum of individual presence)
- Test edge cases (sleeping phones, network changes)

### 3. MAC Address Mapping Refinement  
**Status**: Partially Complete
- Current: Some people mapped via Home Assistant device trackers
- Needed: Direct WiFi MAC address mapping for more reliable detection
- Use discovery mode to identify and map unknown devices
- Update `people_config.yaml` with phone MAC addresses

### 4. Operational Procedures
**Status**: Documentation Needed
- Health monitoring procedures (metric freshness checks)
- MAC address update workflow
- Service management commands
- Troubleshooting guide

### 5. System Robustness Improvements
**Status**: Future Enhancement
- Tune offline grace periods based on observed behavior
- Add guest device counting
- Implement failure isolation between data sources
- API rate limiting and backoff strategies

## Service Management

```bash
# Check service status
sudo systemctl status presence-monitoring

# View real-time logs
sudo journalctl -u presence-monitoring -f

# Restart service
sudo systemctl restart presence-monitoring

# Stop service
sudo systemctl stop presence-monitoring
```

## Configuration Files

### Key Files
- `/home/nickc/code/electricity_monitoring/presence_to_graphite.py` - Main script
- `/home/nickc/code/electricity_monitoring/presence/people_config.yaml` - Configuration
- `/etc/systemd/system/presence-monitoring.service` - Systemd unit
- `/etc/presence-monitoring.env` - Secrets (Home Assistant token)
- `/home/nickc/code/electricity_monitoring/presence/state.json` - Runtime state

### Current Settings
- WiFi scan interval: 30 seconds
- Offline grace period: 300 seconds (5 minutes)
- Home Assistant poll interval: 300 seconds (5 minutes)
- Metric send interval: 5 seconds
- Network scan range: 192.168.86.0/24

## Metrics Being Sent

Current output shows 27 metrics sent every 5 seconds:
- 6 people × 3 metrics each (is_home, from_wifi, from_homeassistant) = 18 metrics
- 3 aggregate metrics (count_home, anyone_home, devices_present_count) = 3 metrics  
- Additional per-person and device metrics = 6 metrics

## Dependencies

### Python Packages (requirements.txt)
- requests (Home Assistant API)
- PyYAML (configuration)
- scapy (ARP scanning)
- python-dateutil (time handling)

### System Requirements
- Network interface access for ARP scanning
- Home Assistant long-lived access token
- Graphite server connectivity (192.168.86.123:2003)

## Known Issues

### Resolved
- ✅ Tado API authentication failures → Bypassed with Home Assistant integration
- ✅ Permission issues for network scanning → Running as root user
- ✅ Home Assistant API connectivity → Long-lived token configured

### Outstanding
- MAC address randomization on modern phones (partially mitigated by Home Assistant)
- Need to populate direct WiFi MAC mappings for more robust detection

## Next Steps Priority

1. **High**: Create Grafana dashboard for presence visualization
2. **High**: Validate system behavior with real presence changes  
3. **Medium**: Map additional WiFi MAC addresses for improved detection
4. **Medium**: Document operational procedures
5. **Low**: Implement optional system robustness improvements

---

**Last Updated**: 2024-10-23  
**System Status**: Operational - collecting presence data successfully