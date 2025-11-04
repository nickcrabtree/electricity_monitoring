# SSH Tunnel Auto-Discovery Guide

Automatically discover and monitor Kasa devices on remote networks (e.g., 192.168.1.0/24) through SSH tunneling to OpenWrt router.

## How It Works

1. **SSH Connection** - Securely connects to OpenWrt router via SSH
2. **Remote Discovery** - Queries router's DHCP leases for Kasa devices on 192.168.1.0
3. **Port Forwarding** - Creates SSH tunnels for each device (9999 → localhost:990X)
4. **Automatic Polling** - Polls devices through tunnels, transparent to main script
5. **Metrics Export** - Sends power metrics to Graphite

```
┌─────────────────────────┐
│  Your Machine           │
│  192.168.86.7           │
│                         │
│  kasa_to_graphite.py ◄──┼──► Graphite
│       ▲                 │    192.168.86.123:2003
│       │                 │
│  SSH Tunnels:           │
│  127.0.0.1:9900 ◄──────┼──┐
│  127.0.0.1:9901 ◄──────┼──┤
│                         │  │
└─────────────────────────┘  │ SSH
                             │ Port Forward
                             │
                    ┌────────┼─────────┐
                    │        │         │
                    │  OpenWrt Router  │
                    │  192.168.86.1    │
                    │                  │
                    │ [SSH Server]     │
                    │                  │
                    │ DHCP Leases:     │
                    │  192.168.1.50    │
                    │  192.168.1.51    │
                    │  192.168.1.52    │
                    │                  │
                    │ Remote Network   │
                    │  192.168.1.0/24  │
                    │  │  ┌─────────┐  │
                    │  ├──┤ Kasa #1 │  │
                    │  │  └─────────┘  │
                    │  │  ┌─────────┐  │
                    │  ├──┤ Kasa #2 │  │
                    │  │  └─────────┘  │
                    │  │  ┌─────────┐  │
                    │  └──┤ Kasa #3 │  │
                    │     └─────────┘  │
                    └─────────────────┘
```

## Setup

### 1. Enable SSH Tunnel Support in config.py

```python
# SSH Tunnel Configuration (for cross-subnet device discovery)
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@192.168.86.1'  # SSH connection
SSH_IDENTITY_FILE = None  # Path to SSH key (None = use default)
SSH_TUNNEL_SUBNET = '192.168.1.0/24'  # Remote subnet to scan
```

### 2. Verify SSH Access to OpenWrt

From your machine, test SSH access:

```bash
ssh root@192.168.86.1 'cat /var/dhcp.leases'
```

If prompted for password, it works. If it says "Connection refused", SSH server isn't running on router.

To enable SSH on OpenWrt:
1. Log into http://192.168.86.1
2. Go to **System → Administration**
3. Enable **SSH Server**
4. Set SSH port (default: 22)
5. Save & Apply

### 3. Optional: Configure Passwordless SSH (Recommended)

For fully automatic operation without password prompts:

```bash
# Generate SSH key (if not already done)
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""

# Copy key to OpenWrt
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.86.1

# Test passwordless login
ssh root@192.168.86.1 'echo OK'
# Should print: OK
```

Then update config.py:

```python
SSH_IDENTITY_FILE = '/home/nickc/.ssh/id_ed25519'  # Path to your key
```

## How Auto-Discovery Works

### Process

1. **Initialization**
   - Reads SSH config from `config.py`
   - Tests SSH connection to OpenWrt

2. **Discovery Phase**
   - SSH to router: `cat /var/dhcp.leases`
   - Parse DHCP leases looking for Kasa devices
   - Filter for hostnames containing: kasa, tp-link, smart-plug, tapo

3. **Tunnel Creation**
   - For each discovered device:
     - Find available local port (9900, 9901, ...)
     - Create tunnel: `ssh -L local_port:remote_ip:9999`
     - Map remote IP → local port

4. **Device Polling**
   - Connect to `127.0.0.1:local_port` (tunnel)
   - Poll power metrics through tunnel
   - Tunnel transparently forwards to remote device

5. **Metrics Export**
   - Send metrics to Graphite as usual
   - Works exactly like local devices

### Example Discovery Output

```
2025-11-04 12:34:56,123 - __main__ - INFO - Discovering Kasa devices on network...
2025-11-04 12:34:56,124 - ssh_tunnel_manager - INFO - Discovering devices on remote subnet 192.168.1.0/24 via SSH...
2025-11-04 12:34:56,456 - ssh_tunnel_manager - INFO - Found device: Smart-Plug-Living at 192.168.1.50 (MAC: 84:0D:8E:AA:BB:01)
2025-11-04 12:34:56,457 - ssh_tunnel_manager - INFO - Found device: Smart-Plug-Kitchen at 192.168.1.51 (MAC: 84:0D:8E:AA:BB:02)
2025-11-04 12:34:56,789 - ssh_tunnel_manager - INFO - Creating SSH tunnel to 192.168.1.50:9999 -> localhost:9900
2025-11-04 12:34:57,123 - ssh_tunnel_manager - INFO - Tunnel established: localhost:9900 -> 192.168.1.50:9999
2025-11-04 12:34:57,124 - __main__ - INFO - Added tunneled device Smart-Plug-Living at 192.168.1.50 -> localhost:9900
2025-11-04 12:34:57,456 - __main__ - INFO - Found 2 Kasa device(s)
```

## Testing

### 1. Test SSH Tunnel Setup

