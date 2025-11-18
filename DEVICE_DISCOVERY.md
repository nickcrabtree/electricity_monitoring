# Automatic Device Discovery and Naming

## Overview
The electricity monitoring system now uses **fully automatic device discovery** with **persistent friendly names**. No manual configuration is required.

## How It Works

### Kasa Devices
1. **Auto-discovery**: Scans network using Kasa's UDP broadcast protocol
2. **Stable IDs**: Uses MAC addresses as permanent identifiers
3. **Friendly Names**: Extracts device alias (name set in Kasa app)
4. **Persistence**: Names stored in `device_names.json` keyed by MAC address

**Example:**
```
MAC: b8:27:eb:c9:b4:78
Name: "Living Room Lamp"
Metric: home.electricity.kasa.living_room_lamp.power_watts
```

### Tuya Devices
1. **Auto-discovery**: Scans network using tinytuya's device scan
2. **Stable IDs**: Uses permanent device IDs (not IP addresses)
3. **Friendly Names**: Extracts device name from scan results
4. **Persistence**: Names stored in `device_names.json` keyed by device ID

**Example:**
```
Device ID: bfc2c15f07d2b072d5rchn
Name: "Kettle"
Metric: home.electricity.tuya.kettle.power_watts
```

## Device Names Storage

File: `device_names.json`

```json
{
  "b8:27:eb:c9:b4:78": "Living Room Lamp",
  "bfc2c15f07d2b072d5rchn": "Kettle",
  "another-device-id": "Garage Lights"
}
```

### Features
- **Persistent across reboots**: Names survive restarts
- **Persistent across IP changes**: Keyed by stable IDs, not IPs
- **Automatically updated**: New devices added on first discovery
- **Manual override**: Edit JSON file to change names if desired

## Discovery Frequency

Both systems re-scan every **3 minutes** to quickly detect:
- New devices plugged in
- Devices that come back online
- IP address changes (handled transparently)

## No Manual Configuration Required

### What Was Removed
The following manual configuration sections are **obsolete and removed**:

```python
# OLD - No longer needed!
KASA_DEVICES = {
    '00:1A:2B:3C:4D:5E': 'bedroom_plug',
    '192.168.1.50': 'kitchen_plug',
}

TUYA_DEVICES = {
    'device_id_123': {
        'name': 'kitchen_kettle',
        'ip': '192.168.86.51',
        'local_key': 'key_here',
        'version': '3.3'
    }
}
```

### Current Config
```python
# NEW - Fully automatic!
# Device configurations are now fully automatic via discovery
# Device names are persisted in device_names.json
```

## Network Discovery

### Local Network (192.168.86.0/24)
- **Kasa**: UDP broadcast discovery
- **Tuya**: tinytuya.deviceScan()

### Remote Network (192.168.1.0/24)
- **Route**: Static route via OpenWrt router (`openwrt.lan`)
- **Auto-updated**: Route refreshed every 10 minutes via cron
- **Tuya**: Remote scanning via SSH to router
- **Kasa**: Can discover via UDP tunneling (optional)

## Metric Naming Convention

All metrics use friendly names:

```
home.electricity.kasa.<friendly_name>.<metric>
home.electricity.tuya.<friendly_name>.<metric>
```

Friendly names are sanitized:
- Lowercase
- Spaces replaced with underscores
- Special characters removed

**Examples:**
- "Living Room Lamp" → `living_room_lamp`
- "Kettle" → `kettle`
- "Garage Lights" → `garage_lights`

## Changing Device Names

### Option 1: Rename in Device App
1. Change name in Kasa/Tuya app
2. Wait for next scan (within 3 minutes)
3. New name automatically detected and saved

### Option 2: Edit JSON Directly
1. Stop monitoring scripts
2. Edit `device_names.json`
3. Change the name value for the device ID
4. Restart monitoring scripts

**Example:**
```json
{
  "bfc2c15f07d2b072d5rchn": "Kitchen Kettle"
}
```

## Troubleshooting

### Check discovered devices
```bash
# Kasa
python3 kasa_to_graphite.py --discover

# Tuya  
python3 tuya_local_to_graphite.py --discover
```

