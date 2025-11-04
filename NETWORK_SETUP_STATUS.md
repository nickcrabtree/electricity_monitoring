# Network Setup Status

## Current Situation

### Local Network (192.168.86.0/24)
- ✅ Your machine is connected: 192.168.86.7
- ✅ Router is reachable: 192.168.86.1
- ⚠️ Kasa device found: 192.168.86.48 (currently offline/unreachable - connection reset)

### Cross-Subnet Network (192.168.1.0/24)
- ❌ **Not yet accessible** - No route to this network
- ❌ No devices found on port 9999 (Kasa port)
- ❌ Cannot ping 192.168.1.1

## What Needs to Be Done

### On Your OpenWrt Router (192.168.86.1)

To enable cross-subnet routing from 192.168.86.0 to 192.168.1.0, you need to:

1. **SSH into the router:**
   ```bash
   ssh root@192.168.86.1
   ```

2. **Add static route (if not already done):**
   ```bash
   # This tells the router to forward traffic to 192.168.1.0 through its interface
   # Check your router's LAN interface name (usually br-lan or similar)
   route add -net 192.168.1.0/24 dev br-lan
   ```

3. **Or via OpenWrt Web UI:**
   - Go to Network → Static Routes
   - Add a route:
     - Destination: 192.168.1.0/24
     - Gateway: 192.168.1.1 (or your LAN interface)
     - Metric: 10

4. **Verify routing is working:**
   ```bash
   # From your machine
   ping 192.168.1.1
   # Should succeed
   ```

### On Your Machine (if router doesn't forward)

If the router routing doesn't work, you can add a static route manually:

```bash
# Add route to 192.168.1.0/24 via the router
sudo ip route add 192.168.1.0/24 via 192.168.86.1

# Verify it works
ping 192.168.1.1

# Make persistent (add to /etc/netplan or equivalent)
```

## Testing Once Routing is Enabled

```bash
# 1. Ping the OpenWrt 192.168.1.0 interface
ping 192.168.1.1

# 2. Find Kasa devices on that subnet
nmap -p 9999 192.168.1.0/24

# 3. Update config.py with device IPs (if found)
# Edit KASA_DEVICES with actual IPs

# 4. Test discovery
cd /home/nickc/code/electricity_monitoring
conda activate electricity
python kasa_to_graphite.py --discover

# 5. Test single poll
python kasa_to_graphite.py --once

# 6. Verify metrics in Graphite
curl -s 'http://192.168.86.123/render?target=home.electricity.kasa.*&from=-5min&format=json'
```

## Current Local Device

### 192.168.86.48 (Kasa plug)
- Currently **offline** or not responding
- Once powered on/reachable, it should be monitored automatically
- Test when device is online: `python kasa_to_graphite.py --once`

## Next Steps

1. **Check OpenWrt router status:**
   - Verify 192.168.1.0/24 devices exist and have IPs
   - Check routing table on router: `ip route` on the router

2. **Configure routing:**
   - Either on router (recommended) or on your machine
   - Test with `ping 192.168.1.1`

3. **Scan for devices:**
   - Once routing works: `nmap -p 9999 192.168.1.0/24`
   - Note the IP addresses of Kasa devices

4. **Update configuration:**
   - Add discovered devices to `config.py` KASA_DEVICES
   - Or leave KASA_DISCOVERY_NETWORKS enabled for auto-discovery

5. **Verify data flow:**
   - Run `python kasa_to_graphite.py --once`
   - Check Graphite for incoming metrics

## Troubleshooting Commands

```bash
# Check if 192.168.86.48 is online
ping 192.168.86.48

# Check Graphite connectivity
nc -zv 192.168.86.123 2003

# Test with echo metric
echo "test.kasa.ping 1 $(date +%s)" | nc 192.168.86.123 2003

# Check Graphite for metrics
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.*'

# Monitor script logs
python kasa_to_graphite.py 2>&1 | head -50

# Check if Kasa devices are listening
netstat -tulpn | grep 9999  # On the Kasa device itself
```
