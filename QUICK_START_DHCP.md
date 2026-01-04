# Quick Start: Kasa Monitoring with DHCP Devices

Since Kasa devices use DHCP and get dynamic IPs, configure them using **hostname** or **MAC address** instead.

## 5-Minute Setup

### 1. Find Your Devices

**Option A: SSH to OpenWrt (fastest)**
```bash
ssh root@192.168.86.1
cat /var/dhcp.leases
```

Look for Kasa devices:
```
1635593400 84:0d:8e:aa:bb:01 Smart-Plug-Living 192.168.86.50 1635593600
1635593400 84:0d:8e:aa:bb:02 Smart-Plug-Kitchen 192.168.86.51 1635593600
```

**Option B: From your machine**
```bash
arp -a | grep -i kasa
```

### 2. Choose Identification Method

| Method | Example | When to Use |
|--------|---------|-------------|
| **Hostname** | `Smart-Plug-Living.local` | Recommended - human-readable |
| **MAC** | `84:0D:8E:AA:BB:01` | Failsafe - works everywhere |
| **IP** | `192.168.86.50` | ❌ Not recommended - DHCP |

### 3. Configure Devices

Edit `config.py`:

```python
KASA_DEVICES = {
    'Smart-Plug-Living.local': 'living_room_plug',
    'Smart-Plug-Kitchen.local': 'kitchen_plug',
    # Or using MAC addresses:
    # '84:0D:8E:AA:BB:01': 'living_room_plug',
    # '84:0D:8E:AA:BB:02': 'kitchen_plug',
}
```

### 4. Test

```bash
cd /home/nickc/code/electricity_monitoring
conda activate electricity

# Test discovery
python kasa_to_graphite.py --discover

# Test single poll
python kasa_to_graphite.py --once

# Run continuously
python kasa_to_graphite.py &
```

## How It Works

The script automatically:
1. **Resolves** hostname/MAC to current IP
2. **Connects** to the device
3. **Polls** power metrics
4. **Sends** to Graphite
5. **Repeats** every 30 seconds

If the device's IP changes (DHCP renewal):
- Script re-resolves the hostname/MAC
- Gets new IP automatically
- Continues monitoring seamlessly

## Hostname vs MAC Address

### Hostnames (Preferred)
```python
KASA_DEVICES = {
    'Smart-Plug-Living.local': 'living_room_plug',
}
```

✅ **Pros:**
- Easy to read and remember
- Works with mDNS (.local)
- Human-friendly

❌ **Cons:**
- Requires mDNS to resolve
- May not work on all networks

### MAC Addresses (Failsafe)
```python
KASA_DEVICES = {
    '84:0D:8E:AA:BB:01': 'living_room_plug',
}
```

✅ **Pros:**
- Works anywhere (no DNS)
- Hardware identifier - never changes
- Most reliable

❌ **Cons:**
- Requires ARP lookup
- Less memorable

## Troubleshooting

### Devices not discovered

```bash
# Check if device is on network
ping Smart-Plug-Living.local

# Or try MAC resolution
arp -a | grep -i \"84:0d:8e\"

# Check Graphite is reachable
nc -zv 192.168.86.123 2003
```

### Hostname not resolving

```bash
# Try .local domain
ping device-name.local

# If that fails, use MAC address instead
# Get MAC from: arp -a or OpenWrt DHCP leases
```

## Example Full Configuration

```python
# config.py
KASA_DEVICES = {
    # Living room plug with hostname
    'Smart-Plug-LivingRoom.local': 'living_room_plug',
    
    # Kitchen plug with MAC (in case hostname fails)
    '84:0D:8E:AA:BB:02': 'kitchen_plug',
    
    # Bedroom plug using static hostname
    'bedroom-kasa.local': 'bedroom_plug',
}
```

## Checking Metrics

Once running, verify data in Graphite:

```bash
# List all electricity metrics
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.*' | head

# Get power readings for last 5 minutes
curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*.power_watts&from=-5min&format=json'
```

## Next Steps

1. ✅ Find your devices (SSH to router)
2. ✅ Update `config.py`
3. ✅ Test with `--discover` and `--once`
4. ✅ Run continuously: `python kasa_to_graphite.py`
5. ✅ Check Graphite for metrics
6. ✅ Set up as systemd service or cron

See `DEVICE_IDENTIFICATION.md` for detailed device lookup instructions.
"}