### Check device names
```bash
cat device_names.json | python3 -m json.tool
```

### Check monitoring logs
```bash
tail -f /home/pi/electricity_kasa.log
tail -f /home/pi/electricity_tuya_local.log
```

### Force re-discovery
```bash
# Kill processes (watchdog will restart them)
pkill -f kasa_to_graphite.py
pkill -f tuya_local_to_graphite.py

# Or manually restart
/home/pi/code/electricity_monitoring/watchdog_electricity.sh
```

## Benefits of Automatic Discovery

✅ **Zero configuration** - Just plug in devices and they're discovered  
✅ **Survives IP changes** - Uses stable IDs, not IP addresses  
✅ **Friendly metrics** - Uses actual device names in Graphite  
✅ **Fast detection** - New devices found within 3 minutes  
✅ **Persistent names** - Names survive restarts and IP changes  
✅ **Simple maintenance** - No config files to edit  

## Cross-Subnet Discovery

### Static Route Setup
A static route is automatically maintained to reach devices on 192.168.1.0/24:

```bash
# Route updated every 10 minutes via cron
*/10 * * * * /home/pi/scripts/update_openwrt_route.sh
```

The route dynamically adapts when OpenWrt's DHCP IP changes.

### Current Network Layout
- **192.168.86.0/24**: Main network (Pi, most devices)
- **192.168.1.0/24**: Secondary network via OpenWrt br-lan
- **Router**: `openwrt.lan` (dynamic hostname)

## Future Enhancements

Potential improvements:
- Web UI to rename devices
- Alert on new device discovery
- Device grouping/tagging
- Historical device tracking

## New plug added but not visible in Grafana

If you add a new Tuya smart plug (e.g. **Freezer**, **Office shelves**) and it does not appear on the electricity Grafana dashboard, follow this checklist:

1. **Confirm it is being discovered**
   ```bash
   python3 tuya_local_to_graphite.py --discover
   ```
   - The device should show up with a non-empty `IP` and a reasonable `Version` (usually `3.3`).

2. **Check the Tuya collector log for timeouts**
   ```bash
   strings /home/pi/electricity_tuya_local.log | grep '<DEVICE_ID_OR_NAME>' | tail -n 20
   ```
   - Repeated lines like `timeout (1/3)` / `failed after 3 timeout attempts` mean the Pi cannot successfully poll the plug.
   - For example, at the time of writing, `Freezer` (ID `bf6d71e3b1942c0414ud0a`) and `Office shelves` (ID `10105863c4dd57078c2e`) were discovered but consistently timed out.

3. **Typical causes of timeouts**
   - **No local IP**: the device shows `ip": ""` in `snapshot.json` (cloud-only state) – local LAN discovery hasnt found it yet.
   - **Wrong local key**: the device was re-paired or moved between Tuya accounts, so the stored key is stale.
   - **Network reachability**: the plug is on another subnet or isolated Wi-Fi and is not reachable from the Pi.

4. **Fixes**
   - Ensure the plug is on the correct Wi-Fi and reachable from the Pi (ping its IP).
   - Re-run the tinytuya setup to refresh local keys and metadata:
     ```bash
     python3 -m tinytuya wizard
     ```
     Verify that the affected device IDs (e.g. `bf6d71e3b1942c0414ud0a`, `10105863c4dd57078c2e`) have valid `ip`, `key` and `ver`.
   - After updating keys, either wait for the watchdog to restart the collector or manually restart it:
     ```bash
     pkill -f tuya_local_to_graphite.py
     /home/pi/code/electricity_monitoring/watchdog_electricity.sh
     ```

5. **Verify data in Graphite/Grafana**
   - On the Graphite host, check for new series:
     ```bash
     ssh nickc@192.168.86.123 'find /var/lib/graphite/whisper/home/electricity -maxdepth 4 -type f -name "power_watts.wsp" | sort'
     ```
   - Once `home/electricity/tuya/<friendly_name>/power_watts.wsp` exists and is updating, the plug will automatically appear in the Grafana dashboard panels that use `home.electricity.tuya.*.power_watts`.
