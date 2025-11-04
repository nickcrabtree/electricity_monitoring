# SSH Configuration for Cross-Subnet Device Discovery

## Overview
The electricity monitoring system scans for both Kasa and Tuya devices across multiple subnets using SSH tunneling to the OpenWrt router.

## Configuration

### Router Hostname
- The router is accessed via `openwrt.lan` (dynamic hostname)
- This resolves automatically via mDNS/DNS
- No need to hardcode IP addresses

### SSH Key Setup
The Pi authenticates to the router using SSH keys stored in `/etc/dropbear/authorized_keys` on the router.

Current Pi key:
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICAyFZf+SLHXy47XLvZGFai9WjAM9cwZeM48k/qUSz2P pi@blackpi2
```

### Pi SSH Config (`~/.ssh/config`)
```
Host openwrt
    HostName openwrt.lan
    User root
    StrictHostKeyChecking no
    IdentityFile ~/.ssh/id_ed25519
    IdentityFile ~/.ssh/id_rsa
```

## How Cross-Subnet Discovery Works

### Tuya Devices
1. `tuya_local_to_graphite.py` scans local network (192.168.86.0/24)
2. Then SSHs to router and scans 192.168.1.0/24 for devices on port 6668
3. Attempts to connect to discovered devices

### Kasa Devices  
1. `kasa_to_graphite.py` uses UDP broadcast discovery
2. Can optionally use UDP tunneling through SSH for cross-subnet discovery (currently disabled)

## Testing SSH Access
```bash
# From Pi
ssh openwrt 'echo SSH working'
```

## Network Layout
- **192.168.86.0/24**: Main network (Pi, most devices)
- **192.168.1.0/24**: Secondary network via OpenWrt br-lan interface
- Router bridges both networks and can scan for devices on 192.168.1.x

## Device Naming
Devices are identified by stable IDs (MAC addresses for Kasa, device IDs for Tuya) and mapped to friendly names in `device_names.json`. This ensures metrics remain consistent even when IP addresses change.

## Troubleshooting

### Check if SSH is working
```bash
ssh -v openwrt 'echo test'
```

### Check router authorized_keys
```bash
ssh openwrt 'cat /etc/dropbear/authorized_keys'
```

### Manually scan for Tuya devices on 192.168.1.x
```bash
ssh openwrt 'for i in $(seq 1 254); do (timeout 0.2 nc -z -w 1 "192.168.1.$i" 6668 && echo "192.168.1.$i") & done; wait'
```

### Check monitoring logs
```bash
tail -f /home/pi/electricity_tuya_local.log
tail -f /home/pi/electricity_kasa.log
```

## Static Route to 192.168.1.0/24

The Pi needs a static route to reach devices on the 192.168.1.x subnet via the OpenWrt router.

### Route Management Script
`~/scripts/update_openwrt_route.sh` - Automatically updates the route when OpenWrt's IP changes.

This script runs every 10 minutes via cron:
```
*/10 * * * * /home/pi/scripts/update_openwrt_route.sh >/dev/null 2>&1
```

### Manual Route Management
```bash
# Check current route
ip route show | grep 192.168.1

# Run update script manually
sudo /home/pi/scripts/update_openwrt_route.sh

# Test connectivity
ping -c 2 192.168.1.1
```

### How It Works
1. Script resolves `openwrt.lan` to current IP address
2. Checks if route exists with correct gateway
3. Updates route if gateway changed or route missing
4. Logs all actions to syslog (`grep openwrt_route /var/log/syslog`)

This ensures the Pi can always reach 192.168.1.x devices even when OpenWrt's DHCP IP changes.
