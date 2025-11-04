# Metric Naming Verification ✅

## Confirmed: Friendly Device Names Used

Metrics sent to Graphite use **friendly device names** set in the Kasa app, NOT IP addresses or MAC addresses.

## Metric Path Format

```
home.electricity.kasa.{friendly_device_name}.{metric_type}
```

## Code Verification

### Discovery Phase
**File:** `kasa_to_graphite.py` (Line 289)
```python
device_name = format_device_name(device.alias)
```

- `device.alias` = Friendly name from Kasa device (e.g., "Kitchen Plug", "Living Room Light")
- `format_device_name()` converts to lowercase and sanitizes (e.g., "kitchen_plug")

### Metric Collection
**File:** `kasa_to_graphite.py` (Lines 307-318)
```python
base_metric = f"{config.METRIC_PREFIX}.kasa.{device_name}"

metrics = [
    (f"{base_metric}.power_watts", power_value),
    (f"{base_metric}.voltage_volts", voltage_value),
    (f"{base_metric}.current_amps", current_value),
    (f"{base_metric}.is_on", on_off_value),
]
```

### Format Device Name Logic
**File:** `graphite_helper.py` (Lines 96-118)
```python
def format_device_name(name: str) -> str:
    """Format device name for use in metric path"""
    name = name.lower()  # Kitchen Plug → kitchen plug
    name = name.replace(' ', '_').replace('-', '_')  # kitchen plug → kitchen_plug
    name = ''.join(c for c in name if c.isalnum() or c == '_')  # remove special chars
    while '__' in name:
        name = name.replace('__', '_')  # remove consecutive underscores
    name = name.strip('_')  # remove leading/trailing underscores
    return name
```

## Example Metrics

### Device: "Living Room Lamp"
```
home.electricity.kasa.living_room_lamp.power_watts
home.electricity.kasa.living_room_lamp.voltage_volts
home.electricity.kasa.living_room_lamp.current_amps
home.electricity.kasa.living_room_lamp.is_on
```

### Device: "Kitchen-Plug-2"
```
home.electricity.kasa.kitchen_plug_2.power_watts
home.electricity.kasa.kitchen_plug_2.voltage_volts
home.electricity.kasa.kitchen_plug_2.current_amps
home.electricity.kasa.kitchen_plug_2.is_on
```

### Device: "OfficeDevice#1"
```
home.electricity.kasa.officedevice1.power_watts
home.electricity.kasa.officedevice1.voltage_volts
home.electricity.kasa.officedevice1.current_amps
home.electricity.kasa.officedevice1.is_on
```

## What's NOT Used

❌ **NOT IP addresses** - e.g., NOT `192.168.86.50` or `127.0.0.1:9900`
❌ **NOT MAC addresses** - e.g., NOT `6c:5a:b0:2e:50:ee`
❌ **NOT device IDs** - e.g., NOT `110100XXXX`

## How to Set Friendly Names

1. Open **Kasa app** on your phone
2. Tap the device
3. Go to **Settings** (gear icon)
4. Set **Device Name** to your preferred friendly name
   - Examples: "Kitchen", "Bedroom", "Living Room Lamp", "Office Desk"
5. Save

## Graphite Query Examples

Once metrics are flowing, query them in Grafana:

### Get all Kasa metrics
```
home.electricity.kasa.*
```

### Get power from all Kasa devices
```
home.electricity.kasa.*.power_watts
```

### Get specific device
```
home.electricity.kasa.living_room_lamp.*
```

### Get specific metric type
```
home.electricity.kasa.*.voltage_volts
```

## Testing

To verify metrics are using friendly names:

```bash
# Query Graphite for all electricity metrics
curl -s 'http://192.168.86.123/metrics/find?query=home.electricity.kasa.*'

# Example output (if device named "Kitchen Plug"):
# home.electricity.kasa.kitchen_plug.power_watts
# home.electricity.kasa.kitchen_plug.voltage_volts
# home.electricity.kasa.kitchen_plug.current_amps
# home.electricity.kasa.kitchen_plug.is_on
```

## Grafana Dashboard Setup

**Example dashboard query:**
```
select metric from /^home.electricity.kasa.living_room_lamp.power_watts$/
```

Will show metrics as:
- `home.electricity.kasa.living_room_lamp.power_watts`

**Legend formatting (Grafana):**
```
{{device}} - {{metric}}
```

Result: `living_room_lamp - power_watts`

## Summary

✅ **Confirmed Implementation:**
- Device names from Kasa app (`device.alias`) used directly
- Formatted to lowercase and sanitized for metric paths
- NO IPs, MACs, or device IDs in metric names
- Human-friendly and easy to read in Grafana
- Survives IP/MAC changes (uses device alias which doesn't change)

## Cross-Subnet Devices (KP115, KP303)

When SSH tunneling is fully working, devices on 192.168.1.0/24 will also use friendly names:

```
home.electricity.kasa.{friendly_name_from_kasa_app}.power_watts
```

Example from your network:
- If you name the KP115 "Kitchen Outlets" → `home.electricity.kasa.kitchen_outlets.power_watts`
- If you name the KP303 "Office Power" → `home.electricity.kasa.office_power.power_watts`

## References

- `graphite_helper.py` - Metric name formatting
- `kasa_to_graphite.py` - Device metric collection (line 289)
- `config.py` - Metric prefix: `home.electricity`
