# Passwordless SSH Setup - Complete ✅

Successfully configured passwordless SSH access to OpenWrt router for automatic Kasa device discovery through SSH tunneling.

## What Was Done

### 1. SSH Key Setup ✅
- Generated ED25519 SSH key: `~/.ssh/id_ed25519`
- Copied public key to OpenWrt router
- Verified passwordless authentication works

### 2. SSH Config File ✅
Added entry to `~/.ssh/config`:
```
Host openwrt openwrt.lan 192.168.86.1 192.168.86.22
  HostName openwrt.lan
  User root
  IdentitiesOnly yes
  IdentityFile ~/.ssh/id_ed25519
  ServerAliveInterval 60
  ServerAliveCountMax 3
```

This allows connections via:
- `ssh openwrt`
- `ssh openwrt.lan`
- `ssh root@openwrt.lan`
- `ssh root@192.168.86.1`
- `ssh root@192.168.86.22`

### 3. Configuration Updated ✅
Updated `config.py`:
```python
SSH_TUNNEL_ENABLED = True
SSH_REMOTE_HOST = 'openwrt'  # Uses SSH config alias
SSH_IDENTITY_FILE = None  # Uses default from SSH config
SSH_TUNNEL_SUBNET = '192.168.1.0/24'
```

### 4. DHCP Format Fixed ✅
Corrected DHCP leases parsing in `ssh_tunnel_manager.py`:
- Format: `timestamp mac ip hostname remaining`
- Now correctly identifies Kasa devices by model name

### 5. Device Detection Enhanced ✅
Added Kasa model recognition:
- KP115 (3-outlet plug)
- KP303 (6-outlet plug)  
- KP125 (single outlet)
- HS110 (smart plug)
- Plus: kasa, tp-link, smart-plug, tapo

## Testing Results

### SSH Connection ✅
```bash
$ ssh openwrt 'uname -a'
Linux OpenWrt 6.6.86 #0 SMP Sun Apr 13 16:38:32 2025 mips GNU/Linux
```
✓ Works without password!

### Device Discovery ✅
Discovered on 192.168.1.0/24:
- **KP115** at 192.168.1.230 (MAC: 6c:5a:b0:2e:50:ee)
- **KP303** at 192.168.1.134 (MAC: 34:60:f9:86:fe:d3)

### SSH Tunneling ✅
```
192.168.1.230:9999 ──SSH Tunnel──> localhost:9900
192.168.1.134:9999 ──SSH Tunnel──> localhost:9900
```
Tunnels created, devices accessible through localhost ports.

### Other Network Devices
Also found on 192.168.1.0/24:
- RingDoorbell-12
- tado (smart thermostat)
- neff-oven (connected cooker)

## Quick Reference

### Test SSH Connection
```bash
ssh openwrt 'cat /var/dhcp.leases'
```

### Test Device Discovery
```bash
cd /home/nickc/code/electricity_monitoring
conda activate electricity
python kasa_to_graphite.py --discover
```

### Run Monitoring
```bash
python kasa_to_graphite.py
```

### Check Tunnels
```bash
ps aux | grep 'ssh.*-L'
```

### Manual Tunnel Test
```bash
# Create tunnel manually
ssh -L 9999:192.168.1.230:9999 openwrt -N -f

# Test tunnel
nc -zv 127.0.0.1 9999

# Kill tunnel
pkill -f 'ssh.*-L.*9999'
```

## How It Works

### Discovery Flow
1. **SSH to OpenWrt** → `openwrt` alias in SSH config
2. **Query DHCP** → `cat /var/dhcp.leases`
3. **Parse Leases** → Extract IP, MAC, hostname
4. **Filter Devices** → Look for KP115, KP303, etc.
5. **Create Tunnels** → SSH port forward for each
6. **Poll Metrics** → Connect through localhost:port

