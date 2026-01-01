# Cross-Subnet Kasa Device Setup Guide

> **Status: LEGACY / OPTIONAL**
>
> This guide describes how to reach 192.168.1.x devices from a single host on 192.168.86.x.
>
> With the current **dual-Pi deployment** (`blackpi2` on 192.168.86.x + `flint` on 192.168.1.x), cross-subnet discovery is **not required** — each Pi polls its own local subnet directly. See `docs/CURRENT_ARCHITECTURE_OVERVIEW.md`.

---

## Current Setup

You've enabled cross-subnet routing on your OpenWrt router to access devices on the 192.168.1.0 network from your 192.168.86.0 network.

## Configuration Options

### Option 1: Automatic Discovery (Recommended if working)

The system now supports discovering Kasa devices on multiple subnets:

```python
# In config.py, devices will be discovered from:
KASA_DISCOVERY_NETWORKS = [
    None,                  # Local subnet (192.168.86.0/24)
    '192.168.1.0/24',     # OpenWrt subnet with smart plugs
]
```

**Test discovery:**
```bash
conda activate electricity
python kasa_to_graphite.py --discover
```

### Option 2: Manual Device Configuration (Fallback)

If automatic discovery doesn't find your 192.168.1.0 devices, manually specify them:

```python
# In config.py, update KASA_DEVICES:
KASA_DEVICES = {
    '192.168.1.50': 'living_room_plug',
    '192.168.1.51': 'kitchen_plug',
    '192.168.1.52': 'bedroom_plug',
}
```

**Find device IPs on 192.168.1.0 network:**
```bash
# Check your OpenWrt router's DHCP client list
# Or scan from your machine:
nmap -p 9999 192.168.1.0/24
# Kasa devices listen on port 9999
```

**Test specific device:**
```bash
python kasa_to_graphite.py --discover
```

The script will use both discovered and manually configured devices.

### Option 3: Both Automatic + Manual

Combine both approaches for reliability:
- Auto-discovery finds devices that respond
- Manual config provides fallback for devices that don't respond to broadcast

```python
# In config.py:
KASA_DISCOVERY_NETWORKS = [None, '192.168.1.0/24']

KASA_DEVICES = {
    '192.168.1.50': 'living_room_plug',  # Fallback if discovery fails
}
```

## Troubleshooting

### Discovery finds no 192.168.1.0 devices

**Check connectivity first:**
```bash
# Can you ping the OpenWrt router?
ping 192.168.1.1

# Can you reach a specific device?
ping 192.168.1.50

# Can you reach the Kasa port?
nc -zv 192.168.1.50 9999
```

**If pings work but kasa discovery fails:**
1. The cross-subnet routing might need adjustments
2. Try manual device configuration instead
3. Check if Kasa devices require a local discovery broadcast

### Device discovered but fails to update

This is common if the device is:
- Offline or powered down
- On a network segment with restricted access
- Not responding to port 9999 initially (retry may work)

The script handles this gracefully - devices will be retried on next poll cycle.

### Using manual configuration

Once you've added devices to `KASA_DEVICES`:

```bash
# Test discovery
python kasa_to_graphite.py --discover

# Test single poll
python kasa_to_graphite.py --once

# Start continuous monitoring
python kasa_to_graphite.py
```

## Performance Considerations

- Discovery scans both subnets sequentially (10s timeout each)
- Manual devices in config.py are connected immediately
- Polling happens in parallel with async

For best performance with many devices across subnets, use manual configuration.

## Next Steps

1. **Find your device IPs:**
   - Check OpenWrt DHCP clients list for 192.168.1.x Kasa devices
   - Note their IP addresses and device names

2. **Add to config.py:**
   ```python
   KASA_DEVICES = {
       '192.168.1.XX': 'device_name_1',
       '192.168.1.YY': 'device_name_2',
   }
   ```

3. **Test:**
   ```bash
   conda activate electricity
   python kasa_to_graphite.py --discover
   python kasa_to_graphite.py --once
   ```

4. **Verify in Graphite:**
   ```bash
   # Check if metrics are arriving
   curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*.*&from=-5min&format=json'
   ```

5. **Run continuously:**
   ```bash
   python kasa_to_graphite.py &
   ```