```bash
conda activate electricity
cd /home/nickc/code/electricity_monitoring

# Enable debug logging temporarily
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)

from ssh_tunnel_manager import SSHTunnelManager

manager = SSHTunnelManager('root@192.168.86.1')
if manager.test_connection():
    print('✓ SSH connection successful')
    
    # Test remote discovery
    devices = manager.discover_remote_devices('192.168.1.0/24')
    print(f'Found {len(devices)} device(s)')
    for ip, info in devices.items():
        print(f'  {ip}: {info}')
else:
    print('✗ SSH connection failed')
"
```

### 2. Test Full Discovery

```bash
# Run discovery with auto-tunneling
python kasa_to_graphite.py --discover

# Should show both local AND remote devices
```

### 3. Test Single Poll

```bash
# Poll once (will create tunnels, get metrics, send to Graphite)
python kasa_to_graphite.py --once

# Check output for metrics sent
```

### 4. Run Continuously

```bash
# Start monitoring
python kasa_to_graphite.py

# In another terminal, check Graphite for metrics
curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*&from=-5min&format=json' | python3 -m json.tool | head -20
```

## Troubleshooting

### SSH Connection Refused

```bash
# Check if SSH is running on OpenWrt
ssh root@192.168.86.1 'ps aux | grep sshd'

# If not found, enable SSH on router:
# OpenWrt Web UI → System → Administration → SSH Server
```

### No Devices Found

```bash
# Check DHCP leases on router directly
ssh root@192.168.86.1 'cat /var/dhcp.leases'

# Look for Kasa device hostnames
# If empty, check if devices are actually on that network
```

### Tunnel Creation Fails

```bash
# Check if Kasa device is reachable on its subnet
ssh root@192.168.86.1 'ping 192.168.1.50 -c 1'

# Check if device has Kasa port open
ssh root@192.168.86.1 'nc -zv 192.168.1.50 9999'

# Try manual tunnel
ssh -L 9999:192.168.1.50:9999 root@192.168.86.1 -N -f
# Then test: nc -zv 127.0.0.1 9999
```

### Credentials Issues

If passwordless SSH isn't set up:

```bash
# You'll be prompted for password each discovery cycle
# To make it truly automatic, set up SSH key:

ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519_openwrt
ssh-copy-id -i ~/.ssh/id_ed25519_openwrt.pub root@192.168.86.1

# Then in config.py:
SSH_IDENTITY_FILE = '/home/nickc/.ssh/id_ed25519_openwrt'
```

## Configuration Examples

### Minimal (with password prompt)

```python
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@192.168.86.1'
# SSH_IDENTITY_FILE = None  # Will prompt for password
SSH_TUNNEL_SUBNET = '192.168.1.0/24'
```

### Recommended (passwordless)

```python
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@192.168.86.1'
SSH_IDENTITY_FILE = '/home/nickc/.ssh/id_ed25519'
SSH_TUNNEL_SUBNET = '192.168.1.0/24'
```

### Multiple Subnets (if using VLANs)

```python
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'root@192.168.86.1'
SSH_IDENTITY_FILE = '/home/nickc/.ssh/id_ed25519'
SSH_TUNNEL_SUBNET = '192.168.1.0/24'  # Can only specify one

# To support multiple subnets, edit ssh_tunnel_manager.py
# and call discover_remote_devices() for each subnet
```

## How Tunnels Work

### SSH Port Forwarding

```bash
# This command creates the tunnel:
ssh -L 9900:192.168.1.50:9999 root@192.168.86.1 -N -f

# Breaking it down:
# -L local_port:remote_ip:remote_port
# 9900               = port on YOUR machine
# 192.168.1.50       = target device (on remote network)
# 9999               = Kasa port on target device
# root@192.168.86.1  = SSH to OpenWrt router
# -N                 = no remote command
# -f                 = background
```

### Transparent Usage

Once tunnel is created:
- Connect to `127.0.0.1:9900` on your machine
- SSH router forwards connection to `192.168.1.50:9999`
- Completely transparent to client
- Data is encrypted through SSH tunnel

## Performance Notes

- **First discovery**: ~2-3 seconds (SSH queries, tunnel creation)
- **Subsequent polling**: ~2-5 seconds per device (same as local)
- **Overhead**: SSH encryption adds ~10-20% CPU compared to direct connection
- **Bandwidth**: Minimal - only Kasa protocol data sent

## Cleanup

Tunnels are automatically cleaned up when:
- Script exits
- New discovery cycle starts
- Device is no longer found on network

Manual cleanup:

```bash
# Kill SSH tunnel processes
pkill -f 'ssh.*-L.*9'

# Or more specifically:
ps aux | grep 'ssh.*-L' | grep -v grep
```

## Limitations

1. **One subnet at a time** - SSH_TUNNEL_SUBNET only supports one subnet
   - Workaround: Call `discover_remote_devices()` multiple times for multiple subnets

2. **Tunnels are per-discovery cycle** - Recreated each poll
   - Workaround: Could be optimized to keep persistent tunnels

3. **Port range limited** - Uses ports 9900-9999 (max 100 devices)
   - Workaround: Change port range in code if needed

4. **SSH password in scripts** - Can't be automated with password
   - Solution: Use SSH keys (recommended anyway)

## Next Steps

1. ✅ Enable SSH on OpenWrt router
2. ✅ Set up passwordless SSH (optional but recommended)
3. ✅ Update `config.py` with SSH settings
4. ✅ Test: `python kasa_to_graphite.py --discover`
5. ✅ Run continuously: `python kasa_to_graphite.py`
6. ✅ Set up as systemd service or cron

See README.md for running as a service.
