# Presence Monitoring Operational Playbook

This document provides step-by-step procedures for maintaining and troubleshooting the presence monitoring system.

## Daily Operations

### Health Checks

#### Service Status
```bash
# Check if presence service is running
sudo systemctl status presence-monitoring

# Expected: active (running) status
```

#### Metric Freshness
```bash
# Check recent logs for successful metric sends
sudo journalctl -u presence-monitoring --since "5 minutes ago" | grep "Successfully sent"

# Expected: Regular entries showing "Successfully sent 27 metrics"
# If no recent entries, service may be stuck
```

#### Quick Presence Test
```bash
# Send test metric to verify Graphite connectivity
echo "home.presence.test_operational 1 $(date +%s)" | nc 192.168.86.123 2003

# Check in Grafana that the test metric appears
```

## Device Management

### Adding New People/Devices

#### Step 1: Discover WiFi Devices
```bash
# Run discovery mode to see all devices on network
python /home/nickc/code/electricity_monitoring/presence_to_graphite.py --discover

# Output shows: IP, MAC, hostname for all detected devices
# Look for devices belonging to new person
```

#### Step 2: Update Configuration
```bash
# Edit the people configuration
nano /home/nickc/code/electricity_monitoring/presence/people_config.yaml

# Add new person:
# - person: newperson
#   homeassistant_entity: person.newperson  # if in Home Assistant
#   homeassistant_device_trackers: []       # device tracker entities
#   wifi_macs: ["AA:BB:CC:DD:EE:FF"]       # discovered MAC addresses
#   wifi_hostnames: ["newperson-phone"]    # hostname patterns
```

#### Step 3: Restart Service
```bash
sudo systemctl restart presence-monitoring

# Verify service starts successfully
sudo systemctl status presence-monitoring
```

#### Step 4: Validate New Person
```bash
# Check logs for new person detection
sudo journalctl -u presence-monitoring -f

# Expected: logs showing detection of new person
# Look for: "Detected newperson at home" or similar
```

### Updating MAC Address Mappings

#### When to Update
- New phone/device for existing person
- MAC address randomization changes
- Device replacement

#### Discovery Process
```bash
# 1. Run discovery to see current network state
python /home/nickc/code/electricity_monitoring/presence_to_graphite.py --discover

# 2. Have person toggle WiFi on their device
# 3. Run discovery again to see changes
python /home/nickc/code/electricity_monitoring/presence_to_graphite.py --discover

# 4. Compare outputs to identify new/changed MAC addresses
```

#### Update Configuration
```bash
# Edit people config file
nano /home/nickc/code/electricity_monitoring/presence/people_config.yaml

# Update wifi_macs list for the person:
# wifi_macs: ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"]  # add new MAC
```

#### Apply Changes
```bash
# Restart service to pick up config changes
sudo systemctl restart presence-monitoring

# Monitor logs to verify detection
sudo journalctl -u presence-monitoring -f | grep "person_name"
```

## System Health Monitoring

### Log Analysis

#### Normal Operation Indicators
```bash
# Check for regular successful metric sends
sudo journalctl -u presence-monitoring --since "1 hour ago" | grep "Successfully sent" | wc -l

# Expected: ~720 entries per hour (every 5 seconds)
# If significantly lower, investigate service issues
```

#### Error Patterns to Watch For
```bash
# WiFi scanning errors
sudo journalctl -u presence-monitoring | grep -i "wifi.*error"

# Home Assistant API errors
sudo journalctl -u presence-monitoring | grep -i "homeassistant.*error"

# Graphite connection errors
sudo journalctl -u presence-monitoring | grep -i "graphite.*error"

# General service crashes
sudo journalctl -u presence-monitoring | grep -i "traceback\|exception"
```

### Performance Monitoring

#### Metric Counts
```bash
# Check current metric output
sudo journalctl -u presence-monitoring -n 50 | grep "Successfully sent"

# Expected patterns:
# - 27 metrics = 6 people × 3 sources + 6 aggregate + 3 device counts
# - If count is wrong, check configuration or service logs
```

#### Memory/CPU Usage
```bash
# Check resource usage
ps aux | grep presence_to_graphite.py

# Expected: Low CPU (~1-5%), reasonable memory (~50-100MB)
# High CPU may indicate scanning issues
```

### Network Connectivity Tests

#### Graphite Server
```bash
# Test Carbon port connectivity
nc -zv 192.168.86.123 2003

# Expected: "Connection to 192.168.86.123 2003 port [tcp/*] succeeded!"
```

#### Home Assistant
```bash
# Test Home Assistant API (requires token in environment file)
source /etc/presence-monitoring.env
curl -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" http://homeassistant.local:8123/api/

# Expected: JSON response with message "API running"
```

#### Local Network Scanning
```bash
# Test if network scanning works
sudo python -c "
from scapy.all import ARP, Ether, srp
import ipaddress
arp_request = ARP(pdst='192.168.86.0/24')
broadcast = Ether(dst='ff:ff:ff:ff:ff:ff')
arp_request_broadcast = broadcast / arp_request
answered_list = srp(arp_request_broadcast, timeout=2, verbose=False)[0]
print(f'Found {len(answered_list)} devices')
"

# Expected: Found X devices (where X > 5 typically)
```

## Troubleshooting Guide

### Service Won't Start