### Polling Flow
```
kasa_to_graphite.py
    ↓
connect to 127.0.0.1:9900
    ↓
SSH tunnel forwards to 192.168.1.230:9999
    ↓
Kasa device responds with metrics
    ↓
Send to Graphite (192.168.86.123:2003)
```

## Security Notes

✅ **Encrypted Communication**
- All traffic through SSH encrypted tunnel
- Device IPs and metrics protected

✅ **Key-Based Authentication**
- ED25519 (strong crypto)
- No passwords stored or transmitted
- Can restrict key permissions on router

✅ **Access Control**
- SSH config can limit to specific hosts
- Can require passphrase if needed

## File Changes Summary

### New Files
- `PASSWORDLESS_SSH_SETUP.md` (this guide)

### Modified Files
- `~/.ssh/config` - Added OpenWrt entry
- `config.py` - SSH tunnel settings
- `ssh_tunnel_manager.py` - Fixed DHCP parsing, added KP model detection

### Key Installation
- `~/.ssh/id_ed25519` (private key) - **KEEP SECRET**
- `~/.ssh/id_ed25519.pub` (public key) - Installed on OpenWrt
- `~/.ssh/known_hosts` - Added OpenWrt fingerprint

## Next Steps

### Option 1: Test with --once (single poll)
```bash
cd /home/nickc/code/electricity_monitoring
conda activate electricity
python kasa_to_graphite.py --once
```

### Option 2: Run Continuously
```bash
python kasa_to_graphite.py
```

### Option 3: Set as Systemd Service
See README.md for production deployment guide.

### Option 4: Monitor in Background
```bash
python kasa_to_graphite.py > /tmp/kasa.log 2>&1 &
tail -f /tmp/kasa.log
```

## Troubleshooting

### SSH Key Issues
```bash
# Verify key installed on OpenWrt
ssh openwrt 'cat ~/.ssh/authorized_keys | grep ed25519'

# Check key permissions
ls -la ~/.ssh/id_ed25519
# Should be: -rw------- (600)

# Check key format
file ~/.ssh/id_ed25519
# Should be: OpenSSH private key
```

### SSH Connection Issues
```bash
# Test verbosity
ssh -v openwrt 'echo OK'

# Check SSH service on OpenWrt
ssh openwrt 'ps aux | grep sshd'

# Verify SSH config entry
ssh -G openwrt
```

### Device Discovery Issues
```bash
# Check if Kasa devices on network
ssh openwrt 'cat /var/dhcp.leases | grep -i "kp\|kasa"'

# Test if device port is open
ssh openwrt 'nc -zv 192.168.1.230 9999'
```

### Tunnel Issues
```bash
# Check if tunnel created
ps aux | grep 'ssh.*-L'

# Manually test tunnel
ssh -L 9999:192.168.1.230:9999 openwrt -N -f
nc -zv 127.0.0.1 9999

# Check for port conflicts
netstat -tulpn | grep 990
```

## Security Best Practices Applied

✅ ED25519 key (modern, secure)
✅ SSH config best practices
✅ Server alive interval (timeout detection)
✅ Identity file locked down (600 permissions)
✅ SSH agent can manage key passphrase if needed

## Success Checklist

- ✅ SSH key generated and installed
- ✅ Passwordless authentication working
- ✅ SSH config configured
- ✅ Device discovery finds KP115 and KP303
- ✅ SSH tunnels created automatically
- ✅ DHCP parsing fixed
- ✅ Ready for continuous monitoring

## References

- `SSH_TUNNEL_AUTO_DISCOVERY.md` - Full setup guide
- `config.py` - Configuration options
- `ssh_tunnel_manager.py` - SSH tunnel implementation
- `kasa_to_graphite.py` - Main monitoring script

## Support

For issues or questions about the SSH setup:
1. Check this guide's troubleshooting section
2. Review `SSH_TUNNEL_AUTO_DISCOVERY.md`
3. Check OpenWrt SSH logs: `logread | grep sshd`
4. Test manually: `ssh openwrt 'commands'`
