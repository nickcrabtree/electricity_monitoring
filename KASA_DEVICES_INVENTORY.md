# Kasa Devices Inventory

Complete list of Kasa smart plugs discovered on both network subnets.

## Summary

| Subnet | Location | Count | Status |
|--------|----------|-------|--------|
| 192.168.86.0/24 (Local) | Home Network | 1 | ❌ Offline |
| 192.168.1.0/24 (Remote) | OpenWrt Subnet | 2 | ✅ Online |
| **Total** | **Combined** | **3** | **2 Online** |

---

## LOCAL SUBNET (192.168.86.0/24)

### Devices Found

#### 1. Device at 192.168.86.48
- **Status**: ❌ **OFFLINE** (Connection refused)
- **IP**: 192.168.86.48
- **MAC**: Unknown (not responding)
- **Model**: Unknown
- **Name**: Unknown (device not reachable)
- **Notes**: Device is not responding to queries. May be powered off or disconnected.

---

## REMOTE SUBNET (192.168.1.0/24) - Via OpenWrt

### Devices Found: 2 Kasa Smart Plugs

#### 1. KP115 (3-Outlet Smart Plug)
- **Status**: ✅ **ONLINE**
- **IP**: 192.168.1.230
- **MAC**: `6c:5a:b0:2e:50:ee`
- **Model**: KP115 (3-outlet)
- **Name in DHCP**: KP115
- **Metrics Path**: `home.electricity.kasa.kp115.*`
- **Available Metrics**:
  - power_watts (current power draw)
  - voltage_volts
  - current_amps
  - is_on (on/off state)

#### 2. KP303 (6-Outlet Smart Plug)
- **Status**: ✅ **ONLINE**
- **IP**: 192.168.1.134
- **MAC**: `34:60:f9:86:fe:d3`
- **Model**: KP303 (6-outlet)
- **Name in DHCP**: KP303
- **Metrics Path**: `home.electricity.kasa.kp303.*`
- **Available Metrics**:
  - power_watts (current power draw)
  - voltage_volts
  - current_amps
  - is_on (on/off state)

---

## Other Devices on 192.168.1.0/24 (Non-Kasa)

### Smart Home Devices
| IP | MAC | Hostname | Type |
|----|-----|----------|------|
| 192.168.1.213 | 5c:47:5e:0d:a9:12 | RingDoorbell-12 | Ring Doorbell |
| 192.168.1.197 | ec:e5:12:1c:68:f6 | tado | Tado Thermostat |
| 192.168.1.177 | 38:b4:d3:b4:ef:6b | neff-oven-383050427038005774 | Neff Connected Oven |

### Other Devices
| IP | MAC | Hostname | Type |
|----|-----|----------|------|
| 192.168.1.186 | 2a:e1:43:1a:ff:65 | * | Unknown |
| 192.168.1.242 | 90:48:6c:92:45:a3 | * | Unknown |
| 192.168.1.143 | a8:80:55:0a:6f:bb | wlan0 | WiFi Interface |

---

## Metric Naming for Kasa Devices

### Format
```
home.electricity.kasa.{device_name}.{metric_type}
```

### Current Metrics (as discovered)
```
home.electricity.kasa.kp115.power_watts
home.electricity.kasa.kp115.voltage_volts
home.electricity.kasa.kp115.current_amps
home.electricity.kasa.kp115.is_on

home.electricity.kasa.kp303.power_watts
home.electricity.kasa.kp303.voltage_volts
home.electricity.kasa.kp303.current_amps
home.electricity.kasa.kp303.is_on
```

### Recommended Friendly Names (Set in Kasa App)

To make metrics more readable, rename devices in the Kasa app:

**KP115 Suggestions:**
- "Kitchen Outlets"
- "Office Power"
- "Utility Room"

**KP303 Suggestions:**
- "Living Room Power"
- "Entertainment Center"
- "Workshop Outlets"

After renaming in app, metrics will update to:
```
home.electricity.kasa.kitchen_outlets.power_watts
home.electricity.kasa.living_room_power.power_watts
```

---

## How Devices Were Discovered

### Local Subnet (192.168.86.0/24)
- **Method**: Direct Kasa discovery using `Discover.discover()`
- **Result**: 1 device found but offline
- **Timeout**: 15 seconds

### Remote Subnet (192.168.1.0/24)
- **Method**: SSH to OpenWrt router, query DHCP leases
- **Command**: `cat /var/dhcp.leases`
- **Result**: 2 Kasa devices (KP115, KP303) plus other smart home devices
- **Connection**: SSH via `openwrt` alias (passwordless)

---

## Current Monitoring Status

### Local Device (192.168.86.48)
- ❌ Not currently monitoring
- Reason: Device is offline/unreachable
- Action: Power on device or verify connectivity

### Remote Devices (192.168.1.0/24)
- ⏳ Ready to monitor (SSH tunneling implementation in progress)
- KP115: Waiting for tunnel implementation
- KP303: Waiting for tunnel implementation
- Status: Device discovery working, tunnel creation needs debugging

---

## Next Steps

### For Local Device (192.168.86.48)
1. Verify device is powered on
2. Check if it's responding to network
3. Power cycle if needed
4. Run discovery again once online

### For Remote Devices (KP115, KP303)
1. Fix SSH tunnel Device instantiation (currently fails)
2. Test tunnel creation and polling
3. Verify metrics arrive in Graphite
4. Set friendly names in Kasa app
5. Configure Grafana dashboards

### Recommended Actions
- [ ] Power on device at 192.168.86.48
- [ ] Debug SSH tunnel Device creation
- [ ] Set friendly names in Kasa app for KP115 and KP303
- [ ] Test metric collection from remote devices
- [ ] Create Grafana dashboard

---

## Testing Commands

### List all Kasa-like devices on remote subnet
```bash
ssh openwrt 'cat /var/dhcp.leases | grep -i "kp\|kasa"'
```

### Check if KP115 is reachable
```bash
ssh openwrt 'ping -c 1 192.168.1.230'
```

### Check if KP303 is reachable
```bash
ssh openwrt 'ping -c 1 192.168.1.134'
```

### Query Graphite for existing Kasa metrics
```bash
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.kasa.*'
```

---

## Files Referenced

- `kasa_to_graphite.py` - Main monitoring script
- `ssh_tunnel_manager.py` - Remote device access
- `config.py` - Configuration settings
- `METRIC_NAMING_VERIFICATION.md` - Metric name format
- `PASSWORDLESS_SSH_SETUP.md` - SSH configuration

---

## Notes

- All remote devices use SSH tunneling through OpenWrt router
- Device names in metrics come from DHCP hostname or Kasa app name
- Kasa devices support power monitoring (has_emeter = true expected)
- Consider renaming devices in Kasa app for cleaner metric names
