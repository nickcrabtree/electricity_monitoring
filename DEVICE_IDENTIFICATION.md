# Kasa Device Identification Guide

Since DHCP assigns dynamic IP addresses, use **hostname** or **MAC address** to identify devices. These remain constant even when IPs change.

## Finding Device Hostnames

### Method 1: OpenWrt Web UI (Easiest)
1. Log into your OpenWrt router (http://192.168.86.1)
2. Go to **Status → DHCP Leases** (or Clients)
3. Look for devices with names like:
   - `Smart-Plug-XXXXX`
   - `Kasa-Device`
   - `TP-Link-XXXXX`
   - Device manufacturer names

### Method 2: From Your Machine
```bash
# Show all devices with hostnames
getent hosts

# Or use avahi-browse for mDNS devices (if available)
avahi-browse -a | grep -i kasa

# Or use nmap to find devices and their hostnames
nmap -A 192.168.86.0/24
```

### Method 3: SSH to OpenWrt Router
```bash
ssh root@192.168.86.1

# List all connected clients with hostnames
cat /var/dhcp.leases
# Output format: timestamp MAC hostname IP remaining-time

# Or use dnsmasq status
nslookup [hostname] 192.168.86.1
```

## Finding Device MAC Addresses

### Method 1: OpenWrt Web UI
1. Log into http://192.168.86.1
2. Go to **Status → DHCP Leases**
3. Look for the MAC address column next to each device

### Method 2: From Your Machine
```bash
# Show ARP table with MAC addresses
arp -a | grep -i kasa
# Output: hostname (192.168.86.XX) at aa:bb:cc:dd:ee:ff [ether]

# Use nmap to scan and show MAC addresses
nmap -sn 192.168.86.0/24 | grep -A1 "MAC"
```

### Method 3: On the Device Itself
For Kasa smart plugs:
- Press and hold the button for ~3 seconds
- Check the LED pattern or app display for MAC/serial info

## Configuration Examples

### Using Hostnames (Recommended for mDNS)
```python
# In config.py
KASA_DEVICES = {
    'smart-plug-living-room.local': 'living_room_plug',
    'smart-plug-kitchen.local': 'kitchen_plug',
    'kasa-bedroom.local': 'bedroom_plug',
}
```

**Pros:**
- Human-readable
- Survives DHCP IP changes
- Works across subnets with mDNS
- Easy to remember and manage

**Cons:**
- Requires mDNS to work
- Some networks may not resolve `.local` domains

### Using MAC Addresses (Most Reliable)
```python
# In config.py
KASA_DEVICES = {
    '84:0D:8E:XX:XX:XX': 'living_room_plug',     # MAC address format: AA:BB:CC:DD:EE:FF
    '84:0D:8E:YY:YY:YY': 'kitchen_plug',
    '84:0D:8E:ZZ:ZZ:ZZ': 'bedroom_plug',
}
```

**Pros:**
- Works everywhere (no DNS required)
- Unique and never changes
- Hardware-based identifier
- Works across subnets

**Cons:**
- Requires ARP lookup (must be on same network segment)
- Less human-friendly

### Using IP Addresses (Not Recommended - DHCP)
```python
# In config.py
KASA_DEVICES = {
    '192.168.86.50': 'living_room_plug',
}
```

**Pros:**
- Direct and simple

**Cons:**
- ⚠️ **CHANGES WITH DHCP** - not reliable
- Only works on same subnet
- Requires static DHCP reservation

## Finding Specific Device Info

### Get Device MAC from Hostname
```bash
# Resolve hostname to IP first
ip=$(getent hosts smart-plug-living-room.local | awk '{print $1}')

# Get MAC address
arp $ip

# Output: smart-plug-living-room.local (192.168.86.50) at 84:0d:8e:ab:cd:ef [ether]
```

### Get Device Hostname from MAC
```bash
# Find device by MAC and show its hostname
arp -a | grep -i "84:0d:8e:ab:cd:ef"

# Or from OpenWrt:
ssh root@192.168.86.1 "cat /var/dhcp.leases | grep 84:0d:8e:ab:cd:ef"
```

## Setting Up Configuration

### Step 1: Identify Your Devices
```bash
# Scan for Kasa devices on your network
nmap -sn 192.168.86.0/24 | grep -i "Kasa\|TP-Link\|Smart"

# Or from OpenWrt DHCP leases
ssh root@192.168.86.1 "cat /var/dhcp.leases"
```

### Step 2: Collect Hostnames/MACs
Create a list:
```
Device Name          | Hostname/MAC
--------------------|------------------------------
Living Room Plug     | smart-plug-living-room.local  OR  84:0D:8E:AA:BB:01
Kitchen Plug         | smart-plug-kitchen.local      OR  84:0D:8E:AA:BB:02
Bedroom Plug         | kasa-bedroom.local            OR  84:0D:8E:AA:BB:03
```

### Step 3: Update config.py
```python
KASA_DEVICES = {
    'smart-plug-living-room.local': 'living_room_plug',
    'smart-plug-kitchen.local': 'kitchen_plug',
    'kasa-bedroom.local': 'bedroom_plug',
}

# Alternative with MAC addresses:
KASA_DEVICES = {
    '84:0D:8E:AA:BB:01': 'living_room_plug',
    '84:0D:8E:AA:BB:02': 'kitchen_plug',
    '84:0D:8E:AA:BB:03': 'bedroom_plug',
}
```

### Step 4: Test Discovery
```bash
conda activate electricity
cd /home/nickc/code/electricity_monitoring

# Test discovery
python kasa_to_graphite.py --discover

# Test single poll
python kasa_to_graphite.py --once

# Run continuously
python kasa_to_graphite.py
```

## Troubleshooting

### Hostname not resolving
- Ensure mDNS is working: `ping hostname.local`
- Check if `.local` works: `avahi-browse -a`
- Fall back to MAC address method

### MAC address not found in ARP
- Device must be online and communicating
- May need to ping device first: `ping device-hostname`
- Try: `arp -d *` to clear ARP cache, then retry

### Still not finding devices
1. Verify device is powered on
2. Check it's on the network: `ping [device-hostname or IP]`
3. Use raw IP if all else fails, but note it will change
4. Contact your router admin for DHCP lease table

## Persistence Across DHCP Lease Changes

With hostname/MAC configuration:
- **No action needed** - script resolves hostname/MAC to current IP automatically
- IP changes are transparent to monitoring
- Script handles ARP updates seamlessly

Example flow:
1. Config specifies: `'84:0D:8E:AA:BB:01': 'living_room_plug'`
2. Device gets IP 192.168.86.50 → script connects and monitors
3. Lease expires, device gets 192.168.86.100 → script auto-resolves to new IP
4. Monitoring continues without configuration changes
