# Intelligent MAC Learning System

## Overview

The presence monitoring system now includes an intelligent MAC learning system that automatically detects when devices change MAC addresses (due to privacy randomization) and suggests new mappings based on multiple identification methods.

## How It Works

### Multi-Modal Device Identification

The system uses several techniques to identify devices across MAC address changes:

1. **IPv6 Suffix Matching** (Most Reliable)
   - Extracts the device identifier portion of IPv6 addresses
   - Often stable even when MAC addresses change
   - High confidence weight in similarity scoring

2. **Device Fingerprinting**
   - OS detection via nmap scanning
   - Open port patterns (specific services/ports)
   - Device type identification (iPhone, Android, etc.)

3. **Hostname Pattern Recognition**
   - Consistent hostname patterns across MAC changes
   - Device names that persist

4. **Temporal Correlation**
   - Correlates Home Assistant presence changes with new device appearances
   - Detects when someone appears "home" in HA but not via WiFi

### Learning Triggers

The system learns when:
- A person shows as "home" in Home Assistant but "away" via WiFi
- Unknown devices appear on the network
- Timing correlation suggests the unknown device belongs to the "missing" person

## Configuration

### Enable/Disable Learning

MAC learning is automatically enabled when the presence monitoring system starts. No additional configuration needed.

### Confidence Thresholds

```python
learning_threshold = 0.6   # Minimum score to suggest mapping (60%)
auto_add_threshold = 0.85  # Score for high-confidence suggestions (85%)
```

### Evidence Weights

- IPv6 suffix match: 40% confidence boost
- Device type match: 30% confidence boost  
- Hostname pattern: 20% confidence boost
- Open port similarity: 10% confidence boost

## Usage

### Viewing Suggestions

#### In Discovery Mode
```bash
python presence_to_graphite.py --discover
```
Shows recent MAC learning suggestions along with device discovery.

#### In Service Logs
```bash
sudo journalctl -u presence-monitoring -f | grep "MAC Learning"
```
Watch for real-time learning suggestions.

### Acting on Suggestions

When the system suggests a new MAC mapping:

1. **Review the Evidence**:
   - Confidence percentage
   - Matching criteria (device type, IPv6, etc.)
   - Device information (IP, hostname)

2. **Update Configuration**:
   ```bash
   nano presence/people_config.yaml
   ```
   Add the suggested MAC to the person's `wifi_macs` list:
   ```yaml
   - person: nick
     wifi_macs:
       - "BE:80:47:8F:9F:78"  # Existing MAC
       - "AA:BB:CC:DD:EE:FF"  # New suggested MAC
   ```

3. **Restart Service**:
   ```bash
   sudo systemctl restart presence-monitoring
   ```

### Example Suggestion Output

```
MAC Learning Suggestion (confidence: 78%):
  Add MAC 2E:E2:97:9D:9A:CB to person 'mo'
  Device: 192.168.86.22 - maureen-s-a16.lan
  - Device type matches previous devices
  - Hostname pattern matches
  - Timing correlates with Home Assistant presence
  Command: Add '2E:E2:97:9D:9A:CB' to wifi_macs for mo in people_config.yaml
```

## System State Files

### MAC Learning State
- **File**: `presence/mac_learning_state.json`
- **Contains**: Device fingerprints, learning history, presence correlation data
- **Auto-managed**: Created and updated automatically

### Learning History
The system maintains:
- Device fingerprints by MAC address
- Person-specific fingerprint patterns
- Presence change history for correlation
- Previously made suggestions (to avoid spam)

## Advanced Features

### Automatic High-Confidence Additions

When confidence exceeds 85%, the system logs a high-priority suggestion:
```
*** HIGH CONFIDENCE - Consider auto-adding ***
```
These suggestions have very strong evidence and are usually safe to add.

### Fingerprint Evolution

The system continuously updates device fingerprints:
- New open ports are added (not replaced)
- Missing data is filled in over time
- Confidence scores improve with more data

### Correlation Windows

- **Presence correlation**: 5-minute window for arrival/departure timing
- **Suggestion cooldown**: Won't repeat the same suggestion
- **History retention**: Keeps 100 most recent presence changes per person

## Troubleshooting

### No Suggestions Generated

**Possible Causes**:
- No previous fingerprints for the person (new person in system)
- All people detected via current MACs
- Home Assistant not showing presence discrepancies

**Solutions**:
- Let system run for a few days to build fingerprint database
- Manually trigger learning by having someone leave and return
- Check Home Assistant integration is working

### False Positive Suggestions

**Possible Causes**:
- Guest devices with similar characteristics
- Network equipment with similar fingerprints

**Solutions**:
- Review confidence scores (ignore <70%)
- Check device hostnames and IP addresses
- Wait for multiple correlation events before acting

### Service Performance

The MAC learning system is designed to be lightweight:
- Runs only during metric send cycles (every 5 seconds)
- Skips processing when no fresh scan data available
- Uses efficient fingerprint storage and comparison

## Security Considerations

### Privacy
- Fingerprints stored locally only
- No external network calls for learning
- MAC addresses and device info stay on your network

### Network Impact
- Uses existing WiFi scans (no additional scanning)
- Optional nmap fingerprinting (can be disabled)
- Respects existing scan intervals

## Maintenance

### Periodic Cleanup

The system automatically manages its state files, but you can manually clean up old data:

```bash
# Remove learning suggestions older than 30 days
# (This is just informational - system does this automatically)
```

### Monitoring Health

Check learning system health:
```bash
# View recent suggestions
python presence_to_graphite.py --discover

# Check system is learning
grep "MAC learning" presence/mac_learning_state.json
```

## Integration with Existing System

The MAC learning system integrates seamlessly:
- ✅ **No breaking changes** to existing configuration
- ✅ **Backward compatible** with current MAC mappings
- ✅ **Complementary** to Home Assistant integration
- ✅ **Optional** - can be disabled if needed

The system enhances reliability by automatically adapting to device MAC address changes while maintaining all existing functionality.

---

**Status**: ✅ **Active** - Intelligent MAC learning is now running in your presence monitoring system