#### Check Configuration Syntax
```bash
# Validate YAML syntax
python -c "
import yaml
with open('/home/nickc/code/electricity_monitoring/presence/people_config.yaml') as f:
    yaml.safe_load(f)
print('Configuration valid')
"
```

#### Check File Permissions
```bash
# Verify service files are accessible
ls -la /etc/systemd/system/presence-monitoring.service
ls -la /etc/presence-monitoring.env

# Verify script is executable
ls -la /home/nickc/code/electricity_monitoring/presence_to_graphite.py
```

#### Check Dependencies
```bash
# Verify Python environment
/usr/bin/python3 -c "import requests, yaml, scapy" 2>&1 | echo "Dependencies: $?"
# Expected: "Dependencies: 0"
```

### No Presence Detection

#### Verify WiFi Scanning
```bash
# Run discovery manually as root
sudo python3 /home/nickc/code/electricity_monitoring/presence_to_graphite.py --discover

# Expected: List of detected devices with IPs and MACs
# If empty, check network interface/routing
```

#### Verify Home Assistant Integration
```bash
# Test Home Assistant connectivity manually
source /etc/presence-monitoring.env
python3 -c "
import requests
resp = requests.get('http://homeassistant.local:8123/api/states/person.nick', 
                   headers={'Authorization': f'Bearer {os.environ[\"HOMEASSISTANT_TOKEN\"]}')
print(f'Status: {resp.status_code}, State: {resp.json().get(\"state\", \"N/A\")}')
"
```

#### Check MAC Address Mappings
```bash
# Verify MACs in config match discovered devices
grep -A5 "wifi_macs" /home/nickc/code/electricity_monitoring/presence/people_config.yaml
# Compare with discovery output
```

### Metrics Not Reaching Graphite

#### Test Direct Graphite Send
```bash
# Send test metric manually
echo "home.presence.test_troubleshoot 123 $(date +%s)" | nc 192.168.86.123 2003

# Check if it appears in Grafana/Graphite UI
```

#### Check Service Logs for Send Failures
```bash
# Look for Graphite send errors
sudo journalctl -u presence-monitoring | grep -A5 -B5 "graphite.*error"

# Look for network timeouts
sudo journalctl -u presence-monitoring | grep -i timeout
```

### High Resource Usage

#### Check Scan Frequency
```bash
# Verify scan intervals in config
grep -E "scan_interval|poll_interval" /home/nickc/code/electricity_monitoring/presence/people_config.yaml

# Recommended values:
# wifi scan_interval_seconds: 30
# homeassistant poll_interval_seconds: 300
```

#### Monitor Process Resource Usage
```bash
# Real-time monitoring
top -p $(pgrep -f presence_to_graphite.py)

# If CPU consistently >10%, investigate network issues
# If memory constantly growing, check for memory leaks
```

## Credential Management

### Home Assistant Token Rotation

#### Generate New Token
1. Login to Home Assistant at http://homeassistant.local:8123
2. Go to Profile → Long-lived access tokens
3. Generate new token with name "Presence Monitoring"
4. Copy the token value

#### Update Environment File
```bash
# Edit environment file (requires sudo)
sudo nano /etc/presence-monitoring.env

# Update HOMEASSISTANT_TOKEN=your_new_token_here
# Save and exit

# Restart service to use new token
sudo systemctl restart presence-monitoring
```

#### Verify New Token Works
```bash
# Test new token
sudo journalctl -u presence-monitoring -f | grep -i "homeassistant"

# Expected: No authentication errors in logs
```

### Tado Credentials (if re-enabled)

#### Update Credentials
```bash
# Edit environment file
sudo nano /etc/presence-monitoring.env

# Update:
# TADO_USERNAME=your_email@example.com  
# TADO_PASSWORD=your_new_password

# Restart service
sudo systemctl restart presence-monitoring
```

## Monitoring and Alerting Setup

### Key Metrics to Monitor

#### Service Health
- Service uptime: `systemctl is-active presence-monitoring`
- Metric send success rate: Log analysis for "Successfully sent" frequency
- Error rate: Count of ERROR level log entries

#### Presence Detection Accuracy  
- People count consistency: `home.presence.count_home` should match sum of individual presence
- Anyone home logic: `home.presence.anyone_home` should be 1 when count > 0
- WiFi device detection rate: `home.presence.wifi.devices_present_count` relative to expected devices

### Grafana Alerts (Recommended)

#### Service Down Alert
- Query: `home.presence.count_home` 
- Condition: No data for 2 minutes
- Action: Send notification

#### Inconsistent Detection Alert
- Query: `home.presence.anyone_home` vs sum of individual presence
- Condition: Values don't match for >5 minutes
- Action: Log investigation alert

## Maintenance Schedule

### Weekly
- Review service logs for errors: `sudo journalctl -u presence-monitoring --since "7 days ago" | grep -i error`
- Check metric freshness in Grafana dashboards
- Verify presence detection matches actual occupancy

### Monthly  
- Review and update MAC address mappings if needed
- Check Home Assistant token expiration (tokens don't expire but good to verify)
- Analyze presence patterns for accuracy improvements

### Quarterly
- Review configuration for new people/devices
- Update documentation with any process changes
- Performance optimization based on usage patterns

---

**Last Updated**: 2024-10-23  
**Contact**: Check service logs first, then review this playbook for solutions